"""会话上下文压缩中间件（基于 LangChain 内置 SummarizationMiddleware）

在 LangChain 内置的 SummarizationMiddleware 之上包一层，追加两项能力：

1. 事件通知：压缩真正发生时，把「开始 / 完成 / 失败」通过自定义事件推给上层，
   由 agent_service 转成前端 COMPACTION_* 事件；
2. 结果落库：压缩成功后，把「摘要正文 + 摘要水位线 + 摘要 token 数 + 累计压缩次数」
   CAS 写回 ai_chat_session；下一轮按水位线加载历史（水位线之前已被摘要替代，不再
   重复送入模型），从而避免重复压缩。

压缩算法（阈值判断、cutoff 计算、摘要生成、消息重写）完全复用内置件，本类只新增
上述两项能力，不改变压缩行为；压缩失败时降级为「本轮按原上下文继续」，不中断对话。

@author Luxon
@date 2026/10/10
"""

import logging
from dataclasses import dataclass

from langchain.agents.middleware import SummarizationMiddleware
from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.config import get_stream_writer
from sqlalchemy import update

from app.database import SessionLocal
from app.models.chat_session import ChatSession

logger = logging.getLogger(__name__)

# 摘要消息统一前缀：加载历史与生成摘要共用，标识这条人类消息承载的是历史摘要而非用户提问
SUMMARY_PREFIX = "以下是本次会话此前内容的摘要，供你延续对话参考：\n"

# 摘要提示词：中文输出 + 明确保留/省略清单（与 EKA 一致）；{messages} 为待压缩对话正文占位符
EKA_SUMMARY_PROMPT = """请把以下对话压缩成结构化摘要，用于后续会话延续。用中文输出。
必须保留：用户目标与显式约束（原话）、已确认决策、未完成任务、关键事实、文件名/表名/工具名。
可省略：寒暄、闲聊、已完成的中间推理。

【待压缩的对话内容】
{messages}"""

# LangChain 消息 id 上的数据库行 ID 标记前缀：压缩后据此反推摘要水位线
_DB_ID_PREFIX = "db-"


def db_message_id(row_id: int) -> str:
    """把消息行 ID 编码为 LangChain 消息 id

    :param row_id: ai_chat_message 主键
    :return: 带前缀的字符串 id
    """
    return f"{_DB_ID_PREFIX}{row_id}"


def parse_db_message_id(message_id: str | None) -> int | None:
    """从 LangChain 消息 id 还原消息行 ID

    :param message_id: 消息 id，可能为空或非本模块编码
    :return: 还原后的行 ID；无法还原时返回 None
    """
    if message_id and message_id.startswith(_DB_ID_PREFIX):
        try:
            return int(message_id[len(_DB_ID_PREFIX):])
        except ValueError:
            return None
    return None


@dataclass
class AgentContext:
    """智能体运行上下文：把请求级信息带进中间件，供压缩结果落库使用"""

    # 当前会话 ID（memoryId）
    session_id: int

    # 本轮加载历史时的摘要水位线，作为压缩落库的 CAS 基线
    watermark: int


def _emit(payload: dict) -> None:
    """向 LangGraph custom 流写一条事件；无流式上下文时静默忽略

    :param payload: 事件载荷，至少含 kind 字段
    """
    try:
        get_stream_writer()(payload)
    except Exception:
        # 不在流式执行上下文（如直接单测调用）时忽略，不影响压缩本身
        pass


def _extract_summary(result: dict | None) -> str:
    """从压缩结果里取出摘要正文，剥掉统一前缀"""
    if not result:
        return ""
    for message in result.get("messages", []):
        if (
            isinstance(message, HumanMessage)
            and message.additional_kwargs.get("lc_source") == "summarization"
        ):
            content = message.content if isinstance(message.content, str) else str(message.content)
            return content[len(SUMMARY_PREFIX):] if content.startswith(SUMMARY_PREFIX) else content
    return ""


def _count_db_messages(messages: list[BaseMessage]) -> int:
    """统计一段消息里来源于数据库的行数（摘要等派生消息不计入）"""
    return sum(1 for message in messages if parse_db_message_id(message.id) is not None)


def _max_db_message_id(messages: list[BaseMessage]) -> int:
    """取一段消息里最大的数据库行 ID，作为新的摘要水位线"""
    ids = [parse_db_message_id(message.id) for message in messages]
    valid_ids = [row_id for row_id in ids if row_id is not None]
    return max(valid_ids) if valid_ids else 0


def _short(error: Exception) -> str:
    """取异常的一句话描述，超长截断"""
    text = str(error) or error.__class__.__name__
    return text[:200]


class NotifyingSummarizationMiddleware(SummarizationMiddleware):
    """带「压缩事件通知 + 结果落库」的摘要中间件

    仅在压缩前后追加事件推送与落库；压缩内核完全沿用内置 SummarizationMiddleware。
    """

    @staticmethod
    def _build_new_messages(summary: str) -> list[HumanMessage]:
        """构造承载摘要的人类消息：中文前缀 + 摘要正文，并打上来源标记"""
        return [
            HumanMessage(
                content=SUMMARY_PREFIX + summary,
                additional_kwargs={"lc_source": "summarization"},
            )
        ]

    def before_model(self, state, runtime):
        """同步：模型调用前按需压缩，并推送事件、落库结果"""
        messages = state["messages"]
        cutoff_index = self._preview_cutoff(messages)
        if cutoff_index <= 0:
            return super().before_model(state, runtime)

        _emit({"kind": "COMPACTION_START"})
        try:
            result = super().before_model(state, runtime)
        except Exception as error:
            return self._on_compaction_error(error)
        self._on_compaction_done(runtime, messages, cutoff_index, result)
        return result

    async def abefore_model(self, state, runtime):
        """异步：模型调用前按需压缩，并推送事件、落库结果（流式问答走此分支）"""
        messages = state["messages"]
        cutoff_index = self._preview_cutoff(messages)
        if cutoff_index <= 0:
            return await super().abefore_model(state, runtime)

        _emit({"kind": "COMPACTION_START"})
        try:
            result = await super().abefore_model(state, runtime)
        except Exception as error:
            return self._on_compaction_error(error)
        self._on_compaction_done(runtime, messages, cutoff_index, result)
        return result

    def _preview_cutoff(self, messages: list[BaseMessage]) -> int:
        """预判本轮是否会压缩，返回待压缩区末端下标（0 表示不压缩）

        复用内置件同一套阈值判断与 cutoff 计算，保证「预判」与「实际压缩」一致。
        """
        if not messages:
            return 0
        self._ensure_message_ids(messages)
        total_tokens = self.token_counter(messages)
        if not self._should_summarize(messages, total_tokens):
            return 0
        cutoff_index = self._determine_cutoff_index(messages)
        return cutoff_index if cutoff_index > 0 else 0

    def _on_compaction_error(self, error: Exception) -> None:
        """压缩失败：推错误事件并降级（返回 None 表示不修改 state，本轮按原上下文继续）"""
        logger.warning("会话压缩失败，本轮按原上下文继续：%s", error)
        _emit({"kind": "COMPACTION_ERROR", "message": _short(error)})
        return None

    def _on_compaction_done(
        self, runtime, messages: list[BaseMessage], cutoff_index: int, result: dict | None
    ) -> None:
        """压缩成功：把摘要与水位线落库，并推完成事件"""
        messages_to_summarize = messages[:cutoff_index]
        summary = _extract_summary(result)
        summary_tokens = self.token_counter([HumanMessage(content=summary)]) if summary else 0
        new_watermark = _max_db_message_id(messages_to_summarize)
        if summary and new_watermark > 0:
            self._persist(runtime, summary, summary_tokens, new_watermark)
        _emit(
            {
                "kind": "COMPACTION_DONE",
                "compactedMessages": _count_db_messages(messages_to_summarize),
                "summaryTokens": summary_tokens,
            }
        )

    @staticmethod
    def _persist(runtime, summary: str, summary_tokens: int, new_watermark: int) -> None:
        """把摘要与水位线 CAS 写回会话行

        以「水位线未变」为条件更新，影响行数为 0 说明会话已被并发压缩，本次落库作废。
        会话 ID 与水位线基线由运行上下文（AgentContext）带入。
        """
        context = getattr(runtime, "context", None)
        if not isinstance(context, AgentContext):
            return
        db = SessionLocal()
        try:
            rows = db.execute(
                update(ChatSession)
                .where(
                    ChatSession.id == context.session_id,
                    ChatSession.summary_upto_message_id == context.watermark,
                )
                .values(
                    summary=summary,
                    summary_upto_message_id=new_watermark,
                    summary_token_count=summary_tokens,
                    compact_count=ChatSession.compact_count + 1,
                    last_input_tokens=None,
                )
            ).rowcount
            db.commit()
            if rows == 0:
                logger.warning(
                    "压缩结果落库 CAS 失败（会话已被并发压缩）：sessionId=%s", context.session_id
                )
            else:
                # 同一轮内若多次压缩，推进本地 CAS 基线，保证下一次落库仍然成功
                context.watermark = new_watermark
        finally:
            db.close()
