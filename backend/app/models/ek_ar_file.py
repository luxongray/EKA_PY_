"""档案原文文件行模型（对应 ek_ar_file 表）

read_file 工具以 database 范围读取档案库原文时使用：file_path 存的是相对档案根目录
（config.archive_root）的路径，file_name 决定用哪个解析器。本模型只读，不参与写入。

@author Luxon
@date 2026/10/10
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EkArFile(Base):
    """档案原文文件表映射：以业务可读 ID 作主键，file_path 相对档案根目录定位磁盘文件"""

    __tablename__ = "ek_ar_file"

    # 主键：业务可读 ID（如 file-cbcz-02-1），同时作为检索库中该文件的唯一身份标识
    id: Mapped[str] = mapped_column(String(36), primary_key=True)

    # 归档条目 ID：指向 ek_ar_main.id（单件）或 ek_ar_side.id（卷内件）
    archive_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # 原始文件名（含扩展名）
    file_name: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # 文件存储路径：相对 archive_root 根目录的相对路径
    file_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # 文件类型：0 正文 1 附件 2 原文扫描件
    file_type: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    # 文件大小（字节）
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # 文件 MD5 摘要，用于完整性校验与去重
    md5: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 逻辑删除标记：0 正常 1 已删除
    del_flag: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    # 创建时间
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # 更新时间
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
