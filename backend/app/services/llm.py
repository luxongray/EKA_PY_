"""大模型工厂

集中构建对话模型并组装调用层的重试与兜底能力：
- 主力模型 DeepSeek（开启思考模式：先推理再作答）、兜底模型通义千问；
  DeepSeek 走官方 langchain-deepseek 集成（原生支持 reasoning_content 的提取与流转），
  通义千问走通用 OpenAI 兼容接口（ChatOpenAI）；
- 重试与兜底一律复用 LangChain 内置能力（标题链用 with_retry / with_fallbacks，
  智能体的模型用 ModelRetryMiddleware / ModelFallbackMiddleware、工具用 ToolRetryMiddleware），
  不自行实现重试循环；
- 标题生成、上下文摘要等旁路任务使用「不开启思考」的 DeepSeek 实例，省时省费。

@author Luxon
@date 2026/10/10
"""

from functools import lru_cache

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI

from app.config import get_settings
from app.services.compaction import AgentContext, NotifyingSummarizationMiddleware
from app.services.prompts import SYSTEM_PROMPT, TITLE_PROMPT
from app.services.tool_events import NotifyingToolMiddleware

# 关闭客户端自带重试：重试统一上交给 LangChain 能力，避免双重退避放大等待
_CLIENT_MAX_RETRIES = 0


def _build_deepseek_model(
    base_url: str, api_key: str, model: str, *, thinking: bool
) -> ChatDeepSeek:
    """构建 DeepSeek 对话模型实例

    :param base_url: 服务地址（DeepSeek OpenAI 兼容端点）
    :param api_key: 访问密钥
    :param model: 模型名
    :param thinking: 是否开启思考模式；开启后模型先输出推理过程（reasoning_content）再作答，
                     思考内容由 langchain-deepseek 提取进 additional_kwargs，供上层推送前端
    :return: 可用于同步/流式调用的 DeepSeek 对话模型
    """
    settings = get_settings()
    # 思考模式参数：reasoning_effort 控制推理强度，extra_body.thinking 显式开启思考
    thinking_kwargs: dict = (
        {"reasoning_effort": "high", "extra_body": {"thinking": {"type": "enabled"}}}
        if thinking
        else {}
    )
    return ChatDeepSeek(
        model=model,
        base_url=base_url,
        api_key=api_key,
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout_seconds,
        max_retries=_CLIENT_MAX_RETRIES,
        **thinking_kwargs,
    )


def _build_openai_compatible_model(base_url: str, api_key: str, model: str) -> ChatOpenAI:
    """构建通用 OpenAI 兼容对话模型实例（兜底模型用，不带思考模式）

    :param base_url: 服务地址（如通义千问 OpenAI 兼容端点）
    :param api_key: 访问密钥
    :param model: 模型名
    :return: 可用于同步/流式调用的对话模型
    """
    settings = get_settings()
    return ChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=api_key,
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout_seconds,
        max_retries=_CLIENT_MAX_RETRIES,
    )


@lru_cache
def get_primary_model() -> ChatDeepSeek:
    """主力问答模型（DeepSeek，开启思考）：进程内单例，供智能体问答使用"""
    settings = get_settings()
    return _build_deepseek_model(
        settings.llm_primary_base_url,
        settings.llm_primary_api_key,
        settings.llm_primary_model,
        thinking=True,
    )


@lru_cache
def get_utility_model() -> ChatDeepSeek:
    """旁路任务模型（DeepSeek，不开启思考）：供标题生成、上下文摘要等使用，省时省费"""
    settings = get_settings()
    return _build_deepseek_model(
        settings.llm_primary_base_url,
        settings.llm_primary_api_key,
        settings.llm_primary_model,
        thinking=False,
    )


@lru_cache
def get_fallback_model() -> ChatOpenAI:
    """兜底模型（通义千问）：主力模型重试耗尽后接管，走通用 OpenAI 兼容接口"""
    settings = get_settings()
    return _build_openai_compatible_model(
        settings.llm_fallback_base_url, settings.llm_fallback_api_key, settings.llm_fallback_model
    )


@lru_cache
def get_title_runnable():
    """标题生成链：旁路模型失败自动切兜底模型（均为 LangChain 内置能力）"""
    settings = get_settings()
    # 内层重试：对旁路模型的瞬时失败做指数退避重试（含抖动）
    primary = get_utility_model().with_retry(
        stop_after_attempt=settings.llm_max_retries + 1,
        wait_exponential_jitter=True,
    )
    # 外层兜底：重试仍失败则改用兜底模型再跑一次
    return primary.with_fallbacks([get_fallback_model()])


def generate_title(first_message: str) -> str | None:
    """按首问生成会话标题

    :param first_message: 用户首个提问原文
    :return: 生成的中文标题；主备模型均失败时返回 None，由调用方兜底为截断标题
    """
    try:
        response = get_title_runnable().invoke(
            [SystemMessage(content=TITLE_PROMPT), HumanMessage(content=first_message)]
        )
        title = (getattr(response, "text", "") or "").strip()
        return title or None
    except Exception:
        # 标题属非关键路径：主备模型都异常时静默降级，不阻断建会话
        return None


@lru_cache
def get_agent():
    """构建问答智能体：挂系统提示词与重试/兜底/压缩中间件，主力模型开启思考

    :return: 编译后的智能体（可 astream 流式调用）
    """
    from langchain.agents import create_agent
    from langchain.agents.middleware import (
        ModelFallbackMiddleware,
        ModelRetryMiddleware,
        ToolRetryMiddleware,
    )

    # 延迟导入工具：避免 llm -> chat_session_service -> chat_file_service -> file_tools 的模块级循环导入
    from app.services.file_tools import FILE_TOOLS
    from app.services.rag_tools import RAG_TOOLS
    from app.services.sql_tools import SQL_TOOLS

    settings = get_settings()
    return create_agent(
        model=get_primary_model(),
        tools=[*FILE_TOOLS, *SQL_TOOLS, *RAG_TOOLS],
        system_prompt=SYSTEM_PROMPT,
        context_schema=AgentContext,
        middleware=[
            # 兜底在外层：主力模型重试全部失败后，改用兜底模型再跑一次
            ModelFallbackMiddleware(get_fallback_model()),
            # 重试在内层：针对主力模型自身的瞬时失败做指数退避重试，耗尽后抛错交外层兜底
            ModelRetryMiddleware(
                max_retries=settings.llm_max_retries,
                backoff_factor=2.0,
                initial_delay=1.0,
                max_delay=30.0,
                jitter=True,
                on_failure="error",
            ),
            # 工具调用事件：执行前后各发一条 custom 事件（放在重试外层，保证一次调用只发一对）
            NotifyingToolMiddleware(),
            # 工具调用失败重试：内置 ToolRetryMiddleware，重试耗尽后把错误交回模型处理
            # （on_failure="continue" 与 EKA 一致：单个工具失败不中断本轮对话）
            ToolRetryMiddleware(
                max_retries=settings.tool_max_retries,
                backoff_factor=2.0,
                initial_delay=0.5,
                max_delay=10.0,
                jitter=True,
                on_failure="continue",
            ),
            # 会话压缩：复用内置 SummarizationMiddleware，并额外把压缩过程推给前端。
            # 摘要属旁路任务，用不开启思考的实例，避免压缩本身又触发一轮推理
            NotifyingSummarizationMiddleware(
                get_utility_model(),
                trigger=("tokens", settings.compaction_trigger_tokens),
                keep=("messages", settings.compaction_keep_messages),
            ),
        ],
    )
