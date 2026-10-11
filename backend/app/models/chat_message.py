"""AI 会话消息行模型（对应 ai_chat_message 表）：一条消息一行，message_type 用数字编码，content 存 LangChain 官方序列化 JSON"""
from datetime import datetime
from enum import IntEnum

from sqlalchemy import BigInteger, DateTime, Integer, SmallInteger, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ChatMessageTypeCode(IntEnum):
    """会话消息类型编码：与 ai_chat_message.message_type 列取值一一对应"""

    # 系统提示消息
    SYSTEM = 1
    # 用户提问消息（对应 LangChain human）
    USER = 2
    # AI 回复消息（对应 LangChain ai）
    AI = 3
    # 工具执行结果消息（对应 LangChain tool）
    TOOL = 4
    # 自定义消息
    CUSTOM = 5


class ChatMessage(Base):
    """AI 会话消息表映射：按 seq 还原会话内消息先后"""

    __tablename__ = "ai_chat_message"

    # 消息 ID（自增主键，前端用作消息稳定标识）
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # 所属会话 ID（ai_chat_session.id）
    session_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # 会话内顺序号，从 0 开始
    seq: Mapped[int] = mapped_column(Integer, nullable=False)

    # 消息类型编码：1系统 2用户 3AI 4工具 5自定义（对应 ChatMessageTypeCode）
    message_type: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    # 单条消息内容（LangChain 官方序列化 JSON，message_to_dict 单条结果，后端只透传不解析）
    content: Mapped[str] = mapped_column(String, nullable=False)

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
