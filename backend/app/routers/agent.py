"""Agent 对话路由（对应 /agent）

单接口：POST /agent/chat/stream 流式问答。入参与事件格式对齐前端 eka-api.ts；
建流前先校验会话存在，校验失败直接返回 400（前端按 resp.ok 判错，不进事件流）。

@author Luxon
@date 2026/10/10
"""

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.common.result import fail
from app.deps import get_db
from app.services import agent_service, chat_session_service

router = APIRouter(prefix="/agent", tags=["Agent 对话"])

logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    """对话请求体：本轮问题、记忆 ID 与随消息上传的文件 ID 列表（字段名对齐前端）"""

    model_config = ConfigDict(populate_by_name=True)

    # 用户本轮问题
    message: str | None = None

    # 记忆 ID（即会话 ID），前端透传延续本会话
    memory_id: int | None = Field(default=None, alias="memoryId")

    # 本轮随消息一起发送的已上传文件 ID 列表，可为空
    file_ids: list[int] | None = Field(default=None, alias="fileIds")


@router.post("/chat/stream")
async def chat_stream(payload: ChatRequest, db: Session = Depends(get_db)):
    """流式问答：SSE 推送 STREAM_TEXT / DONE / ERROR 三种事件"""
    # 步骤 1：入参校验（失败在建流之前返回非 200，前端据此判错）
    if not payload.message or not payload.message.strip():
        return JSONResponse(status_code=400, content=fail("消息不能为空"))
    if payload.memory_id is None:
        return JSONResponse(status_code=400, content=fail("记忆 ID 不能为空"))

    # 步骤 2：会话必须存在且未删除
    try:
        chat_session_service.require_session(db, payload.memory_id)
    except BusinessException as error:
        return JSONResponse(status_code=400, content=fail(error.message))

    # 步骤 3：记录入口日志（问题只记前 100 字，避免长文本刷屏）
    logger.info(
        "收到 Agent 流式对话请求：memoryId=%s，问题前 100 字=%s",
        payload.memory_id,
        payload.message[:100],
    )

    # 步骤 4：返回 SSE 流，禁用中间层缓冲以保证逐帧实时到达
    return StreamingResponse(
        agent_service.stream_events(payload.memory_id, payload.message, payload.file_ids),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
