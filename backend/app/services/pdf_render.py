"""HTML 转 PDF 渲染服务

用 Playwright 驱动 Chromium（优先复用系统已装的 Chrome）把 HTML 渲染成 PDF 字节流，
渲染全程在内存中完成、不落盘，效果对齐原 Java 后端「导出即去掉网页外壳的纯文档」。

@author Luxon
@date 2026/10/10
"""

from app.common.exceptions import BusinessException

# 导出样式覆盖：中和模型生成 HTML 常见「网页外壳」（灰底、居中白卡片、阴影），
# 使其呈现为纯文档，页面留白交给 PDF 页边距参数控制
EXPORT_CSS = """
html, body { background: #fff !important; margin: 0 !important; padding: 0 !important; }
body > * {
  max-width: none !important;
  width: auto !important;
  margin-left: 0 !important;
  margin-right: 0 !important;
  padding-left: 0 !important;
  padding-right: 0 !important;
  box-shadow: none !important;
  background: transparent !important;
}
"""


def render_pdf(html: str) -> bytes:
    """把 HTML 原文渲染为 PDF 字节流

    :param html: 生成产物的 HTML 原文
    :return: PDF 文件的字节内容
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BusinessException("导出 PDF 失败：未安装 playwright，请先在项目虚拟环境执行 uv sync") from exc

    prepared = _prepare_html(html)
    try:
        with sync_playwright() as playwright:
            # 优先用系统 Chrome，免去下载浏览器内核；缺失时回退 Playwright 自带 Chromium
            try:
                browser = playwright.chromium.launch(channel="chrome")
            except Exception:
                browser = playwright.chromium.launch()
            try:
                page = browser.new_page()
                page.set_content(prepared, wait_until="networkidle")
                page.add_style_tag(content=EXPORT_CSS)
                return page.pdf(
                    format="A4",
                    print_background=True,
                    margin={"top": "14mm", "bottom": "14mm", "left": "12mm", "right": "12mm"},
                )
            finally:
                browser.close()
    except BusinessException:
        raise
    except Exception as exc:
        raise BusinessException(f"导出 PDF 失败：{exc}") from exc


def _prepare_html(html: str) -> str:
    """规整待渲染的 HTML：空值兜底并补齐基本结构，交给浏览器渲染"""
    if not html or not html.strip():
        return "<html><head><meta charset='utf-8'></head><body></body></html>"
    # 已含完整结构则原样返回，否则补最小外壳，保证 UTF-8 声明正确
    if "<html" in html.lower():
        return html
    return f"<html><head><meta charset='utf-8'></head><body>{html}</body></html>"
