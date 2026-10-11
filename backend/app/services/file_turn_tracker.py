"""本轮文件变更追踪器

记录某一轮对话中被 write_file / edit_file 新建或覆盖的文件 ID，供轮次收尾时统一把这些
文件的占位符锚点回填到本轮最后一条消息。

工具在 LangChain 中可能跑在工作线程里，故用「按会话 ID 记录的全局表 + 锁」而非 ThreadLocal：
同一会话本身串行，不存在并发写冲突（与原 Java 版 ChatFileTurnTracker 一致）。

@author Luxon
@date 2026/10/10
"""

import threading
from collections import defaultdict


class _FileTurnTracker:
    """会话 ID -> 本轮被写/改文件 ID 集合的线程安全登记表"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._files: dict[int, set[int]] = defaultdict(set)

    def mark(self, session_id: int | None, file_id: int | None) -> None:
        """登记一个本轮被写/改的文件；任一为空时忽略"""
        if session_id is None or file_id is None:
            return
        with self._lock:
            self._files[session_id].add(file_id)

    def drain(self, session_id: int | None) -> set[int]:
        """取出并清空某会话本轮登记的文件集合（轮次收尾时调用，避免污染下一轮）"""
        if session_id is None:
            return set()
        with self._lock:
            return self._files.pop(session_id, set())


# 全局单例：写文件工具与轮次收尾共用
turn_tracker = _FileTurnTracker()
