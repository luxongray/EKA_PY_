"""知识库混合检索工具（LangChain 原生 @tool）

用 LangChain 原生 `@tool` 定义 rag_search，底层各步骤尽量复用生态原生件：

- 向量化：`langchain_openai.OpenAIEmbeddings` 指向 DashScope OpenAI 兼容端点（与 Java 版同一条链路）；
- 向量召回：`langchain_elasticsearch.ElasticsearchStore`（官方集成）的 kNN 近邻检索，档案条件作为
  kNN 的 filter 下推预过滤；
- 关键词召回：`langchain_elasticsearch.ElasticsearchRetriever`，用 `body_func` 直接给出 ES 查询体
  （bool.must 承载 BM25 match，filter 数组承载档案等值条件）；
- 重排序：`httpx` 直连 DashScope text-rerank 原生端点（该模型不支持 OpenAI 兼容模式，
  生态里的 DashScopeRerank 又把模型写死，故自行发起 HTTP，请求体与 Java 版一致）；
- 合并去重、结果格式化：业务逻辑，自行实现，与 Java 版逐条对齐。

工具与参数的对外描述严格照搬原 Java 版 RagSearchTool 的 @Tool / @P 文案，保证模型看到的提示词一致。
复用原 Java 版已建好的 `eka-rag` 索引：其字段名（text / vector / metadata）与 LangChain 的
ElasticsearchStore 默认字段同名，可直接读取、无需重新入库；档案过滤维度（projectName /
volumeName / itemName / formYear）与 Java 版写入的 metadata 子字段一一对应。

工具失败直接抛 BusinessException，由已挂的 ToolRetryMiddleware 重试、耗尽后把错误交回模型处理。

@author Luxon
@date 2026/10/10
"""

import logging
from functools import lru_cache
from typing import Annotated, Mapping

import httpx
from elasticsearch import Elasticsearch
from langchain.tools import tool
from langchain_core.documents import Document
from langchain_elasticsearch import ElasticsearchRetriever, ElasticsearchStore
from langchain_openai import OpenAIEmbeddings
from pydantic import BaseModel, Field

from app.common.exceptions import BusinessException
from app.config import get_settings

logger = logging.getLogger(__name__)

# 召回来源标识：与 Java 版 RagSearchTool 的 origin 文案一致
SOURCE_VECTOR = "向量检索"
SOURCE_KEYWORD = "精准检索"

# ES 正文块字段与向量字段：与 eka-rag 索引结构一致（langchain4j 默认命名）
TEXT_FIELD = "text"
VECTOR_FIELD = "vector"

# 无命中时的统一提示
EMPTY_RESULT = "未检索到相关内容"


class ArchiveFilter(BaseModel):
    """档案身份过滤条件（rag_search 的 filter 入参模型）

    按归属工程项目、卷、件、归档年度对检索结果做精确等值过滤，四个字段全部可空，
    留空（None 或空白串）的字段不参与过滤；对象整体为空时等同于不过滤。
    字段与 eka-rag 元数据键一一对应（project_name / volume_name / item_name / form_year）。

    @author Luxon
    @date 2026/10/10
    """

    # 归属工程项目名（精确匹配，对应元数据键 project_name），留空不过滤
    projectName: str | None = Field(default=None, description="归属工程项目名，精确匹配，留空不过滤")
    # 卷名（精确匹配，对应元数据键 volume_name），留空不过滤
    volumeName: str | None = Field(default=None, description="卷名，精确匹配，留空不过滤")
    # 件名（精确匹配，对应元数据键 item_name），留空不过滤
    itemName: str | None = Field(default=None, description="件名，精确匹配，留空不过滤")
    # 归档年度（精确匹配，对应元数据键 form_year，按年份数字比较），留空不过滤
    formYear: str | None = Field(default=None, description="归档年度，精确匹配，数字年份如 2023，留空不过滤")


@tool
def rag_search(
    originalQuery: Annotated[str, "用于结果重排序的查询语义"],
    vectorQuery: Annotated[str | None, "向量语义检索的查询语句"] = None,
    esQuery: Annotated[str | None, "ES 关键词精准检索词"] = None,
    filter: Annotated[ArchiveFilter | None, "档案过滤条件（项目/卷/件/归档年度，可空对象即不过滤）"] = None,
) -> str:
    """文档混合检索工具（检索知识库中的档案原文与政策内容）。
    通过语义检索与关键词检索进行多路召回，并对召回结果进行重排序，
    返回与当前问题最相关的文档内容。
    【职责范围】本工具负责回答需要原文佐证的内容类问题：档案原文、政策依据、条款表述、
    办法/方案的具体内容（如“有哪些政策”“补偿标准是多少”“文件怎么规定的”“什么流程”）。
    调用本工具时，vectorQuery 和 esQuery 至少提供一个。
    当需要限定查询范围时，可提供 filter 档案过滤条件（按归属项目/卷/件/归档年度精确过滤，
    四者均可不填，留空的维度不限制；已知用户所属项目时可优先用 filter.projectName 圈定范围）。
    """
    # 步骤 1：参数校验——两路召回至少要有一路有查询，全空则无法召回任何候选
    has_vector = bool(vectorQuery and vectorQuery.strip())
    has_keyword = bool(esQuery and esQuery.strip())
    if not has_vector and not has_keyword:
        raise BusinessException("vectorQuery 与 esQuery 至少需要提供一个")

    # 步骤 2：把档案过滤条件翻译为 ES filter 子句，供两路召回下推（等价 SQL 的 WHERE 语义）
    filter_clauses = _build_filter_clauses(filter)
    settings = get_settings()

    # 步骤 3：向量路召回（query 为空则跳过），档案条件下推为 kNN 的 filter 预过滤
    vector_hits = (
        _vector_search(vectorQuery, filter_clauses, settings.rag_vector_top_k)
        if has_vector
        else []
    )

    # 步骤 4：关键词路召回（query 为空则跳过），档案条件下推为 bool.filter
    keyword_hits = (
        _keyword_search(esQuery, filter_clauses, settings.rag_keyword_top_k)
        if has_keyword
        else []
    )

    # 步骤 5：两路候选合并去重（向量命中优先保序）
    candidates = _deduplicate(vector_hits, keyword_hits)

    # 步骤 6：送重排序模型打分取 Top N；originalQuery 为空时按向量查询、关键词查询依次兜底
    rerank_query = (originalQuery or "").strip() or (vectorQuery or esQuery or "")
    top = _rerank(rerank_query, candidates)

    # 步骤 7：组装面向模型的检索结果文本
    return _format_for_llm(top)


# 交给 create_agent 的检索工具集合
RAG_TOOLS = [rag_search]


@lru_cache
def _get_es_client() -> Elasticsearch:
    """构建并复用 ES 客户端（全进程单例）

    :return: 指向配置 ES 地址的客户端，单次请求超时由配置控制
    """
    settings = get_settings()
    return Elasticsearch(settings.es_url, request_timeout=settings.es_timeout_seconds)


@lru_cache
def _get_embeddings() -> OpenAIEmbeddings:
    """构建并复用查询词向量化模型（DashScope OpenAI 兼容模式）

    :return: 向量化模型；未配置密钥时抛业务异常
    :raises BusinessException: 未配置任何可用的 DashScope 密钥
    """
    settings = get_settings()
    # 向量化密钥缺省时回退到兜底对话模型的密钥（同一把 DashScope Key）
    api_key = settings.rag_embedding_api_key or settings.llm_fallback_api_key
    if not api_key:
        raise BusinessException("未配置 DashScope API Key，无法进行向量化检索")
    # 关闭客户端自带重试，重试统一交给工具重试中间件，避免双重退避放大等待；
    # 关闭本地分词长度校验——DashScope 兼容端点不经 tiktoken，客户端本地分词会与远端不一致
    return OpenAIEmbeddings(
        model=settings.rag_embedding_model,
        base_url=settings.rag_embedding_base_url,
        api_key=api_key,
        dimensions=settings.rag_embedding_dimensions,
        check_embedding_ctx_length=False,
        max_retries=0,
    )


@lru_cache
def _get_vector_store() -> ElasticsearchStore:
    """构建并复用 ES 向量库封装（官方集成，默认字段 vector/text）

    :return: 指向 eka-rag 索引的向量库封装
    """
    settings = get_settings()
    return ElasticsearchStore(
        index_name=settings.es_index,
        embedding=_get_embeddings(),
        client=_get_es_client(),
        vector_query_field=VECTOR_FIELD,
        query_field=TEXT_FIELD,
    )


def _build_filter_clauses(filter: ArchiveFilter | None) -> list[dict]:
    """把档案过滤条件翻译为 ES filter 子句数组

    字符串维度匹配 metadata 各字段的 .keyword 子字段（等值精确匹配），
    归档年度解析为数字后匹配 form_year 数值字段；留空维度不生成子句。

    :param filter: 档案过滤条件，可为 None 或全空（均表示不过滤）
    :return: ES filter 子句列表；无可过滤维度时返回空列表
    :raises BusinessException: 归档年度不是数字时
    """
    if filter is None:
        return []

    clauses: list[dict] = []
    # 步骤 1：三个字符串维度逐一生成 term 子句（查 .keyword 子字段做精确匹配）
    if filter.projectName and filter.projectName.strip():
        clauses.append({"term": {"metadata.project_name.keyword": filter.projectName.strip()}})
    if filter.volumeName and filter.volumeName.strip():
        clauses.append({"term": {"metadata.volume_name.keyword": filter.volumeName.strip()}})
    if filter.itemName and filter.itemName.strip():
        clauses.append({"term": {"metadata.item_name.keyword": filter.itemName.strip()}})

    # 步骤 2：归档年度解析为数字——form_year 在 ES 是数值字段，字符串无法命中
    if filter.formYear and filter.formYear.strip():
        try:
            year = int(filter.formYear.strip())
        except ValueError as exc:
            raise BusinessException(
                f"归档年度 formYear 必须是数字年份（如 2023），实际收到：{filter.formYear}"
            ) from exc
        clauses.append({"term": {"metadata.form_year": year}})
    return clauses


def _vector_search(
    vector_query: str, filter_clauses: list[dict], top_k: int
) -> list[dict]:
    """向量路召回：查询词向量化后在 ES 做 kNN 近邻检索

    :param vector_query: 向量语义查询词，调用方保证非空
    :param filter_clauses: 档案过滤子句（下推为 kNN 的 filter 预过滤），可为空
    :param top_k: 召回条数
    :return: 统一候选结构列表，按向量相似度降序
    """
    # 步骤 1：调用向量库的相似度检索，档案条件作为 filter 下推（先过滤再近邻）
    pairs = _get_vector_store().similarity_search_with_score(
        vector_query, k=top_k, filter=filter_clauses or None
    )

    # 步骤 2：转为统一候选结构——Document 的 metadata 即 ES 里的业务元数据（id/page/section 等）
    candidates: list[dict] = []
    for doc, score in pairs:
        metadata = dict(doc.metadata or {})
        candidates.append(
            {
                "id": metadata.get("id"),
                "text": doc.page_content,
                "metadata": metadata,
                "origin": SOURCE_VECTOR,
                "score": score,
            }
        )
    return candidates


def _keyword_search(
    es_query: str, filter_clauses: list[dict], top_k: int
) -> list[dict]:
    """关键词路召回：对 text 字段做 BM25 match 检索，档案条件叠加为 bool.filter

    :param es_query: 关键词查询词，调用方保证非空
    :param filter_clauses: 档案过滤子句，可为空
    :param top_k: 召回条数
    :return: 统一候选结构列表，按 BM25 分数降序
    """
    settings = get_settings()

    # 步骤 1：用原生检索器承载自定义 ES 查询体（bool.must 承载 match，filter 承载等值条件）
    def body_func(query: str) -> dict:
        return {
            "size": top_k,
            "query": {
                "bool": {
                    "must": [{"match": {TEXT_FIELD: query}}],
                    "filter": filter_clauses,
                }
            },
        }

    retriever = ElasticsearchRetriever(
        index_name=settings.es_index,
        body_func=body_func,
        document_mapper=_map_keyword_hit,
        client=_get_es_client(),
    )

    # 步骤 2：执行检索并转为统一候选结构（document_mapper 已把业务元数据与 _score 放进 metadata）
    candidates: list[dict] = []
    for doc in retriever.invoke(es_query):
        metadata = dict(doc.metadata or {})
        candidates.append(
            {
                "id": metadata.get("id"),
                "text": doc.page_content,
                "metadata": metadata,
                "origin": SOURCE_KEYWORD,
                "score": metadata.get("_score"),
            }
        )
    return candidates


def _map_keyword_hit(hit: Mapping) -> Document:
    """把一条 ES 命中映射为 LangChain Document

    正文取 text 字段，元数据取 _source.metadata（业务字段），并把 BM25 分数
    _score 一并放进元数据，供上层按统一结构取用。

    :param hit: ES 单条命中
    :return: 承载正文与业务元数据的 Document
    """
    source = hit.get("_source", {}) or {}
    metadata = dict(source.get("metadata", {}) or {})
    metadata["_score"] = hit.get("_score")
    return Document(page_content=source.get(TEXT_FIELD, ""), metadata=metadata)


def _deduplicate(vector_hits: list[dict], keyword_hits: list[dict]) -> list[dict]:
    """两路候选按业务 id 去重合并（向量命中优先保序）

    :param vector_hits: 向量命中候选（已按相似度降序）
    :param keyword_hits: 关键词命中候选（已按 BM25 分数降序）
    :return: 去重后的候选列表，先向量命中后关键词命中
    """
    # 按插入顺序去重：同一 id 后到者丢弃，保证向量命中优先
    merged: dict[str, dict] = {}
    for candidate in (*vector_hits, *keyword_hits):
        merged.setdefault(candidate.get("id"), candidate)
    return list(merged.values())


def _rerank(query: str, candidates: list[dict]) -> list[dict]:
    """调用 DashScope 重排序模型对候选打分取 Top N

    :param query: 重排序的查询语义
    :param candidates: 去重后的候选列表
    :return: 重排序后的 Top N 候选，按相关度降序；候选为空时返回空列表
    :raises BusinessException: 调用失败（网络异常或非 200 响应）
    """
    # 步骤 1：候选为空时直接返回，避免发起无意义的重排请求
    if not candidates:
        return []

    settings = get_settings()
    api_key = settings.rag_rerank_api_key or settings.llm_fallback_api_key
    if not api_key:
        raise BusinessException("未配置 DashScope API Key，无法进行重排序")

    # 步骤 2：构造请求体——纯文本 query 用 {"text": ...}；return_documents=false 由本地回填原文
    body = {
        "model": settings.rag_rerank_model,
        "input": {
            "query": {"text": query},
            "documents": [{"text": candidate["text"]} for candidate in candidates],
        },
        "parameters": {
            "top_n": settings.rag_rerank_top_n,
            "return_documents": False,
        },
    }

    # 步骤 3：发起 HTTP 调用，非 200 视为失败并携带响应体抛出
    try:
        response = httpx.post(
            settings.rag_rerank_base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=settings.rag_rerank_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        raise BusinessException(f"重排序调用失败：{exc}") from exc
    if response.status_code != 200:
        raise BusinessException(
            f"重排序调用失败 HTTP {response.status_code}: {response.text}"
        )

    # 步骤 4：按返回的 index 映射回本地候选，用 relevance_score 覆盖召回阶段分数
    results = response.json().get("output", {}).get("results", [])
    top: list[dict] = []
    for result in results:
        candidate = dict(candidates[result["index"]])
        candidate["score"] = result.get("relevance_score")
        top.append(candidate)
    return top


def _format_for_llm(candidates: list[dict]) -> str:
    """把重排后的候选格式化为面向模型的文本

    :param candidates: 重排后的 Top N 候选
    :return: 逐条含相关度/来源/档案身份/正文的文本；无命中时返回提示语
    """
    # 步骤 1：空结果提示——明确告诉模型本次检索无命中
    if not candidates:
        return EMPTY_RESULT

    # 步骤 2：逐条输出：头行（序号 + 相关度 + 来源）、块结构行、身份行、正文
    lines: list[str] = []
    for index, candidate in enumerate(candidates, start=1):
        metadata = candidate["metadata"]
        score = candidate.get("score")
        score_text = f"{float(score):.4f}" if score is not None else "-"
        lines.append(
            f"[第{index}条 | 相关度 {score_text} | 来源: {candidate['origin']}]"
        )
        lines.append(
            f"块ID: {_text(metadata.get('id'))}"
            f" | 页码: {_text(metadata.get('page'))}"
            f" | 章节: {_text(metadata.get('section'))}"
        )
        lines.append(
            f"项目: {_text(metadata.get('project_name'))}"
            f" | 卷: {_text(metadata.get('volume_name'))}"
            f" | 件: {_text(metadata.get('item_name'))}"
            f" | 年度: {_text(metadata.get('form_year'))}"
        )
        lines.append(f"正文: {candidate['text']}")
        lines.append("")
    return "\n".join(lines).strip()


def _text(value: object) -> str:
    """把元数据值渲染为展示文本：空值统一显示为「-」"""
    return "-" if value is None or value == "" else str(value)
