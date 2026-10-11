"""文档正文解析

把会话文件与档案原文按扩展名解析为纯文本，供 read_file 工具交给模型阅读。
解析一律下沉到 LangChain 生态加载器与其依赖：docx 用 Docx2txtLoader、pdf 用 PyPDFLoader，
xlsx 用 openpyxl 逐个工作表读取（与原 Java 版一致：带工作表标题、行内用竖线分隔），
生成的 html 产物本身即文本、直接读取。

解析失败统一抛业务异常，由工具层翻译为可读提示；本模块不感知会话与文件归属。

@author Luxon
@date 2026/10/10
"""

from pathlib import Path

from app.common.exceptions import BusinessException

# 支持的解析扩展名：与上传白名单及生成产物保持一致
DOCX = "docx"
XLSX = "xlsx"
PDF = "pdf"
HTML = "html"


def extract_text(path: Path, extension: str) -> str:
    """按扩展名把磁盘文件解析为纯文本

    :param path: 文件绝对路径
    :param extension: 小写扩展名（不含点），取自文件名或落库格式
    :return: 解析出的纯文本正文
    :raises BusinessException: 文件不存在、格式不支持或解析失败时抛出
    """
    # 步骤 1：文件必须真实存在于磁盘，否则给出可读提示而非原始 OSError
    if not path.exists():
        raise BusinessException(f"文件内容缺失，无法读取：{path.name}")

    # 步骤 2：按扩展名分发到对应解析器；html 直接读文本，不走二进制解析
    try:
        if extension == HTML:
            return path.read_text(encoding="utf-8")
        if extension == DOCX:
            return _extract_docx(path)
        if extension == PDF:
            return _extract_pdf(path)
        if extension == XLSX:
            return _extract_xlsx(path)
    except BusinessException:
        raise
    except Exception as exc:
        # 解析库抛出的原始异常统一收敛为业务异常，避免细节泄漏给模型与用户
        raise BusinessException(f"{extension} 文件解析失败：{exc}") from exc

    raise BusinessException(f"不支持的文件格式：{extension}")


def _extract_docx(path: Path) -> str:
    """解析 docx：用 LangChain 的 Docx2txtLoader 提取正文文本"""
    # 延迟导入：解析库只在真正读取文档时才需要，避免应用启动即加载整套生态依赖
    from langchain_community.document_loaders import Docx2txtLoader

    docs = Docx2txtLoader(str(path)).load()
    return "\n".join(doc.page_content for doc in docs)


def _extract_pdf(path: Path) -> str:
    """解析 pdf：用 LangChain 的 PyPDFLoader 逐页提取文本"""
    from langchain_community.document_loaders import PyPDFLoader

    docs = PyPDFLoader(str(path)).load()
    return "\n".join(doc.page_content for doc in docs)


def _extract_xlsx(path: Path) -> str:
    """解析 xlsx：用 openpyxl 逐个工作表读取，行内单元格以竖线分隔（与原 Java 版一致）"""
    from openpyxl import load_workbook

    # read_only + data_only：流式读取且取单元格显示值，兼顾大文件性能与读取准确性
    workbook = load_workbook(filename=str(path), read_only=True, data_only=True)
    try:
        lines: list[str] = []
        for sheet in workbook.worksheets:
            lines.append(f"【工作表】{sheet.title}")
            for row in sheet.iter_rows(values_only=True):
                # 跳过整行为空的占位行，避免表格尾部大段空白撑大文本
                cells = ["" if value is None else str(value) for value in row]
                if any(cells):
                    lines.append(" | ".join(cells))
        return "\n".join(lines)
    finally:
        workbook.close()
