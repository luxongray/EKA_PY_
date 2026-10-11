"""会话文件业务服务

统一承载用户上传、列表查询、HTML 预览、PDF 导出与按会话清理：
元数据落 ai_chat_file 表，文件内容落 FileStorage 管理的磁盘目录。

@author Luxon
@date 2026/10/10
"""

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.config import get_settings
from app.models.chat_file import ChatFile
from app.models.chat_session import ChatSession
from app.services.chat_session_service import require_session
from app.services.file_storage import FileStorage
from app.services.file_turn_tracker import turn_tracker
from app.services.pdf_render import render_pdf

# 文件来源编码：与前端 FILE_KIND 一一对应
KIND_SOURCE = 1
KIND_GENERATED = 2

# 文件格式编码：与前端 FILE_FORMAT 一一对应
FORMAT_DOCX = 1
FORMAT_XLSX = 2
FORMAT_PDF = 3
FORMAT_HTML = 4

# 扩展名 -> 格式编码
EXTENSION_TO_FORMAT = {"docx": FORMAT_DOCX, "xlsx": FORMAT_XLSX, "pdf": FORMAT_PDF, "html": FORMAT_HTML}

# 格式编码 -> 磁盘扩展名
FORMAT_TO_EXTENSION = {FORMAT_DOCX: "docx", FORMAT_XLSX: "xlsx", FORMAT_PDF: "pdf", FORMAT_HTML: "html"}

# 允许上传的源文件格式白名单：仅 docx/xlsx/pdf，生成产物 html 不允许再传
UPLOAD_FORMATS = {FORMAT_DOCX, FORMAT_XLSX, FORMAT_PDF}

# 单次上传允许的最大文件数
MAX_UPLOAD_COUNT = 5

# 存储单例
storage = FileStorage()


def upload(db: Session, session_id: int, files: list) -> list[dict]:
    """上传用户源文件：校验类型与数量后，元数据落表、内容以文件 ID 命名落盘

    :param files: UploadFile 列表，单次最多 5 个
    :return: 落库后的文件视图列表
    """
    # 步骤 1：数量校验
    if not files:
        raise BusinessException("请选择要上传的文件")
    if len(files) > MAX_UPLOAD_COUNT:
        raise BusinessException(f"单次最多上传 {MAX_UPLOAD_COUNT} 个文件")

    # 步骤 2：先确认会话存在，并取出归属用户 ID（决定磁盘目录层级）
    session = require_session(db, session_id)
    user_id = session.user_id or get_settings().default_user_id

    result: list[dict] = []
    for upload_file in files:
        original_name = upload_file.filename or ""

        # 步骤 3：解析扩展名并做白名单校验
        extension = _extract_extension(original_name)
        file_format = EXTENSION_TO_FORMAT.get(extension or "")
        if file_format is None or file_format not in UPLOAD_FORMATS:
            raise BusinessException(f"不支持的文件类型：{original_name}")

        # 步骤 4：读字节并先插入元数据行拿自增 ID（stored_path 非空，先占空串）
        data = upload_file.file.read()
        record = ChatFile(
            session_id=session_id,
            file_name=original_name,
            kind=KIND_SOURCE,
            format=file_format,
            file_size=len(data),
            stored_path="",
            del_flag=0,
        )
        db.add(record)
        db.commit()
        db.refresh(record)

        # 步骤 5：以文件 ID 命名落盘，并把相对路径回填到元数据行
        record.stored_path = storage.store(user_id, session_id, record.id, FORMAT_TO_EXTENSION[file_format], data)
        db.commit()

        result.append(_to_vo(record))
    return result


def list_by_session(db: Session, session_id: int) -> list[dict]:
    """查询某会话的全部有效文件（上传 + 生成），按最后修改时间倒序"""
    rows = db.execute(
        select(ChatFile)
        .where(ChatFile.session_id == session_id, ChatFile.del_flag == 0)
        .order_by(ChatFile.updated_at.desc())
    ).scalars().all()
    return [_to_vo(row) for row in rows]


def preview_html(db: Session, file_id: int) -> str:
    """读取生成产物的 HTML 原文：非 HTML 产物一律拒绝"""
    record = _require_file(db, file_id)
    if record.format != FORMAT_HTML:
        raise BusinessException("该文件不是可预览或导出的文档产物")
    _ensure_stored(record)
    return storage.read_text(record.stored_path)


def export_pdf(db: Session, file_id: int) -> tuple[str, bytes]:
    """把生成产物渲染为 PDF：读回 HTML 原文渲染成 PDF 字节，全程不落盘

    :return: (下载文件名, PDF 字节内容)
    """
    # 步骤 1：定位文件并读回 HTML 原文
    record = _require_file(db, file_id)
    if record.format != FORMAT_HTML:
        raise BusinessException("该文件不是可预览或导出的文档产物")
    _ensure_stored(record)
    html = storage.read_text(record.stored_path)

    # 步骤 2：渲染为 PDF 字节，下载名把 .html 后缀换成 .pdf
    content = render_pdf(html)
    file_name = _html_to_pdf_name(record.file_name)
    return file_name, content


def delete_by_session(db: Session, session_id: int) -> None:
    """清理会话的文件数据：逻辑删除全部文件元数据，并移除磁盘上的会话目录"""
    # 步骤 1：取出会话归属用户用于定位磁盘目录（不过滤删除标记：会话此时可能已被逻辑删除）
    user_id = db.execute(
        select(ChatSession.user_id).where(ChatSession.id == session_id)
    ).scalar_one_or_none() or get_settings().default_user_id

    # 步骤 2：逻辑删除文件元数据行
    db.execute(
        update(ChatFile)
        .where(ChatFile.session_id == session_id, ChatFile.del_flag == 0)
        .values(del_flag=1)
    )
    db.commit()

    # 步骤 3：删除磁盘目录，失败只记日志不影响会话删除结果（此处静默处理）
    storage.delete_session_dir(user_id, session_id)


def find_owned(db: Session, session_id: int, file_id: int | None) -> ChatFile | None:
    """按文件 ID 定位当前会话内的有效文件

    文件必须属于该会话且未被删除，否则一律视为不存在——与 Java 版 findOwned 一致，
    防止模型引用到别的会话或已被删除的文件。

    :param db: 数据库会话
    :param session_id: 会话 ID，用于校验文件归属
    :param file_id: 文件 ID，可为空
    :return: 文件行；不存在、已删除或不属于该会话时返回 None
    """
    # 步骤 1：入参兜底，任一为空都无从定位
    if session_id is None or file_id is None:
        return None

    # 步骤 2：按主键取行，再校验删除标记与会话归属，避免跨会话越权访问
    record = db.get(ChatFile, file_id)
    if record is None or record.del_flag == 1 or record.session_id != session_id:
        return None
    return record


def save_generated(
    db: Session,
    session_id: int,
    file_id: int | None,
    file_name: str | None,
    html: str,
    description: str | None,
) -> ChatFile:
    """保存 AI 生成的 HTML 产物：file_id 为空则新建，非空则覆盖该文件（仅 AI 生成的文件可被覆盖）

    :param db: 数据库会话
    :param session_id: 会话 ID
    :param file_id: 要覆盖的文件 ID，为空表示新建
    :param file_name: 展示文件名（含 .html）；覆盖时为空表示沿用原文件名
    :param html: HTML 正文
    :param description: 用途描述；覆盖时为空表示保留原描述
    :return: 落库后的文件行
    :raises BusinessException: 要覆盖的文件不存在或不是 AI 生成产物时
    """
    # 步骤 1：确定磁盘目录归属用户，并计算内容大小供元数据展示
    user_id = _session_user_id(db, session_id)
    size = len(html.encode("utf-8"))

    # 步骤 2：定位目标行——未给 file_id 即新建，给了即复用该行做覆盖式修改
    if file_id is None:
        # stored_path 列非空，先占空串，落盘后回填真实路径
        record = ChatFile(
            session_id=session_id,
            file_name=file_name or "未命名文件.html",
            kind=KIND_GENERATED,
            format=FORMAT_HTML,
            description=description,
            file_size=size,
            stored_path="",
            del_flag=0,
        )
        db.add(record)
        db.commit()
        db.refresh(record)
    else:
        # 覆写用户上传的源文件会破坏原始资料，故仅允许覆盖 AI 生成的文件
        record = find_owned(db, session_id, file_id)
        if record is None:
            raise BusinessException(f"要修改的文件不存在：{file_id}")
        if record.kind != KIND_GENERATED:
            raise BusinessException(f"该文件是用户上传的源文件，不支持修改：{record.file_name}")
        record.file_size = size
        # 文件名与描述为空时保留原值，避免覆盖式修改把展示名或用途说明抹掉
        if file_name:
            record.file_name = file_name
        if description:
            record.description = description

    # 步骤 3：以文件 ID 命名落盘并回填路径（覆盖与新建共用同一路径）
    extension = FORMAT_TO_EXTENSION[FORMAT_HTML]
    record.stored_path = storage.store_text(user_id, session_id, record.id, extension, html)
    db.commit()
    db.refresh(record)

    # 步骤 4：登记本轮变更，轮次收尾时统一把占位符锚到最后一条消息
    turn_tracker.mark(session_id, record.id)
    return record


def edit_generated(
    db: Session, session_id: int, file_id: int, old_string: str, new_string: str
) -> ChatFile:
    """对 AI 生成的 HTML 文件做局部替换：把正文中唯一出现的 old_string 替换为 new_string

    只改写命中处，避免整篇重写带来的 token 开销与内容漂移。

    :param db: 数据库会话
    :param session_id: 会话 ID
    :param file_id: 要修改的文件 ID，必须属于该会话且为 AI 生成的 HTML
    :param old_string: 被替换的原文片段，不允许为空，且必须在文件中唯一出现
    :param new_string: 替换后的新片段，允许为空串（表示删除该片段）
    :return: 落库后的文件行
    :raises BusinessException: 片段为空、文件不可改、未命中或多次命中时
    """
    # 步骤 1：被替换片段不能为空——空串会在任意位置命中，替换语义随之失效
    if not old_string:
        raise BusinessException("要替换的原文片段不能为空")

    # 步骤 2：定位文件并校验类型，仅 AI 生成的 HTML 产物可做局部替换
    record = find_owned(db, session_id, file_id)
    if record is None:
        raise BusinessException(f"要修改的文件不存在：{file_id}")
    if record.kind != KIND_GENERATED or record.format != FORMAT_HTML:
        raise BusinessException(f"该文件是用户上传的源文件，不支持修改：{record.file_name}")

    # 步骤 3：读回现有正文并校验命中唯一——零处命中说明片段与原文不符，多处命中则无法确定改哪处
    content = storage.read_text(record.stored_path)
    start = content.find(old_string)
    if start < 0:
        raise BusinessException("文件中未找到要替换的原文片段，请先用 read_file 核对原文")
    if content.find(old_string, start + len(old_string)) >= 0:
        raise BusinessException("要替换的原文片段在文件中出现多次，请补充更长的上下文以唯一定位")

    # 步骤 4：拼出新正文后复用统一的生成流程，落盘、刷新元数据一并完成
    updated = content[:start] + (new_string or "") + content[start + len(old_string):]
    return save_generated(db, session_id, file_id, None, updated, None)


def list_page(
    db: Session, session_id: int, keyword: str | None, page_num: int, page_size: int
) -> dict:
    """分页查询会话文件：关键词按文件名或用途描述模糊过滤，返回当前页与分页元信息

    :param db: 数据库会话
    :param session_id: 会话 ID
    :param keyword: 关键词，可为空表示不过滤
    :param page_num: 页码，从 1 开始
    :param page_size: 每页条数
    :return: {records, total, current, pages} 的分页结果
    """
    # 步骤 1：关键词有效时才拼 LIKE，并用 or 把文件名/描述两个条件整体括起
    condition = select(ChatFile).where(ChatFile.session_id == session_id, ChatFile.del_flag == 0)
    if keyword and keyword.strip():
        needle = f"%{keyword.strip()}%"
        condition = condition.where(or_(ChatFile.file_name.like(needle), ChatFile.description.like(needle)))

    # 步骤 2：总条数与当前页分别在数据库完成，避免把全量数据拉进内存
    total = db.execute(select(func.count()).select_from(condition.subquery())).scalar() or 0
    current = page_num if page_num and page_num >= 1 else 1
    rows = (
        db.execute(
            condition.order_by(ChatFile.updated_at.desc())
            .limit(page_size)
            .offset((current - 1) * page_size)
        )
        .scalars()
        .all()
    )

    # 步骤 3：拼出分页元信息，总页数至少为 1（空结果也算一页）
    pages = max(1, (total + page_size - 1) // page_size)
    return {"records": [_to_vo(row) for row in rows], "total": total, "current": current, "pages": pages}


def anchor_files(db: Session, file_ids, anchor_message_id: int | None) -> None:
    """批量回填占位符锚点：把指定文件的 anchor_message_id 更新为给定消息 ID

    :param file_ids: 文件 ID 集合，允许为空
    :param anchor_message_id: 锚定的消息 ID，为空时不做任何更新
    """
    if not file_ids or anchor_message_id is None:
        return
    db.execute(
        update(ChatFile).where(ChatFile.id.in_(file_ids)).values(anchor_message_id=anchor_message_id)
    )
    db.commit()


def list_vo_by_ids(db: Session, file_ids) -> list[dict]:
    """按 ID 集合批量取文件视图（发消息时解析前端透传的上传文件清单）

    :param file_ids: 文件 ID 集合，允许为空
    :return: 文件视图列表；入参为空时返回空列表
    """
    if not file_ids:
        return []
    rows = db.execute(select(ChatFile).where(ChatFile.id.in_(file_ids))).scalars().all()
    return [_to_vo(row) for row in rows]


def _session_user_id(db: Session, session_id: int) -> str:
    """取会话归属用户 ID 决定磁盘目录层级；会话缺失时退回默认用户"""
    user_id = db.execute(
        select(ChatSession.user_id).where(ChatSession.id == session_id)
    ).scalar_one_or_none()
    return user_id or get_settings().default_user_id


def _require_file(db: Session, file_id: int) -> ChatFile:
    """按 ID 定位有效文件；不存在或已删除时抛业务异常"""
    record = db.execute(
        select(ChatFile).where(ChatFile.id == file_id, ChatFile.del_flag == 0)
    ).scalar_one_or_none()
    if record is None:
        raise BusinessException("文件不存在")
    return record


def _ensure_stored(record: ChatFile) -> None:
    """校验磁盘路径非空：历史脏数据的 stored_path 可能为空，提前给出可读提示"""
    if not record.stored_path:
        raise BusinessException(f"文件内容缺失，无法预览或导出：{record.file_name}")


def _extract_extension(file_name: str) -> str | None:
    """从文件名中截取小写扩展名（不含点）；无扩展名返回 None"""
    if not file_name or "." not in file_name:
        return None
    return file_name.rsplit(".", 1)[1].lower()


def _html_to_pdf_name(file_name: str) -> str:
    """把展示文件名的 .html/.htm 后缀换成 .pdf"""
    lower = file_name.lower()
    for suffix in (".html", ".htm"):
        if lower.endswith(suffix):
            return file_name[: -len(suffix)] + ".pdf"
    return file_name + ".pdf"


def _to_vo(record: ChatFile) -> dict:
    """实体转对外视图：剥离磁盘路径等内部字段"""
    return {
        "id": record.id,
        "fileName": record.file_name,
        "kind": record.kind,
        "format": record.format,
        "description": record.description,
        "fileSize": record.file_size,
        "anchorMessageId": record.anchor_message_id,
        "updatedAt": record.updated_at,
    }
