"""会话文件工具（LangChain 原生 @tool）

用 LangChain 原生 `@tool` 定义四个文件工具，直接以列表交给 create_agent，不做自定义扫描注册：
- list_files：列出当前会话已上传/已生成的文件，供模型定位「用户提到的某个文件」；
- read_file：读会话文件或档案库原文的正文，超长按固定字符数分页；
- write_file：把内容写成 HTML 产物保存到会话（新建 / 覆盖）；
- edit_file：对已有 HTML 产物做唯一片段替换，避免整篇重写。

会话 ID 经 LangChain 原生 `ToolRuntime.context`（AgentContext.session_id）注入，替代 Java 版
的 @ToolMemoryId；工具内部各自开短连数据库会话，与请求生命周期解耦。工具失败直接抛
BusinessException，由已挂的 ToolRetryMiddleware 重试、耗尽后把错误交回模型处理。

@author Luxon
@date 2026/10/10
"""

import logging
from datetime import datetime
from typing import Annotated

from langchain.tools import ToolRuntime, tool

from app.common.exceptions import BusinessException
from app.config import get_settings
from app.database import SessionLocal
from app.models.ek_ar_file import EkArFile
from app.services import chat_file_service
from app.services.compaction import AgentContext
from app.services.document_reader import extract_text

logger = logging.getLogger(__name__)

# read_file 单页返回的最大字符数：超出即分页，模型用 page 翻页
READ_PAGE_CHARS = 4000

# list_files 单页返回的最大文件数
LIST_PAGE_SIZE = 20

# 新建文件时模型未给名字的兜底文件名
DEFAULT_FILE_NAME = "未命名文件.html"

# 时间展示格式：与前端卡片口径一致
_TIME_FORMAT = "%Y-%m-%d %H:%M"


@tool
def list_files(
    runtime: ToolRuntime[AgentContext],
    keyword: Annotated[str | None, "关键词，按文件名或用途描述模糊过滤，可为空表示不过滤"] = None,
    page: Annotated[int | None, "页码，从 1 开始，默认第 1 页"] = None,
) -> str:
    """列出当前会话中已上传与已生成的文件清单。
    【职责范围】当用户提及某个文件但你无法确定是哪一个（尤其是较早生成或上传的文件）时，
    必须先调用本工具查看清单，再决定读取或修改哪个文件，不要凭猜测臆造文件 ID。
    【使用方式】keyword 可按文件名或用途描述模糊过滤；文件较多时用 page 翻页。
    返回内容包括：文件 ID（read_file / write_file 引用文件时使用）、文件名、来源（上传/生成）、
    格式、用途描述与最后修改时间。
    """
    session_id = runtime.context.session_id
    # 步骤 1：关键词过滤、排序与分页全部下推数据库，只取当前页数据
    db = SessionLocal()
    try:
        result = chat_file_service.list_page(db, session_id, keyword, page or 1, LIST_PAGE_SIZE)
    finally:
        db.close()

    files = result["records"]
    current, pages, total = result["current"], result["pages"], result["total"]

    # 步骤 2：查空分两种情况——有数据却查空说明页码超了，明确告知实际页数让模型重试
    if not files:
        if total > 0:
            return f"页码超出范围：文件共 {pages} 页，请用 page=1..{pages} 重试。"
        if keyword and keyword.strip():
            return f"没有匹配「{keyword}」的文件，可换个关键词或去掉关键词查看全部。"
        return "当前会话还没有任何文件。"

    # 步骤 3：渲染 Markdown 清单，附分页提示
    lines = [f"当前会话文件（第 {current}/{pages} 页，共 {total} 个）：", ""]
    for row in files:
        kind_text = "AI 生成" if row["kind"] == chat_file_service.KIND_GENERATED else "用户上传"
        format_text = chat_file_service.FORMAT_TO_EXTENSION.get(row["format"], "未知")
        line = f"- [id={row['id']}] {row['fileName']}｜来源：{kind_text}｜格式：{format_text}"
        if row.get("description"):
            line += f"｜说明：{row['description']}"
        if row.get("updatedAt"):
            line += f"｜最后修改：{_format_time(row['updatedAt'])}"
        lines.append(line)
    if current < pages:
        lines.extend(["", f"[还有更多，请用 page={current + 1} 查看]"])
    return "\n".join(lines)


@tool
def read_file(
    runtime: ToolRuntime[AgentContext],
    file_id: Annotated[str, "要读取的文件 ID：session 范围取会话文件的数字 ID，database 范围取 ek_ar_file.id"],
    scope: Annotated[str, "文件范围：session=当前会话的文件，database=档案库中的原文文件（按 ek_ar_file.id），必传"],
    page: Annotated[int | None, "页码，从 1 开始，默认第 1 页；内容超长时用其翻页"] = None,
) -> str:
    """读取某个文件的正文内容，支持读取当前会话中已上传或已生成的文件，也支持按 ek_ar_file.id 读取档案库中的原文文件。
    【职责范围】当用户要求查看、总结、分析、翻译某个文件内容时调用本工具；
    或当需要基于已生成文件的内容继续加工时调用。
    【使用方式】file_id 取用户消息中的文件标注、list_files 返回的 id，或查询 ek_ar_file 得到的原文文件 ID，不要臆造 id；
    引用的是更早的会话文件、消息里没有标注时，先用 list_files 查清单定位；
    scope 用于指明文件范围：文件属于当前会话（用户上传或本会话生成）时传 session，
    要读取档案库中的原文文件（按 ek_ar_file.id）时传 database，该参数必传；
    内容超长时本工具按页返回，可用 page 参数翻页读取后续内容。
    支持的文件类型：docx、xlsx、pdf 与生成的 html
    """
    session_id = runtime.context.session_id
    scope_normalized = (scope or "").strip().lower()

    # 步骤 1：按 scope 分派，在数据库里定位文件并取出磁盘路径、展示名与解析用扩展名
    db = SessionLocal()
    try:
        if scope_normalized == "session":
            record = chat_file_service.find_owned(db, session_id, _parse_session_file_id(file_id))
            if record is None:
                raise BusinessException(
                    f"会话文件不存在：{file_id}。请先用 list_files 确认当前会话的文件 ID"
                )
            file_name = record.file_name
            path = chat_file_service.storage.resolve(record.stored_path)
            extension = chat_file_service.FORMAT_TO_EXTENSION.get(record.format, "")
        elif scope_normalized == "database":
            archive = db.get(EkArFile, file_id)
            if archive is None or archive.del_flag == 1:
                raise BusinessException(
                    f"档案原文文件不存在：{file_id}。请先查询档案文件确认文件 ID"
                )
            file_name = archive.file_name or file_id
            path = get_settings().archive_root_path / (archive.file_path or "")
            extension = _extension_of(archive.file_name)
        else:
            raise BusinessException(f"scope 取值非法：{scope}，仅支持 session 或 database")
    finally:
        db.close()

    # 步骤 2：关闭数据库连接后再解析磁盘文件，避免大文件解析长时间占用连接
    text = extract_text(path, extension)

    # 步骤 3：按固定字符数切页，返回指定页并附翻页提示
    return _render_page(file_name, text, page)


@tool
def write_file(
    runtime: ToolRuntime[AgentContext],
    html: Annotated[str, "完整可渲染的 HTML 内容"],
    file_name: Annotated[str | None, "文件名，须以 .html 结尾且有业务含义，如「系统设计说明.html」；修改已有文件时可省略"] = None,
    description: Annotated[str | None, "一句话说明该文件用途，供后续检索定位"] = None,
    file_id: Annotated[int | None, "要修改的文件 ID，直接取用户消息中的文件标注；新建文件时不传"] = None,
) -> str:
    """把内容写成 HTML 文件保存到当前会话中，用户可在线预览并导出为 PDF。
    【职责范围】当用户要求生成报告、方案、文档、说明、表格等可交付产物时调用本工具；
    普通问答直接文字回复即可，不要调用本工具。
    【要求】html 参数传入完整可渲染的 HTML（样式尽量内联，便于导出 PDF 时保真）；
    file_name 用有业务含义的中文名并以 .html 结尾，例如「系统设计说明.html」；
    description 用一句话说明该文件的用途，便于后续检索定位。
    【新建与修改】新建文件时不传 file_id；修改已有文件时必须传 file_id（直接取用户消息中的文件标注，
    消息里没有时先用 list_files 查清单定位），此时 file_name 可省略以沿用原名。仅 AI 生成的文件可被修改，用户上传的源文件不可覆盖。
    """
    session_id = runtime.context.session_id
    # 步骤 1：规整文件名（补 .html 后缀、空名兜底，修改时为空则沿用原名）与 HTML 结构
    normalized_name = _normalize_file_name(file_name, file_id)
    normalized_html = _normalize_html(html)

    # 步骤 2：落库落盘（file_id 为空新建、非空覆盖），回执带文件 ID 供模型后续修改引用
    db = SessionLocal()
    try:
        record = chat_file_service.save_generated(
            db, session_id, file_id, normalized_name, normalized_html, description
        )
    finally:
        db.close()
    logger.info("生成会话文件：sessionId=%s，fileId=%s，fileName=%s", session_id, record.id, record.file_name)
    return f"已生成文件：[id={record.id}] {record.file_name}（用户可在对话中预览并导出 PDF）"


@tool
def edit_file(
    runtime: ToolRuntime[AgentContext],
    file_id: Annotated[int, "要修改的文件 ID，直接取用户消息中的文件标注"],
    old_string: Annotated[str, "被替换的原文片段，须与文件内容逐字符一致且在文件中只出现一次"],
    new_string: Annotated[str, "替换后的新片段，删除该片段时传空字符串"],
) -> str:
    """对已有 HTML 文件做局部修改：只替换指定的片段，不必重写整篇内容。
    【职责范围】文件已生成、只需改动其中一小部分（改标题、改某段文字、调整某个数字等）时用它；
    新建文件或整篇重写请用 write_file。
    【使用方式】file_id 直接取用户消息中的文件标注，消息里没有时先用 list_files 查清单定位；
    old_string 必须与文件中的内容逐字符一致（含缩进与换行），且在文件中只出现一次——
    若提示出现多次，请在片段前后多带一些上下文以唯一定位；不确定原文时先用 read_file 读取；
    仅 AI 生成的文件可被修改，用户上传的源文件不可改。
    """
    session_id = runtime.context.session_id
    # 在 service 内完成唯一性校验与替换并落盘落库，回执带文件 ID 供模型后续引用
    db = SessionLocal()
    try:
        record = chat_file_service.edit_generated(db, session_id, file_id, old_string, new_string)
    finally:
        db.close()
    logger.info("修改会话文件：sessionId=%s，fileId=%s，fileName=%s", session_id, record.id, record.file_name)
    return f"已修改文件：[id={record.id}] {record.file_name}（用户可在对话中预览并导出 PDF）"


# 交给 create_agent 的文件工具集合：顺序即模型可见的工具描述顺序
FILE_TOOLS = [list_files, read_file, write_file, edit_file]


def _parse_session_file_id(file_id: str) -> int:
    """把会话文件的字符串 ID 解析为数字主键；非数字直接报错"""
    try:
        return int(file_id)
    except (TypeError, ValueError) as exc:
        raise BusinessException(f"session 范围的文件 ID 必须是数字：{file_id}") from exc


def _extension_of(file_name: str | None) -> str:
    """从文件名中截取小写扩展名（不含点）；无扩展名返回空串"""
    if not file_name or "." not in file_name:
        return ""
    return file_name.rsplit(".", 1)[1].lower()


def _normalize_file_name(file_name: str | None, file_id: int | None) -> str | None:
    """规整文件名：修改已有文件且未给名时返回 None（沿用原名），新建空名兜底，缺后缀自动补 .html"""
    if not file_name or not file_name.strip():
        return None if file_id is not None else DEFAULT_FILE_NAME
    name = file_name.strip()
    return name if name.lower().endswith(".html") else name + ".html"


def _normalize_html(html: str | None) -> str:
    """规整 HTML：空值兜底并补齐基本结构

    Python 侧的预览（iframe）与导出（Playwright/Chromium）都按 HTML5 解析，
    不像原 Java 版导出链路要求 XHTML 自闭合，故这里只补齐外壳与 UTF-8 声明。
    """
    content = (html or "").strip()
    if "<html" in content.lower():
        return content
    if not content:
        return "<!DOCTYPE html><html><head><meta charset='utf-8'></head><body></body></html>"
    return f"<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>{content}</body></html>"


def _render_page(file_name: str, text: str, page: int | None) -> str:
    """按固定字符数分页渲染：返回指定页正文，未读完时附下一页提示

    :param file_name: 展示文件名，用于回执
    :param text: 文件全文
    :param page: 请求页码，为空或越界时收敛到有效范围
    :return: 该页正文与翻页提示
    """
    # 步骤 1：计算总页数（空文本也算一页）并收敛页码
    total_pages = max(1, (len(text) + READ_PAGE_CHARS - 1) // READ_PAGE_CHARS)
    current = 1 if not page or page < 1 else min(page, total_pages)

    # 步骤 2：截取当前页区间并拼接回执
    start = (current - 1) * READ_PAGE_CHARS
    body = f"【文件】{file_name}（第 {current}/{total_pages} 页，共 {len(text)} 字符）\n"
    body += text[start:start + READ_PAGE_CHARS]

    # 步骤 3：未读完时提示下一页页码
    if current < total_pages:
        body += f"\n\n[内容未完，请用 page={current + 1} 继续读取]"
    return body


def _format_time(value: datetime) -> str:
    """把最后修改时间格式化为展示字符串"""
    return value.strftime(_TIME_FORMAT)
