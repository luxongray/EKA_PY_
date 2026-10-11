"""数据库工具（LangChain 原生 @tool）

按原 Java 版设计复刻两个各司其职的数据库工具，直接以列表交给 create_agent：
- search_database_context：返回某张表的业务语义 MD 全文，一次一表；
- execute_sql：只读 SQL 执行，越权校验通过后返回 Markdown 表格。

两工具都不需要会话上下文，故不注入 ToolRuntime；失败直接抛 BusinessException，
交给已挂的 ToolRetryMiddleware 重试、耗尽后把错误交回模型处理。

@author Luxon
@date 2026/10/10
"""

from typing import Annotated

from langchain.tools import tool

from app.common.exceptions import BusinessException
from app.config import get_settings
from app.services import sql_guard
from app.services.table_registry import table_registry


@tool
def search_database_context(
    table_name: Annotated[str, "表名"],
) -> str:
    """数据库表结构与业务语义查询工具。
    【职责范围】本工具负责返回某张数据库表的业务语义（业务含义、字段语义、表关系、常用口径 SQL），
    供组装 SQL 前确认该表的结构与含义。一次只返回一张表的语义。库内可用表如下：ek_appraisal_destroy（鉴定销毁任务）、
    ek_ar_file（原文文件）、ek_ar_main（档案主表/案卷件）、ek_ar_process（归档过程）、ek_ar_project（项目层）、
    ek_ar_side（卷内件）、ek_borrow_order（借阅单）、ek_category（门类树）、ek_process_instance（流程快照）、
    ek_transfer_batch（移交批次）、ek_transfer_detail（移交明细）、ek_user（用户）；库内新增表会自动纳入，
    以报错信息中的可用表清单为准。
    调用时必须传入一个表名，返回该表的业务语义全文（业务含义、字段语义、表关系、常用口径 SQL）。
    涉及多表时逐张调用，一次一表。在调用 execute_sql 编写任何 SQL 之前，必须先调用本工具查清相关表的语义，禁止凭猜测写 SQL
    """
    # 步骤 1：表名必填，留空直接报错并提示可用表，防止模型跳过语义直接写 SQL
    known = table_registry.known_tables()
    if not table_name or not table_name.strip():
        raise BusinessException("必须传入一个表名。库内可用表：" + "、".join(sorted(known)))

    # 步骤 2：表名归一化（去空白 / 去 .md 后缀 / 转小写），并拦截路径穿越字符
    normalized = _normalize_table_name(table_name)

    # 步骤 3：动态白名单校验，未知表名直接报错并提示可用表，引导模型纠正入参
    if normalized not in known:
        raise BusinessException(f"未知表名：{table_name}。库内可用表：" + "、".join(sorted(known)))

    # 步骤 4：定位语义文件并确认仍在根目录下，防止路径穿越
    root = get_settings().sql_md_root_path
    md_file = (root / f"{normalized}.md").resolve()
    if not str(md_file).startswith(str(root)):
        raise BusinessException(f"非法表名：{table_name}")

    # 步骤 5：读取语义全文；文件缺失视为配置问题，明确报错
    if not md_file.is_file():
        raise BusinessException(f"表 {normalized} 的语义文件缺失：{md_file}")
    return md_file.read_text(encoding="utf-8")


@tool
def execute_sql(
    sql: Annotated[str, "Agent 组装的查询语句，仅允许单条 SELECT/WITH，只查白名单内的表"],
) -> str:
    """只读 SQL 执行工具，只允许 SELECT 查询。
    【职责范围】本工具负责回答数据库里可统计、可枚举的结构化事实类问题：档案数量、有哪些卷/件、
    归档状态、某项目下的档案清单、按年度/门类分布等。
    库内可用表如下（库内新增表会自动纳入白名单，以报错信息中的可用表清单为准）：ek_appraisal_destroy、ek_ar_file、ek_ar_main、
    ek_ar_process、ek_ar_project、ek_ar_side、ek_borrow_order、ek_category、ek_process_instance、
    ek_transfer_batch、ek_transfer_detail、ek_user。
    支持多表 JOIN 联查、CTE（WITH…AS…）与子查询，但参与联查的每一张真实表都必须在白名单之内。
    【前置要求】调用本工具前，必须先调用 search_database_context 查清所涉及数据库表的业务语义（字段含义、表关系、常用口径），理解后再组装 SQL，禁止凭猜测直接写 SQL。
    除查询外的一切操作
    （INSERT/UPDATE/DELETE/DROP/CREATE/ALTER 等写操作、多语句堆叠、跨库表、白名单外的表如
    information_schema）都会被拒绝。返回 Markdown 表格结果，超限行数自动截断
    """
    # 步骤 1：越权校验（只读 / 单条 / 白名单 / 本库），不通过直接抛错由模型纠正语句
    sql_guard.validate_only_select(sql)

    # 步骤 2：只读执行并渲染为 Markdown 表格返回给模型
    result = sql_guard.execute_readonly(sql)
    return sql_guard.render_markdown(result, get_settings().sql_max_rows)


def _normalize_table_name(table_name: str) -> str:
    """表名归一化：去首尾空白、兼容误带的 .md 后缀、统一小写，并拦截路径穿越字符

    :param table_name: 原始表名入参
    :return: 归一化后的小写表名
    :raises BusinessException: 含路径穿越或分隔符时
    """
    normalized = table_name.strip().lower()
    if normalized.endswith(".md"):
        normalized = normalized[:-3]
    if ".." in normalized or "/" in normalized or "\\" in normalized:
        raise BusinessException(f"非法表名：{table_name}")
    return normalized


# 交给 create_agent 的数据库工具集合
SQL_TOOLS = [search_database_context, execute_sql]
