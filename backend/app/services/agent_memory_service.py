"""Agent 会话历史读写

以 ai_chat_message 表作为会话历史的唯一存储：content 存单条消息的 LangChain 官方序列化
结果（messages_to_dict），message_type 用数字编码记录消息类别（对应 ChatMessageTypeCode），
加载时用 messages_from_dict 无损还原为 LangChain 消息对象。

@author Luxon
@date 2026/10/10
"""

import json

from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    message_to_dict,
    messages_from_dict,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.chat_message import ChatMessage, ChatMessageTypeCode
from app.models.chat_session import ChatSession
from app.services.compaction import SUMMARY_PREFIX, db_message_id

# LangChain 消息类型 -> 落库数字编码的映射：与 ChatMessageTypeCode 一一对应
_LC_TYPE_TO_CODE = {
    "system": ChatMessageTypeCode.SYSTEM,
    "human": ChatMessageTypeCode.USER,
    "ai": ChatMessageTypeCode.AI,
    "tool": ChatMessageTypeCode.TOOL,
}


def load_history(db: Session, session_id: int) -> tuple[list[BaseMessage], int]:
    """加载本轮交给模型的历史上下文：旧摘要（若有）+ 水位线之后的历史消息

    ai_chat_session.summary_upto_message_id 之前的历史已被摘要替代，这里只加载水位线
    之后的有效消息，避免把已被压缩的内容重复送入模型（也就是不重复压缩的关键）。
    返回的 messages 中，每条历史消息的 id 被编码为其数据库行 ID，供压缩后反推新水位线。

    :param db: 数据库会话
    :param session_id: 会话 ID（memoryId）
    :return: 二元组（上下文消息列表、当前摘要水位线）；无法识别的行被跳过
    """
    # 步骤 1：读会话行上的摘要与水位线（会话缺失时按空处理）
    session = db.execute(
        select(ChatSession.summary, ChatSession.summary_upto_message_id).where(
            ChatSession.id == session_id
        )
    ).one_or_none()
    summary = session.summary if session is not None else None
    watermark = int(session.summary_upto_message_id or 0) if session is not None else 0

    # 步骤 2：只加载水位线之后的有效消息行（水位线之前的已被摘要替代）
    rows = db.execute(
        select(ChatMessage.id, ChatMessage.message_type, ChatMessage.content)
        .where(
            ChatMessage.session_id == session_id,
            ChatMessage.del_flag == 0,
            ChatMessage.id > watermark,
        )
        .order_by(ChatMessage.id.asc())
    ).all()

    # 步骤 3：旧摘要作为一条人类消息插在最前，再依次还原历史消息并打上数据库行 ID
    history: list[BaseMessage] = []
    if summary and summary.strip():
        history.append(HumanMessage(content=SUMMARY_PREFIX + summary))
    for row in rows:
        message = _to_langchain_message(row.message_type, row.content)
        if message is not None:
            message.id = db_message_id(row.id)
            history.append(message)
    return history, watermark


def save_turn(
    db: Session, session_id: int, user_text: str, turn_messages: list[BaseMessage]
) -> None:
    """一轮问答成功后落库：追加用户提问，以及本轮模型产生的全部消息（AI 回复与工具调用链）

    工具调用链必须完整入库——带 tool_calls 的 AI 消息 + 对应的工具结果消息。否则前端重拉
    历史时工具卡片会整段消失（这正是「一轮结束后工具调用不显示」的根因）。

    :param db: 数据库会话
    :param session_id: 会话 ID
    :param user_text: 本轮用户提问原文（已拼入文件清单）
    :param turn_messages: 本轮模型产生、按时间先后排列的 AI / 工具结果消息
    """
    # 步骤 1：用户提问排在整轮最前，其后依次是各段 AI 回复与工具结果
    messages: list[BaseMessage] = [HumanMessage(content=user_text), *turn_messages]

    # 步骤 2：从当前最大顺序号开始连续编号，整轮一次性写入
    seq = _next_seq(db, session_id)
    for offset, message in enumerate(messages):
        db.add(_build_row(session_id, seq + offset, message))
    db.commit()


def _next_seq(db: Session, session_id: int) -> int:
    """取会话内下一个顺序号：当前最大 seq 加一，无历史时从 0 开始"""
    current = db.execute(
        select(func.max(ChatMessage.seq)).where(
            ChatMessage.session_id == session_id, ChatMessage.del_flag == 0
        )
    ).scalar()
    return 0 if current is None else int(current) + 1


def _build_row(session_id: int, seq: int, message: BaseMessage) -> ChatMessage:
    """把一条 LangChain 消息组装成消息行：类型映射为数字编码，内容用官方序列化 JSON"""
    return ChatMessage(
        session_id=session_id,
        seq=seq,
        message_type=_LC_TYPE_TO_CODE.get(message.type, ChatMessageTypeCode.CUSTOM),
        content=json.dumps(message_to_dict(message), ensure_ascii=False),
        del_flag=0,
    )


def _to_langchain_message(message_type: int, content: str) -> BaseMessage | None:
    """把单条 LangChain 序列化 JSON 还原为消息；系统提示词跳过，解析失败返回 None

    :param message_type: 消息类型编码（ChatMessageTypeCode 的取值）
    :param content: 官方序列化 JSON 原文
    :return: 还原后的消息对象；系统消息或解析失败时返回 None
    """
    # 步骤 1：系统提示词不参与历史上下文，直接跳过
    if message_type == ChatMessageTypeCode.SYSTEM:
        return None
    # 步骤 2：解析 JSON 并用官方反序列化还原；历史脏数据或未知类型一律跳过该行
    try:
        payload = json.loads(content)
        return messages_from_dict([payload])[0]
    except Exception:
        return None
