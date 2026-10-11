"""Agent 流式问答服务

一轮问答的执行与推送：加载会话历史 -> 交给智能体流式生成 -> 逐段产出 SSE 帧 ->
把本轮「用户提问 + 模型产生的全部消息（含工具调用链）」落库。

事件名与载荷字段完全对齐前端约定：THINKING{text} / STREAM_TEXT{text} /
TOOL_CALL_REQUEST / TOOL_CALL_RESULT / COMPACTION_* / DONE{answer} / ERROR{message}。
工具调用事件与压缩事件同走 LangGraph 的 custom 流，由中间件在相应时机写入；
落库所需的完整消息取自 messages 流（逐个分片累积还原），保证前端重拉历史时工具卡不丢。

@author Luxon
@date 2026/10/10
"""

import json
import logging
from collections.abc import AsyncIterator

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)

from app.database import SessionLocal
from app.services import agent_memory_service, chat_file_service, chat_message_service
from app.services.compaction import AgentContext
from app.services.file_turn_tracker import turn_tracker
from app.services.llm import get_agent

logger = logging.getLogger(__name__)


def sse_frame(event: str, data: dict) -> str:
    """拼一帧 SSE：事件名 + 单行 JSON 载荷，以空行结束（与前端切块解析约定一致）"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream_events(
    session_id: int, user_message: str, file_ids: list[int] | None = None
) -> AsyncIterator[str]:
    """执行一轮问答并逐帧产出 SSE 文本

    :param session_id: 会话 ID（memoryId）
    :param user_message: 本轮用户提问
    :param file_ids: 本轮随消息上传的文件 ID 列表，可为空
    :return: SSE 帧文本的异步生成器
    """
    # 步骤 1：加载历史、本轮消息边界与本轮上传文件清单（独立短连接读库，不与请求生命周期耦合）
    db = SessionLocal()
    try:
        history, watermark = agent_memory_service.load_history(db, session_id)
        before_max_id = chat_message_service.max_message_id(db, session_id)
        upload_files = chat_file_service.list_vo_by_ids(db, file_ids or [])
    finally:
        db.close()

    # 步骤 1.1：把上传文件清单拼进本轮问题，让模型知道本轮带了哪些文件、文件名与文件 ID
    effective_message = _append_file_notice(user_message, upload_files)

    collected: list[str] = []
    # 本轮要落库的消息：AI 段与工具结果按时间先后排列；AI 分片逐个累积，遇工具结果或流结束时结算
    turn_messages: list[BaseMessage] = []
    pending_ai: AIMessageChunk | None = None
    pending_reasoning: list[str] = []
    # 流式工具调用参数跟踪：模型开始产出工具调用即先推帧建卡，参数生成过程中节流推送参数快照，
    # 让前端在工具尚未返回时就能展开看到正在生成的调用参数
    tool_args = _ToolArgsTracker()

    def flush_ai() -> None:
        """把当前累积的 AI 分片结算成一条完整 AI 消息（含 tool_calls 与思考内容）"""
        nonlocal pending_ai
        if pending_ai is None:
            return
        turn_messages.append(_to_ai_message(pending_ai, "".join(pending_reasoning)))
        pending_ai = None
        pending_reasoning.clear()

    try:
        # 步骤 2：同时订阅 messages（思考/文本/工具消息增量）与 custom（工具调用/压缩过程事件）两种流，
        #         边推送事件边累积落库所需的完整消息；并把会话 ID 与水位线经运行上下文带给中间件
        async for chunk in get_agent().astream(
            {"messages": [*history, HumanMessage(content=effective_message)]},
            stream_mode=["messages", "custom"],
            version="v2",
            context=AgentContext(session_id=session_id, watermark=watermark),
        ):
            chunk_type = chunk.get("type")
            if chunk_type == "messages":
                message, _meta = chunk["data"]
                if isinstance(message, AIMessageChunk):
                    # AI 分片：累积成完整消息；思考与正文分别即时推送
                    pending_ai = message if pending_ai is None else pending_ai + message
                    reasoning = _chunk_reasoning(message)
                    if reasoning:
                        pending_reasoning.append(reasoning)
                        yield sse_frame("THINKING", {"text": reasoning})
                    text = _chunk_text(message)
                    if text:
                        collected.append(text)
                        yield sse_frame("STREAM_TEXT", {"text": text})
                    # 模型开始产出工具调用时就推请求帧并在参数生成过程中推送参数快照：write_file 这类
                    # 大参数生成耗时很长，若等到工具真正执行才推，前端整段生成期既看不到工具卡也看不到参数
                    for tool_frame in tool_args.frames(message):
                        yield tool_frame
                elif isinstance(message, ToolMessage):
                    # 工具结果到达：先结算它前面那条带 tool_calls 的 AI 消息，再收下结果
                    flush_ai()
                    turn_messages.append(message)
                elif isinstance(message, AIMessage):
                    # 非流式模型直接给出完整 AI 消息（少见），同样纳入落库
                    flush_ai()
                    turn_messages.append(message)
                    text = _chunk_text(message)
                    if text:
                        collected.append(text)
                        yield sse_frame("STREAM_TEXT", {"text": text})
            elif chunk_type == "custom":
                data = chunk.get("data")
                # 流式阶段已推送过该调用的参数快照时跳过执行前的重复请求帧，
                # 避免把更完整的流式参数覆盖成截断更短的版本
                if tool_args.already_streamed(data):
                    continue
                frame = _custom_frame(data)
                if frame is not None:
                    yield frame

        # 步骤 2.1：流结束后结算最后一段 AI 消息（收尾文本）
        flush_ai()
        answer = "".join(collected)

        # 步骤 3：本轮问答落库（用户消息存拼入文件清单后的文本，与送给模型的一致；AI 回复连同
        #         思考内容与工具调用链一并保存），随后回填文件占位符锚点，保证前端收到 DONE
        #         后重拉历史/文件列表时工具卡与文件锚点都已就位
        db = SessionLocal()
        try:
            agent_memory_service.save_turn(db, session_id, effective_message, turn_messages)
            _finish_turn(db, session_id, before_max_id, file_ids, completed=True)
        finally:
            db.close()

        # 步骤 4：结束帧携带完整回复
        yield sse_frame("DONE", {"answer": answer})
    except Exception as error:  # noqa: BLE001 —— 兜底把任意异常转成 ERROR 帧，避免连接悬挂
        logger.exception("Agent 流式问答失败：sessionId=%s", session_id)
        # 出错轮次没有落 AI 消息：清空本轮文件登记而不回填锚点，避免卡片错挂到用户消息下
        _drain_touched_files(session_id)
        yield sse_frame("ERROR", {"message": _friendly_error(error)})


def _to_ai_message(chunk: AIMessageChunk, reasoning: str) -> AIMessage:
    """把流式累积的 AI 分片还原成一条可持久化的完整 AI 消息

    分片合并时 additional_kwargs 是「后者覆盖前者」，思考内容会只剩最后一片，
    故思考文本由调用方单独累加后覆盖回去；tool_calls 取合并后的完整调用列表。

    :param chunk: 累积完成的 AI 分片
    :param reasoning: 本段 AI 的完整思考内容（无则为空串）
    :return: 可直接序列化落库的 AI 消息
    """
    # 步骤 1：思考内容放入与 langchain-deepseek 一致的字段位置；无思考则清掉残留字段
    additional = dict(chunk.additional_kwargs or {})
    if reasoning:
        additional["reasoning_content"] = reasoning
    else:
        additional.pop("reasoning_content", None)

    # 步骤 2：只有真正调用过工具时才带上 tool_calls，避免空数组污染历史
    kwargs: dict[str, object] = {"content": chunk.content, "additional_kwargs": additional}
    if chunk.tool_calls:
        kwargs["tool_calls"] = chunk.tool_calls
    return AIMessage(**kwargs)


# 参数快照单次推送的最大字符数：超过后不再增长，避免超大参数（如 write_file 的整页 HTML）刷爆事件流
STREAM_ARG_MAX_CHARS = 4000

# 参数累积到该字符数才再推一帧：节流，避免每个 token 都产出一帧拖慢流
STREAM_ARG_STEP_CHARS = 200


def _request_frame(call_id: str, tool_name: str, arguments: str) -> str:
    """拼一条工具调用请求帧，字段名与前端 eka-api.ts 约定一致"""
    return sse_frame(
        "TOOL_CALL_REQUEST",
        {"callId": call_id, "toolName": tool_name, "arguments": arguments},
    )


class _ToolArgsTracker:
    """跟踪本轮流式工具调用的参数生成过程，产出用于即时展示的请求帧

    模型开始产出某个工具调用时先推一帧（工具名已定、参数为空），前端据此立即建卡并转圈；
    参数随后逐片生成，这里按累积增量节流推送参数快照，让用户在工具返回前就能展开看到参数；
    参数超过上限后不再增长。工具执行前 NotifyingToolMiddleware 还会推一条完整参数的请求帧兜底，
    但若本跟踪器已推送过不短于它的参数快照，则该帧会被跳过，避免把更完整的参数覆盖成截断更短的版本。
    """

    def __init__(self) -> None:
        """初始化各张跟踪表：序号映射、工具名、参数缓冲与已推送长度"""
        # 工具调用序号 -> 调用 ID：流式后续分片不再带 ID，靠序号归位
        self._index_to_call: dict[int, str] = {}
        # 调用 ID -> 工具名：后续分片不带名字，登记首片的名字供推送使用
        self._names: dict[str, str] = {}
        # 调用 ID -> 已累积的参数 JSON 文本
        self._buffers: dict[str, str] = {}
        # 调用 ID -> 上次推送时的参数长度，用于节流判定
        self._sent: dict[str, int] = {}

    def already_streamed(self, data: object) -> bool:
        """判断某条 custom 事件是否已被流式参数快照覆盖

        用于跳过工具执行前的重复请求帧：仅当「已推送给前端的参数长度」不短于该帧参数时才跳过。
        注意必须用已推送长度（_sent）而非累积缓冲长度（_buffers）——参数短于一个节流步长时，
        流式只推过首片（如仅一个「{」），此时累积缓冲虽已是完整参数，前端却只拿到首片，
        若用累积长度判定会把执行前的完整参数帧误跳过，导致前端永远停在残缺参数上。

        :param data: 一条 custom 事件的载荷，可能不是工具请求帧
        :return: True 表示可直接跳过该请求帧
        """
        if not isinstance(data, dict) or data.get("kind") != "TOOL_CALL_REQUEST":
            return False
        call_id = data.get("callId")
        if call_id not in self._sent:
            return False
        return self._sent[call_id] >= len(data.get("arguments") or "")

    def frames(self, chunk: AIMessageChunk) -> list[str]:
        """消费一个 AI 流式分片，返回需要即时推送的请求帧列表

        :param chunk: 当前 AI 流式分片
        :return: 需要推送的 SSE 请求帧列表；没有工具调用增量时为空列表
        """
        frames: list[str] = []
        # 步骤 1：逐个消费分片里的工具调用增量（首片带 ID/名字，后续片只带序号与参数增量）
        for tool_call in chunk.tool_call_chunks or []:
            index = tool_call.get("index")
            call_id = tool_call.get("id")

            # 步骤 2：带 ID 的首片——登记序号与名字，首次出现时先推一帧（参数尚空）让前端立刻建卡
            if call_id:
                if index is not None:
                    self._index_to_call[index] = call_id
                if call_id not in self._buffers:
                    self._buffers[call_id] = ""
                    self._sent[call_id] = 0
                    self._names[call_id] = tool_call.get("name") or ""
                    frames.append(_request_frame(call_id, self._names[call_id], ""))
            else:
                # 步骤 3：不带 ID 的后续分片按序号归位到对应调用，归位失败则跳过
                if index is None:
                    continue
                call_id = self._index_to_call.get(index)
                if not call_id:
                    continue

            # 步骤 4：累积本片带来的参数增量
            delta = tool_call.get("args") or ""
            if delta:
                self._buffers[call_id] += delta

            # 步骤 5：节流推送参数快照——首次拿到非空参数必推一帧，之后每累积一个步长再推，到上限即止
            buffered = self._buffers[call_id]
            grew = len(buffered) - self._sent[call_id]
            can_grow = self._sent[call_id] < STREAM_ARG_MAX_CHARS
            if grew > 0 and can_grow and (self._sent[call_id] == 0 or grew >= STREAM_ARG_STEP_CHARS):
                self._sent[call_id] = len(buffered)
                frames.append(
                    _request_frame(
                        call_id, self._names.get(call_id, ""), buffered[:STREAM_ARG_MAX_CHARS]
                    )
                )
        return frames


def _append_file_notice(message: str, upload_files: list[dict]) -> str:
    """把本轮上传文件清单拼进用户消息：模型据此知道带了哪些文件、文件名与文件 ID

    :param message: 用户原始问题
    :param upload_files: 本轮上传文件视图列表，可为空
    :return: 追加文件清单后的消息；无文件时原样返回
    """
    if not upload_files:
        return message
    names = "、".join(f"[id={file['id']}] {file['fileName']}" for file in upload_files)
    return f"{message}\n\n【本轮上传文件】{names}"


def _finish_turn(
    db, session_id: int, before_max_id: int, upload_file_ids: list[int] | None, completed: bool
) -> None:
    """轮次收尾：回填文件占位符锚点

    上传文件锚到本轮用户消息；本轮生成/修改的文件只在正常完成时才锚到本轮最后一条 AI 消息——
    出错轮次里 AI 消息根本没落库，照样锚定会退化成挂到用户消息下，故此时只清空登记。
    失败不影响本轮结果，仅记日志。

    :param before_max_id: 本轮开始前的最大消息 ID
    :param upload_file_ids: 本轮上传的文件 ID 列表，可为空
    :param completed: 本轮是否正常完成（拿到了完整回答）
    """
    try:
        # 步骤 1：本轮用户消息；无论是否正常结束，上传文件都锚到它
        user_message_id = chat_message_service.first_user_message_id_after(db, session_id, before_max_id)
        chat_file_service.anchor_files(db, upload_file_ids or [], user_message_id)

        # 步骤 2：生成文件仅在正常完成时锚到本轮最后一条 AI 消息
        touched = turn_tracker.drain(session_id)
        if completed and touched:
            chat_file_service.anchor_files(
                db, list(touched), chat_message_service.max_message_id(db, session_id)
            )
    except Exception:  # noqa: BLE001 —— 锚点回填失败不能影响本轮结果
        logger.warning("文件占位符锚点回填失败：sessionId=%s", session_id)


def _drain_touched_files(session_id: int) -> None:
    """清空某会话本轮的文件登记但不回填锚点（出错轮次收尾用）"""
    turn_tracker.drain(session_id)


def _chunk_text(chunk: object) -> str:
    """从消息/分片中提取 AI 文本；非 AI 消息（如工具消息）返回空串"""
    if isinstance(chunk, AIMessage):
        return getattr(chunk, "text", "") or ""
    return ""


def _chunk_reasoning(chunk: object) -> str:
    """从消息/分片中提取模型的思考内容（DeepSeek reasoning_content）；无则返回空串

    langchain-deepseek 会把思考内容放进 additional_kwargs["reasoning_content"]，
    与正文（content）分离，这里单独取出用于推送前端思考展示。
    """
    if isinstance(chunk, AIMessage):
        value = chunk.additional_kwargs.get("reasoning_content")
        return value if isinstance(value, str) else ""
    return ""


def _custom_frame(data: object) -> str | None:
    """把中间件发出的 custom 事件转成前端 SSE 帧（工具调用 + 上下文压缩）

    :param data: custom 流载荷，含 kind 字段标记事件类型
    :return: SSE 帧文本；非本模块处理的事件返回 None
    """
    if not isinstance(data, dict):
        return None
    kind = data.get("kind")
    if kind == "TOOL_CALL_REQUEST":
        return sse_frame(
            "TOOL_CALL_REQUEST",
            {
                "callId": data.get("callId", ""),
                "toolName": data.get("toolName", ""),
                "arguments": data.get("arguments", ""),
            },
        )
    if kind == "TOOL_CALL_RESULT":
        return sse_frame(
            "TOOL_CALL_RESULT",
            {
                "callId": data.get("callId", ""),
                "toolName": data.get("toolName", ""),
                "arguments": data.get("arguments", ""),
                "resultSummary": data.get("resultSummary", ""),
            },
        )
    if kind == "COMPACTION_START":
        return sse_frame("COMPACTION_START", {"message": "正在压缩对话上下文"})
    if kind == "COMPACTION_DONE":
        return sse_frame(
            "COMPACTION_DONE",
            {
                "compactedMessages": data.get("compactedMessages", 0),
                "summaryTokens": data.get("summaryTokens", 0),
            },
        )
    if kind == "COMPACTION_ERROR":
        return sse_frame("COMPACTION_ERROR", {"message": data.get("message", "上下文压缩失败")})
    return None


def _friendly_error(error: Exception) -> str:
    """把异常翻译成面向用户的中文提示，技术细节只落日志"""
    text = str(error)
    lowered = text.lower()
    if "timeout" in lowered or "超时" in text:
        return "当前服务响应较慢，请稍后重试"
    if any(key in text for key in ("Connection", "Socket", "网络")):
        return "网络出现波动，请稍后重试"
    return "服务暂时开小差，请稍后重试"
