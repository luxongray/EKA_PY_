"""会话消息业务服务

消息行只追加不删除，按会话加载全部有效消息行（顺序号升序），
content 为 LangChain 官方序列化 JSON，后端仅透传不解析（解析交给前端）。

@author Luxon
@date 2026/10/10
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.chat_message import ChatMessage, ChatMessageTypeCode


def list_by_session_id(db: Session, session_id: int) -> list[dict]:
    """按会话查询全部有效消息行，返回 [{id, message}]（顺序号升序）"""
    rows = db.execute(
        select(ChatMessage.id, ChatMessage.content)
        .where(ChatMessage.session_id == session_id, ChatMessage.del_flag == 0)
        .order_by(ChatMessage.seq.asc())
    ).all()
    return [{"id": row.id, "message": row.content} for row in rows]


def max_message_id(db: Session, session_id: int) -> int:
    """取会话内当前最大的消息 ID；会话内无消息时返回 0

    供轮次收尾把本轮生成/修改的文件锚到最后一条消息（即本轮 AI 回复）使用。
    """
    return db.execute(
        select(func.max(ChatMessage.id)).where(
            ChatMessage.session_id == session_id, ChatMessage.del_flag == 0
        )
    ).scalar() or 0


def first_user_message_id_after(db: Session, session_id: int, after_id: int) -> int | None:
    """取会话内 ID 大于 after_id 的第一条用户消息 ID（本轮用户消息）

    只认用户消息：系统提示若随本轮写入会晚于本轮起点，按「第一条消息」取会误锚到系统消息下，
    而系统消息在界面上不渲染，文件卡片会因此挂不上。

    :param after_id: 本轮开始前的最大消息 ID（不含）
    :return: 本轮第一条用户消息 ID；无匹配时返回 None
    """
    return db.execute(
        select(ChatMessage.id)
        .where(
            ChatMessage.session_id == session_id,
            ChatMessage.del_flag == 0,
            ChatMessage.message_type == ChatMessageTypeCode.USER,
            ChatMessage.id > after_id,
        )
        .order_by(ChatMessage.id.asc())
        .limit(1)
    ).scalar_one_or_none()
