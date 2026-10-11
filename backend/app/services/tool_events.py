"""工具调用事件中间件

在工具真正执行前后各发一条 custom 事件，由 agent_service 转成前端的
TOOL_CALL_REQUEST / TOOL_CALL_RESULT 帧（字段名对齐 eka-api.ts）。

放在 ToolRetryMiddleware 外层（中间件列表里更靠前）：重试只在内部进行，
事件每次工具调用只发一对，与前端「一次调用一张卡片」的展示语义一致；
参数与结果按固定长度截断，避免超长文本刷爆事件流（与原 Java 版一致）。

@author Luxon
@date 2026/10/10
"""

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.config import get_stream_writer
from langgraph.types import Command

logger = logging.getLogger(__name__)

# 事件里参数的最大字符数，超长截断只保留前部（与原 Java 版一致）
MAX_ARGUMENTS_CHARS = 500

# 事件里工具结果的最大字符数，超长截断只保留前部（与原 Java 版一致）
MAX_RESULT_CHARS = 1000


class NotifyingToolMiddleware(AgentMiddleware):
    """工具调用事件中间件：执行前后各发一条 custom 事件推给前端"""

    def wrap_tool_call(self, request, handler: Callable) -> ToolMessage | Command[Any]:
        """同步：工具执行前后各发一条事件（流式问答走异步分支，此处保持对称实现）"""
        return self._run(request, lambda req: handler(req))

    async def awrap_tool_call(
        self, request, handler: Callable[..., Awaitable[ToolMessage | Command[Any]]]
    ) -> ToolMessage | Command[Any]:
        """异步：工具执行前后各发一条事件（流式问答实际走此分支）"""
        return await self._run_async(request, handler)

    def _run(self, request, execute: Callable) -> ToolMessage | Command[Any]:
        """同步执行：发请求事件 -> 执行 -> 发结果事件"""
        call_id, tool_name, arguments = _describe(request)
        _emit_request(call_id, tool_name, arguments)
        result = execute(request)
        _emit_result(call_id, tool_name, arguments, result)
        return result

    async def _run_async(self, request, execute: Callable[..., Awaitable]) -> ToolMessage | Command[Any]:
        """异步执行：发请求事件 -> 执行 -> 发结果事件"""
        call_id, tool_name, arguments = _describe(request)
        _emit_request(call_id, tool_name, arguments)
        result = await execute(request)
        _emit_result(call_id, tool_name, arguments, result)
        return result


def _describe(request) -> tuple[str, str, str]:
    """从工具调用请求里取出（调用 ID、工具名、参数 JSON 文本）"""
    tool_call = request.tool_call or {}
    call_id = tool_call.get("id") or ""
    tool_name = tool_call.get("name") or (request.tool.name if request.tool else "")
    arguments = _truncate(_to_json_text(tool_call.get("args")), MAX_ARGUMENTS_CHARS)
    return call_id, tool_name, arguments


def _emit_request(call_id: str, tool_name: str, arguments: str) -> None:
    """推送工具调用请求事件"""
    _emit({"kind": "TOOL_CALL_REQUEST", "callId": call_id, "toolName": tool_name, "arguments": arguments})


def _emit_result(call_id: str, tool_name: str, arguments: str, result: Any) -> None:
    """推送工具调用结果事件：结果取文本后截断"""
    summary = _truncate(_result_text(result), MAX_RESULT_CHARS)
    _emit(
        {
            "kind": "TOOL_CALL_RESULT",
            "callId": call_id,
            "toolName": tool_name,
            "arguments": arguments,
            "resultSummary": summary,
        }
    )


def _result_text(result: Any) -> str:
    """把工具返回值转成可展示文本；Command（状态指令）无正文时给统一回执"""
    if isinstance(result, ToolMessage):
        content = result.content
    elif isinstance(result, Command):
        return "(工具已执行)"
    else:
        content = result
    if isinstance(content, str):
        return content
    return _to_json_text(content)


def _to_json_text(value: Any) -> str:
    """把任意值转成 JSON 文本，非字符串内容用 JSON 序列化，便于前端原样展示"""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _truncate(text: str, limit: int) -> str:
    """超长截断：只保留前 limit 个字符"""
    return text if len(text) <= limit else text[:limit]


def _emit(payload: dict) -> None:
    """向 LangGraph custom 流写一条事件；无流式上下文时静默忽略"""
    try:
        get_stream_writer()(payload)
    except Exception:
        # 不在流式执行上下文（如工具单测直接调用）时忽略，不影响工具本身
        pass
