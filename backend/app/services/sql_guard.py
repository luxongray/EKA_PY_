"""只读 SQL 的越权校验与执行

对齐原 Java 版 SqlExecuteTool：把语句解析为 AST 后只放行查询节点，非查询语句、
多语句堆叠、跨库表、白名单外表一律拒绝；支持多表 JOIN、CTE 与子查询，CTE 名与
派生表别名不视为真实表。解析用 sqlglot（对位 Java 的 JSqlParser），执行用项目现有
SQLAlchemy 引擎（只读靠 AST 校验 + 只读账号兜底），查询结果渲染为 Markdown 表格。

@author Luxon
@date 2026/10/10
"""

import logging
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from app.common.exceptions import BusinessException
from app.config import get_settings
from app.database import engine
from app.services.table_registry import table_registry

logger = logging.getLogger(__name__)

# 放行的只读查询节点：SELECT / 集合运算 / 子查询（WITH 查询解析后顶层同为 Select）
_READONLY_NODES = (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery)

# 报错信息里回显 SQL 的最大字符数，避免超长语句刷屏
_SQL_PREVIEW_CHARS = 120

# AST 解析方言：与目标数据库保持一致
_DIALECT = "mysql"


@dataclass
class QueryResult:
    """查询结果载体：列名、已读取的数据行与是否因超限被截断"""

    columns: list[str]
    rows: list[tuple]
    truncated: bool


def validate_only_select(sql: str) -> None:
    """SQL 越权校验：只放行单条只读查询，且只查白名单内的本库表

    :param sql: 模型组装的原始 SQL
    :raises BusinessException: 语句为空/无法解析/非查询/多语句/跨库/表白名单外时
    """
    # 步骤 1：空语句直接拒绝
    if not sql or not sql.strip():
        raise BusinessException("SQL 不能为空，只允许单条 SELECT 查询")

    # 步骤 2：全量解析为 AST；解析失败视为非法，多语句（解析结果不止一条）直接拒绝，
    #         避免只放行首条而漏掉尾部隐藏的写操作
    try:
        statements = sqlglot.parse(sql, read=_DIALECT)
    except Exception as exc:
        raise BusinessException(f"只允许单条可解析的 SELECT 查询，拒绝执行：{_preview(sql)}") from exc
    if len(statements) != 1:
        raise BusinessException("一次只允许执行单条 SELECT 查询，拒绝多语句堆叠")

    # 步骤 3：语句级白名单——只放行只读查询节点，写操作节点一律拒绝（无需枚举关键字）
    statement = statements[0]
    if statement is None or not isinstance(statement, _READONLY_NODES):
        raise BusinessException(f"只允许 SELECT 查询，拒绝执行：{_preview(sql)}")

    # 步骤 4：收集参与查询的真实表（已剔除 CTE 名），未引用任何表则拒绝
    tables = _collect_real_tables(statement)
    if not tables:
        raise BusinessException(f"SQL 未引用任何可用表，拒绝执行：{_preview(sql)}")

    # 步骤 5：逐表校验——显式库前缀必须等于本库（否则跨库），表名必须在动态白名单内
    settings = get_settings()
    database = settings.sql_database.lower()
    known = table_registry.known_tables()
    for prefix, name in tables:
        if prefix and prefix.lower() != database:
            raise BusinessException(f"不允许跨库查询：{prefix}，只能查本库 {settings.sql_database}")
        if name.lower() not in known:
            raise BusinessException(
                f"未知表或不允许查询的表：{name}。可用表：" + "、".join(sorted(known))
            )


def execute_readonly(sql: str) -> QueryResult:
    """以只读查询方式执行 SQL 并读取结果集，超过行数上限即停止并标记截断

    :param sql: 已通过越权校验的单条查询语句
    :return: 列名、数据行与是否截断
    :raises BusinessException: 数据库连接或执行失败时
    """
    settings = get_settings()
    try:
        with engine.connect() as connection:
            # 步骤 1：为本次查询设置数据库侧超时（MAX_EXECUTION_TIME 只作用于 SELECT）
            _apply_timeout(connection, settings.sql_timeout_seconds)
            try:
                # 步骤 2：执行查询并逐行读取，达到行数上限即停止读取
                result = connection.exec_driver_sql(sql)
                columns = list(result.keys())
                rows: list[tuple] = []
                truncated = False
                for row in result:
                    if len(rows) >= settings.sql_max_rows:
                        truncated = True
                        break
                    rows.append(tuple(row))
                return QueryResult(columns, rows, truncated)
            finally:
                # 步骤 3：清掉会话超时，避免粘到连接池中的后续查询
                _reset_timeout(connection)
    except BusinessException:
        raise
    except Exception as exc:
        raise BusinessException(f"查询执行失败：{exc}") from exc


def render_markdown(result: QueryResult, max_rows: int) -> str:
    """把查询结果渲染为 Markdown 表格：NULL 显示为 NULL，换行压空格、竖线转义防破表

    :param result: 查询结果
    :param max_rows: 行数上限，用于截断提示
    :return: Markdown 表格文本
    """
    # 无列可渲染时给统一回执，避免拼出空表格
    if not result.columns:
        return "查询完成，但未返回任何列。"

    # 步骤 1：拼接表头行与分隔行
    lines = ["| " + " | ".join(result.columns) + " |", "|" + " --- |" * len(result.columns)]

    # 步骤 2：逐行拼接单元格
    for row in result.rows:
        lines.append("| " + " | ".join(_format_cell(value) for value in row) + " |")

    # 步骤 3：注明行数与截断情况
    text = "\n".join(lines) + f"\n\n共 {len(result.rows)} 行"
    if result.truncated:
        text += f"（已截断，最多返回 {max_rows} 行，请加 WHERE/LIMIT 缩小范围）"
    return text


def _collect_real_tables(statement) -> set[tuple[str, str]]:
    """收集语句中参与查询的真实表，返回 (库前缀, 表名) 集合

    CTE 定义名不视为真实表（先在定义处收集别名再剔除）；派生表别名由解析器识别，不会出现。

    :param statement: 已解析的只读查询节点
    :return: 去重后的 (库前缀, 表名) 集合，无显式库限定时前缀为空串
    """
    # 步骤 1：先收集全部 CTE 定义名，作为后续剔除依据
    cte_names = {(cte.alias or "").lower() for cte in statement.find_all(exp.CTE) if cte.alias}

    # 步骤 2：遍历全部表引用（FROM/JOIN/子查询全覆盖），剔除 CTE 名并保留库前缀
    tables: set[tuple[str, str]] = set()
    for table in statement.find_all(exp.Table):
        name = (table.name or "").strip()
        if not name or name.lower() in cte_names:
            continue
        prefix = (table.db or table.catalog or "").strip()
        tables.add((prefix, name))
    return tables


def _apply_timeout(connection, seconds: int) -> None:
    """为连接设置数据库侧查询超时（毫秒）；设置失败仅记日志，不阻断查询"""
    try:
        connection.exec_driver_sql(f"SET SESSION MAX_EXECUTION_TIME = {max(0, seconds) * 1000}")
    except Exception:
        logger.debug("设置查询超时失败（忽略）")


def _reset_timeout(connection) -> None:
    """清除连接上的会话超时设置，避免影响连接池中的后续查询"""
    try:
        connection.exec_driver_sql("SET SESSION MAX_EXECUTION_TIME = 0")
    except Exception:
        logger.debug("重置查询超时失败（忽略）")


def _format_cell(value) -> str:
    """单元格格式化：NULL 显式标 NULL，换行压成空格、竖线转义避免破坏表格结构"""
    if value is None:
        return "NULL"
    return str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|")


def _preview(sql: str) -> str:
    """把 SQL 压成单行并截断，用于报错回显"""
    compact = " ".join(sql.split())
    return compact if len(compact) <= _SQL_PREVIEW_CHARS else compact[:_SQL_PREVIEW_CHARS] + "..."
