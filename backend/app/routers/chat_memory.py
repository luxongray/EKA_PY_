"""会话记忆路由（对应 /chat-memory）

复刻原 Java 后端的 6 个接口：新建（标题占位）、改名、分页、查内容、
批量删除、按标题模糊查。所有接口经 Result 包裹返回。

@author Luxon
@date 2026/10/10
"""

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.common.result import ok
from app.deps import get_db
from app.services import chat_file_service, chat_message_service, chat_session_service

router = APIRouter(prefix="/chat-memory", tags=["会话记忆"])


@router.post("/title/generate")
def generate_title(
    first_message: str | None = Query(None, alias="firstMessage"),
    db: Session = Depends(get_db),
):
    """新建会话：生成占位标题并落库，返回记忆 ID 与标题"""
    if first_message is None or not first_message.strip():
        raise BusinessException("首个提问不能为空")
    return ok(chat_session_service.create_session(db, first_message))


@router.put("/title")
def rename_title(
    memory_id: int | None = Query(None, alias="memoryId"),
    title: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """修改会话名：只更新有效会话的标题列"""
    if memory_id is None:
        raise BusinessException("记忆 ID 不能为空")
    if title is None or not title.strip():
        raise BusinessException("新标题不能为空")
    return ok(chat_session_service.rename_title(db, memory_id, title))


@router.get("/page")
def page_sessions(
    page_num: int = Query(1, alias="pageNum"),
    page_size: int = Query(20, alias="pageSize"),
    db: Session = Depends(get_db),
):
    """分页查询会话集合：按修改时间降序，列表不带消息内容"""
    if page_num < 1:
        raise BusinessException("页码最小为 1")
    if page_size < 1:
        raise BusinessException("每页条数最小为 1")
    return ok(chat_session_service.page_sessions(db, page_num, page_size))


@router.get("/content")
def get_session_content(
    memory_id: int | None = Query(None, alias="memoryId"),
    db: Session = Depends(get_db),
):
    """查询会话内容：先校验会话存在，返回「消息 ID + 单条消息 JSON」列表"""
    if memory_id is None:
        raise BusinessException("记忆 ID 不能为空")
    chat_session_service.require_session(db, memory_id)
    return ok(chat_message_service.list_by_session_id(db, memory_id))


@router.delete("/batch")
def batch_delete_sessions(
    memory_ids: list[int] = Body(...),
    db: Session = Depends(get_db),
):
    """批量删除会话：逻辑删会话行，并清理这些会话的文件元数据与磁盘目录"""
    if not memory_ids:
        raise BusinessException("记忆 ID 集合不能为空")
    # 步骤 1：逻辑删会话行
    deleted = chat_session_service.batch_delete(db, memory_ids)
    # 步骤 2：逐个清理会话文件（元数据 + 磁盘目录）
    for memory_id in memory_ids:
        chat_file_service.delete_by_session(db, memory_id)
    return ok(deleted)


@router.get("/search")
def search_by_title(
    keyword: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """根据会话标题模糊查询会话：keyword 为空时查全部有效会话"""
    return ok(chat_session_service.search_by_title(db, keyword))
