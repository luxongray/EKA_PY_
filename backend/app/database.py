"""数据库连接与会话工厂

基于 SQLAlchemy 2.0 + PyMySQL 建立与既有 MySQL 库的连接，
对外提供 Base（ORM 基类）与 SessionLocal（会话工厂）。

@author Luxon
@date 2026/10/10
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings

# 同步引擎：PyMySQL 为同步驱动，FastAPI 的同步端点会跑在线程池中，不阻塞事件循环
engine = create_engine(
    get_settings().database_url,
    pool_pre_ping=True,   # 取连接前先探活，避免 MySQL 主动断开后拿到失效连接
    pool_recycle=3600,    # 连接最长复用 1 小时，规避 MySQL 8 小时空闲断连
    echo=False,
)

# 会话工厂：每个请求创建一个 Session，用完即关
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """ORM 映射基类：所有表模型继承它"""
    pass
