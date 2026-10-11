"""EKA Python 后端全局配置

所有可变设置（数据库连接、存储目录、端口等）集中由本模块从环境变量 / .env 读取，
业务代码一律通过 get_settings() 取值，不在代码里硬编码连接信息。

@author Luxon
@date 2026/10/10
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（EKA_PY）：用于把配置里的相对路径解析为绝对路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """应用配置：字段均可由环境变量或项目根下的 .env 文件覆盖"""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 服务监听端口（默认 8080，顶替原 Java 后端，前端无需改动）
    app_port: int = 8080

    # 开发期热重载开关：开启时 uvicorn 只监视 app 目录并自动重启；生产环境建议在 .env 里设为 false
    app_reload: bool = True

    # MySQL 连接信息（Python 版使用自己独立的库，与原 Java 后端及其数据完全隔离）
    db_host: str = "localhost"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "enterprise_knowledge_agent_py"

    # 会话文件落盘根目录：{userId}/{sessionId}/{fileId}.{ext}
    # 位于本仓库（EKA_PY）之下，与原 Java 版的磁盘目录天然隔离
    session_root: str = "Edit-File"

    # 档案原文根目录：ek_ar_file.file_path 存的是相对此目录的路径（本仓库的 File/ 源文件库）
    archive_root: str = "File"

    # 数据字典根目录：每张业务表一个「表名.md」，供数据库工具查阅表语义（本仓库的 MD/ 目录）
    md_root: str = "MD"

    # 无鉴权模式下新上传文件默认归属的用户 ID（历史数据 userId 多为 "1"）
    default_user_id: str = "1"

    # ---- 数据库 SQL 工具（search_database_context / execute_sql）----
    # 业务表语义 MD 根目录：每张表一个「表名.md」，供数据库工具查阅表语义（数据目录不入库，需本地自备）
    # 默认指向本仓库内的 MD/ 目录（相对 backend/ 根解析）
    sql_md_root: str = "MD"

    # 本库名：SQL 中显式跨库（db.table 且 db 非本库）直接拒绝
    sql_database: str = "enterprise_knowledge_agent_py"

    # 单次查询最多返回行数：超限截断并注明，防止结果撑爆上下文
    sql_max_rows: int = 100

    # 单次查询超时秒数：交给 MySQL 的 MAX_EXECUTION_TIME 掐断慢查询
    sql_timeout_seconds: int = 4

    # 表清单缓存有效期（秒）：库内新增表经一个缓存周期后自动纳入白名单
    sql_table_cache_seconds: int = 300

    # ---- RAG 混合检索工具（rag_search）----
    # ES 连接：向量路与关键词路共用同一个 ES 实例与索引（复用原 Java 版已建好的 eka-rag）
    es_url: str = "http://127.0.0.1:9200"
    es_index: str = "eka-rag"
    # ES 单次请求超时秒数：防止 ES 无响应时请求永久挂起占住线程
    es_timeout_seconds: int = 5

    # 向量化：DashScope OpenAI 兼容模式（与 Java 版同一条链路）
    rag_embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1/"
    # 向量化密钥：留空时回退到兜底对话模型的密钥（同一把 DashScope Key）
    rag_embedding_api_key: str = ""
    rag_embedding_model: str = "text-embedding-v3"
    # 向量维度：与 Java 版写入 eka-rag 的维度保持一致（text-embedding-v3 = 1024）
    rag_embedding_dimensions: int = 1024

    # 向量路召回条数：候选池要比重排目标大，给重排序留足挑选空间
    rag_vector_top_k: int = 20

    # 关键词路召回条数（ES BM25 精准检索）
    rag_keyword_top_k: int = 20

    # 重排序：DashScope text-rerank 原生端点（qwen3-vl-rerank 不支持 OpenAI 兼容模式）
    rag_rerank_base_url: str = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
    # 重排序密钥：留空时回退到兜底对话模型的密钥（同一把 DashScope Key）
    rag_rerank_api_key: str = ""
    rag_rerank_model: str = "qwen3-vl-rerank"
    # 重排序后最终返回条数
    rag_rerank_top_n: int = 10
    # 重排序调用超时秒数：作为底层兜底，超时即失败，由工具重试中间件继续重试
    rag_rerank_timeout_seconds: int = 10

    # 允许跨域访问的前端来源，逗号分隔
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # 主力对话模型：DeepSeek 官方 OpenAI 兼容接口（流式输出，与原 Java 版一致）
    llm_primary_base_url: str = "https://api.deepseek.com/v1"
    llm_primary_api_key: str = ""
    llm_primary_model: str = "deepseek-v4-flash"

    # 兜底对话模型：阿里云百炼 DashScope 通义千问 OpenAI 兼容接口
    llm_fallback_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1/"
    llm_fallback_api_key: str = ""
    llm_fallback_model: str = "qwen3.7-flash"

    # 模型通用采样参数：温度越低越稳定，适合档案问答这类严谨场景
    llm_temperature: float = 0.3

    # 单次模型调用超时秒数（含建连与生成），超时即触发重试
    llm_timeout_seconds: int = 120

    # 模型调用失败的最大重试次数（不含首次），重试耗尽后切换兜底模型
    llm_max_retries: int = 2

    # 工具调用失败的最大重试次数（不含首次），重试耗尽后把错误交回模型处理
    tool_max_retries: int = 2

    # 会话压缩触发阈值（token）：上下文用量超过该值即触发历史压缩（摘要 + 保留最近原文）
    compaction_trigger_tokens: int = 4000

    # 压缩后保留的最近消息条数：更早的消息被摘要替代
    compaction_keep_messages: int = 20

    @property
    def database_url(self) -> str:
        """拼出 SQLAlchemy 使用的 PyMySQL 连接串"""
        return (
            f"mysql+pymysql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}?charset=utf8mb4"
        )

    @property
    def session_root_path(self) -> Path:
        """会话文件根目录的绝对路径：相对路径基于项目根目录解析"""
        path = Path(self.session_root)
        return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()

    @property
    def archive_root_path(self) -> Path:
        """档案原文根目录的绝对路径：相对路径基于项目根目录解析"""
        path = Path(self.archive_root)
        return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()

    @property
    def sql_md_root_path(self) -> Path:
        """业务表语义 MD 根目录的绝对路径：相对路径基于项目根目录解析"""
        path = Path(self.sql_md_root)
        return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()

    @property
    def md_root_path(self) -> Path:
        """数据字典根目录的绝对路径：相对路径基于项目根目录解析"""
        path = Path(self.md_root)
        return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()

    @property
    def cors_origin_list(self) -> list[str]:
        """把逗号分隔的来源切成列表，供 CORS 中间件使用"""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """获取配置单例：整个进程只解析一次 .env"""
    return Settings()
