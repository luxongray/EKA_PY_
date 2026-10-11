"""本地文件存储服务

负责「{userId}/{sessionId}/{fileId}.{ext}」目录结构下的路径计算、落盘与读取，
是上传接口与预览/导出接口共用的底层存储入口，根目录由配置的 session_root 决定。

@author Luxon
@date 2026/10/10
"""

from pathlib import Path

from app.common.exceptions import BusinessException
from app.config import get_settings


class FileStorage:
    """会话文件磁盘存储：所有路径均以相对根目录的正斜杠形式入库，跨平台一致"""

    def __init__(self) -> None:
        # 根目录：配置里可为相对项目根的路径，此处已解析为绝对路径
        self.root: Path = get_settings().session_root_path

    def build_relative_path(self, user_id: str, session_id: int, file_id: int, extension: str) -> str:
        """拼出相对根目录的存储路径，形如 "1/5/12.docx"（统一正斜杠）"""
        return f"{user_id}/{session_id}/{file_id}.{extension}"

    def resolve(self, relative_path: str) -> Path:
        """把相对路径解析为磁盘绝对路径"""
        return self.root / relative_path

    def store(self, user_id: str, session_id: int, file_id: int, extension: str, data: bytes) -> str:
        """把字节内容落盘为「{fileId}.{ext}」，目录不存在时自动创建，返回相对路径"""
        relative_path = self.build_relative_path(user_id, session_id, file_id, extension)
        target = self.resolve(relative_path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        except OSError as exc:
            raise BusinessException(f"文件落盘失败：{relative_path}") from exc
        return relative_path

    def store_text(self, user_id: str, session_id: int, file_id: int, extension: str, content: str) -> str:
        """把文本以 UTF-8 落盘（生成 HTML 产物时使用）"""
        return self.store(user_id, session_id, file_id, extension, content.encode("utf-8"))

    def read_bytes(self, relative_path: str) -> bytes:
        """读取文件原始字节，文件缺失时抛业务异常"""
        target = self.resolve(relative_path)
        try:
            return target.read_bytes()
        except OSError as exc:
            raise BusinessException(f"文件读取失败：{relative_path}") from exc

    def read_text(self, relative_path: str) -> str:
        """以 UTF-8 读取文本文件（预览/导出生成的 HTML 时使用）"""
        return self.read_bytes(relative_path).decode("utf-8")

    def delete_session_dir(self, user_id: str, session_id: int) -> bool:
        """递归删除某个会话的整个文件目录；目录不存在视为已删除"""
        session_dir = self.root / user_id / str(session_id)
        if not session_dir.exists():
            return True
        try:
            # 自底向上删除文件与子目录
            for path in sorted(session_dir.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink(missing_ok=True)
                else:
                    path.rmdir()
            session_dir.rmdir()
            return True
        except OSError:
            return False
