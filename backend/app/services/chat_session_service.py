"""会话记忆业务服务

承载会话元信息的读写：新建（标题占位）、改名、分页、查内容入口、批量删除、模糊查。
无鉴权模式下不再按用户过滤，所有有效会话对请求可见。

@author Luxon
@date 2026/10/10
"""

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.config import get_settings
from app.models.chat_session import ChatSession
from app.services.llm import generate_title

# 标题兜底规则：大模型生成失败时，截取首问前 N 个字符
TITLE_MAX_CHARS = 15

# 无标题时的兜底文案，与原 Java 版一致
DEFAULT_TITLE = "新会话"


def create_session(db: Session, first_message: str) -> dict:
    """新建会话：生成占位标题并落库，返回记忆 ID 与标题

    :param first_message: 用户首个提问原文
    :return: {memoryId, title}
    """
    # 步骤 1：优先用大模型按首问生成标题（内置重试与兜底）；生成失败退回截断占位
    title = generate_title(first_message) or _stub_title(first_message)

    # 步骤 2：标题落库并写归属用户
    session = ChatSession(user_id=get_settings().default_user_id, title=title, del_flag=0)
    db.add(session)
    db.commit()
    db.refresh(session)
    return {"memoryId": session.id, "title": title}


def require_session(db: Session, memory_id: int) -> ChatSession:
    """校验会话存在且未删除，返回会话行；不存在则抛业务异常"""
    session = db.execute(
        select(ChatSession).where(ChatSession.id == memory_id, ChatSession.del_flag == 0)
    ).scalar_one_or_none()
    if session is None:
        raise BusinessException("会话不存在")
    return session


def rename_title(db: Session, memory_id: int, title: str) -> bool:
    """修改会话标题：只更新有效会话的 title 列"""
    result = db.execute(
        update(ChatSession)
        .where(ChatSession.id == memory_id, ChatSession.del_flag == 0)
        .values(title=title)
    )
    db.commit()
    return result.rowcount > 0


def page_sessions(db: Session, page_num: int, page_size: int) -> dict:
    """分页查询有效会话：按更新时间降序，只返回 id/title/updatedAt"""
    # 总数
    total = db.execute(
        select(func.count()).select_from(ChatSession).where(ChatSession.del_flag == 0)
    ).scalar_one()

    # 当前页数据
    rows = db.execute(
        select(ChatSession.id, ChatSession.title, ChatSession.updated_at)
        .where(ChatSession.del_flag == 0)
        .order_by(ChatSession.updated_at.desc())
        .offset((page_num - 1) * page_size)
        .limit(page_size)
    ).all()

    records = [{"id": row.id, "title": row.title, "updatedAt": row.updated_at} for row in rows]
    return {"records": records, "total": total, "current": page_num, "size": page_size}


def search_by_title(db: Session, keyword: str | None) -> list[dict]:
    """按标题模糊查询有效会话；keyword 为空时等价于查全部"""
    stmt = select(ChatSession.id, ChatSession.title).where(ChatSession.del_flag == 0)
    if keyword is not None and keyword.strip():
        stmt = stmt.where(ChatSession.title.like(f"%{keyword.strip()}%"))
    stmt = stmt.order_by(ChatSession.updated_at.desc())
    rows = db.execute(stmt).all()
    return [{"id": row.id, "title": row.title} for row in rows]


def batch_delete(db: Session, memory_ids: list[int]) -> bool:
    """批量逻辑删除会话（只改 del_flag，不删物理行）"""
    result = db.execute(
        update(ChatSession)
        .where(ChatSession.id.in_(memory_ids), ChatSession.del_flag == 0)
        .values(del_flag=1)
    )
    db.commit()
    return result.rowcount > 0


def _stub_title(first_message: str) -> str:
    """标题兜底：去除首尾空白后截取前 N 个字符，为空时用默认文案

    仅在大模型生成标题失败时启用，保证建会话流程不因模型异常而中断。
    """
    text = (first_message or "").strip()
    if not text:
        return DEFAULT_TITLE
    return text[:TITLE_MAX_CHARS]
