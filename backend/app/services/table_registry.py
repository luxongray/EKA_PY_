"""库表白名单注册表

基于 information_schema 动态加载本库全部业务表（ek_ 前缀）作为 SQL 工具的表级白名单，
带 TTL 缓存——库内新增表最多经一个缓存周期后自动纳入，无需改代码。
供 search_database_context 与 execute_sql 两个工具做表名合法性校验（对齐原 Java 版 TableRegistry）。

@author Luxon
@date 2026/10/10
"""

import logging
import threading
import time

from sqlalchemy import text

from app.config import get_settings
from app.database import engine

logger = logging.getLogger(__name__)

# 业务表白名的匹配前缀：只用 ek_ 系业务表，ai_ 系为 AI 内部表、不进 Agent 白名单
# 下划线需转义（\_），否则 LIKE 会把下划线当通配符误匹配
_BUSINESS_PREFIX = "ek\\_%"


class TableRegistry:
    """本库业务表白名单：小写表名集合 + TTL 缓存，多线程安全"""

    def __init__(self) -> None:
        # 缓存值与其到期时刻；用锁保证并发刷新时只查一次库
        self._lock = threading.Lock()
        self._tables: frozenset[str] = frozenset()
        self._expire_at: float = 0.0

    def known_tables(self) -> frozenset[str]:
        """返回本库全部业务表名（小写）：缓存有效期内直接命中，过期则加锁刷新

        :return: 小写表名的不可变集合；查库失败时抛出底层异常
        """
        # 步骤 1：缓存命中即返回，无锁快路径
        if self._tables and time.monotonic() < self._expire_at:
            return self._tables

        # 步骤 2：未命中则加锁刷新（双检防止并发重复查库）
        with self._lock:
            if self._tables and time.monotonic() < self._expire_at:
                return self._tables
            return self._reload()

    def refresh(self) -> frozenset[str]:
        """强制刷新白名单：跳过缓存直接查库（建表后需立刻生效时用）

        :return: 刷新后的表名集合
        """
        with self._lock:
            return self._reload()

    def _reload(self) -> frozenset[str]:
        """查 information_schema 重载白名单，并把表名统一转小写"""
        settings = get_settings()
        # 步骤 1：只取本库、ek_ 前缀的表名
        with engine.connect() as connection:
            names = (
                connection.execute(
                    text(
                        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                        "WHERE TABLE_SCHEMA = :schema AND TABLE_NAME LIKE :prefix"
                    ),
                    {"schema": settings.sql_database, "prefix": _BUSINESS_PREFIX},
                )
                .scalars()
                .all()
            )

        # 步骤 2：转小写装入不可变集合并刷新缓存到期时刻
        self._tables = frozenset(name.lower() for name in names if name)
        self._expire_at = time.monotonic() + settings.sql_table_cache_seconds
        return self._tables


# 全局单例：两个 SQL 工具与校验层共用
table_registry = TableRegistry()
