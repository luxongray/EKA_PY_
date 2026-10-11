"""会话文件路由（对应 /file）

复刻原 Java 后端的 4 个接口：上传源文件、查文件列表、预览 HTML 产物、导出 PDF。
上传与列表经 Result 包裹；预览/导出直接返回字节流（与原后端一致）。

@author Luxon
@date 2026/10/10
"""

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.common.result import fail, ok
from app.deps import get_db
from app.services import chat_file_service, chat_session_service

router = APIRouter(prefix="/file", tags=["会话文件"])


@router.post("/upload")
def upload(
    memory_id: int | None = Form(None, alias="memoryId"),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
):
    """上传用户源文件：单次最多 5 个，仅支持 docx/xlsx/pdf"""
    if memory_id is None:
        raise BusinessException("记忆 ID 不能为空")
    return ok(chat_file_service.upload(db, memory_id, files))


@router.get("/list")
def list_files(
    memory_id: int | None = Query(None, alias="memoryId"),
    db: Session = Depends(get_db),
):
    """查询会话文件列表：先校验会话存在，返回该会话全部有效文件"""
    if memory_id is None:
        raise BusinessException("记忆 ID 不能为空")
    chat_session_service.require_session(db, memory_id)
    return ok(chat_file_service.list_by_session(db, memory_id))


@router.get("/preview")
def preview(
    file_id: int | None = Query(None, alias="fileId"),
    db: Session = Depends(get_db),
):
    """预览生成文件的 HTML：前端 iframe 直接加载本接口地址

    预览/导出返回的是字节流而非 Result 包裹，失败时须返回非 200 状态码：
    前端据此走错误分支展示提示，若沿用 200 会被误当作正常内容下载/渲染。
    """
    if file_id is None:
        return JSONResponse(status_code=500, content=fail("文件 ID 不能为空"))
    try:
        html = chat_file_service.preview_html(db, file_id)
    except BusinessException as exc:
        return JSONResponse(status_code=500, content=fail(exc.message))
    return Response(content=html.encode("utf-8"), media_type="text/html; charset=UTF-8")


@router.get("/export")
def export(
    file_id: int | None = Query(None, alias="fileId"),
    db: Session = Depends(get_db),
):
    """导出生成文件为 PDF：渲染在内存中完成、不落盘，返回 PDF 字节流

    失败时返回非 200 + Result JSON：前端 exportHtmlAsPdf 会读取 message 弹出提示，
    避免把错误 JSON 当作 PDF 下载下来导致「文件已损坏」。
    """
    if file_id is None:
        return JSONResponse(status_code=500, content=fail("文件 ID 不能为空"))
    try:
        file_name, content = chat_file_service.export_pdf(db, file_id)
    except BusinessException as exc:
        return JSONResponse(status_code=500, content=fail(exc.message))
    # 文件名含中文，按 RFC 5987 编码后放进 Content-Disposition，否则浏览器解析乱码
    encoded_name = quote(file_name)
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"}
    return Response(content=content, media_type="application/pdf", headers=headers)
