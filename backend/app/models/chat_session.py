"""AI 会话行模型（对应 ai_chat_session 表）：只承载会话元信息与压缩摘要相关字段"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, SmallInteger, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ChatSession(Base):
    """AI 会话表映射：id 即前端透传的 memoryId"""

    __tablename__ = "ai_chat_session"

    # 会话 ID（自增主键，即 memoryId）
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # 归属用户 ID（无鉴权模式下新会话写配置的默认用户，历史数据原样保留）
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 会话标题（原为 AI 按首问生成，本轮以首问截断占位）
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # 创建时间（数据库自动维护）
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    # 更新时间（数据库自动维护）
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    )

    # 删除标记：0 有效 1 删除
    del_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))

    # 上一次模型调用的真实 input token 数（压缩基线，本轮不涉及）
    last_input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # 当前会话的结构化摘要（压缩产物，本轮不涉及）
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 摘要水位线（已覆盖到最后哪条消息 ID，0 表示从未压缩）
    summary_upto_message_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )

    # 摘要自身 token 数
    summary_token_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    # 累计压缩次数
    compact_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
