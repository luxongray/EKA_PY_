"""会话文件行模型（对应 ai_chat_file 表）：用户上传源文件与 AI 生成产物统一存放"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, SmallInteger, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ChatFile(Base):
    """会话文件表映射：磁盘以主键 id 命名，前端卡片按 anchor_message_id 归组"""

    __tablename__ = "ai_chat_file"

    # 文件 ID（自增主键，同时作为磁盘文件名）
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # 所属会话 ID（ai_chat_session.id）
    session_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # 展示文件名（含扩展名）
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # 磁盘相对路径（相对会话文件根目录，以 fileId 命名）
    stored_path: Mapped[str] = mapped_column(String(512), nullable=False)

    # 文件来源：1 用户上传 2 AI 生成
    kind: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    # 文件格式：1 docx 2 xlsx 3 pdf 4 html
    format: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    # 文件用途描述（上传时为空，生成时 AI 自报）
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # 文件大小（字节）
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # 占位符锚定的消息 ID
    anchor_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # 删除标记：0 有效 1 删除
    del_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))

    # 创建时间（数据库自动维护）
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, server_default=text("CURRENT_TIMESTAMP")
    )

    # 更新时间（数据库自动维护，卡片展示此时间）
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    )
