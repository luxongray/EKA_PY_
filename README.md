# 政务档案利用与分析智能体（EKA · Python 版）

> 一个面向政务档案工作人员的档案利用与分析智能体。

EKA 把「政务档案原文 / 业务数据」作为知识来源，帮助档案工作人员查阅档案原文、理解政策依据、统计分析档案数据，并在此基础上完成公文、材料等文档的撰写与加工。

> 本仓库是 EKA 的 **Python 重写版**，与原 Java 版（`EKA_`）在代码、数据库与依赖上完全独立、互不影响。

---

## 📁 目录结构

```
EKA_PY_/
├── backend/                # 后端：FastAPI + LangChain（端口 8080）
│   └── app/
│       ├── routers/        # REST / SSE 接口层（agent、chat_memory、chat_file）
│       ├── services/       # 业务逻辑（模型、RAG、SQL、文件、压缩、工具）
│       ├── models/         # SQLAlchemy ORM 模型
│       └── common/         # 统一返回体与全局异常处理
└── frontend/               # 前端：Next.js 对话界面（端口 5173）
    └── src/
        ├── providers/      # 对接自研后端的数据层（Eka / Thread）
        ├── lib/            # 接口封装、历史消息解析
        ├── components/     # 聊天、会话侧边栏、消息气泡等组件
        └── app/            # 页面
```

> 说明：**数据字典目录（`MD/`）、档案原文目录（`File/`）、会话文件目录（`Edit-File/`）与 `.env` 均未纳入版本库**，需按下方说明在本地自行准备。

---

## 🚀 快速开始

### 1. 环境要求

| 组件 | 版本 / 说明 |
|---|---|
| Python | 3.12+ |
| uv | 最新版（用于创建虚拟环境与安装依赖） |
| Node.js | 20+ |
| pnpm | 10+（`corepack enable` 后可自动获取） |
| MySQL | 8.x |
| Elasticsearch | 8.x，需安装 **IK 中文分词器** |
| 模型 Key | 阿里云 DashScope API Key、DeepSeek API Key |

### 2. 准备基础设施

1. **建库建表**：创建数据库 `enterprise_knowledge_agent_py`（`utf8mb4`），并按 `backend/app/models/` 中的 ORM 定义建立核心表：
   - 会话记忆：`ai_chat_session`、`ai_chat_message`、`ai_chat_file`
   - 档案业务：`ek_ar_file`
2. **启动 Elasticsearch** 并安装 IK 分词器，向量索引沿用默认名 `eka-rag`。
3. **准备运行时目录**（与后端工作目录相对，未纳入版本库）：
   - `MD/`：数据字典，每张业务表一份「表名.md」语义说明，供 SQL 工具理解表结构；
   - `File/`：档案原文存放根目录；
   - `Edit-File/`：会话文件落盘根目录。

### 3. 配置后端

复制 `backend/.env.example` 为 `backend/.env`（该文件不入库），填入自己的数据库连接与模型密钥：

```bash
cd backend
cp .env.example .env      # Windows PowerShell: Copy-Item .env.example .env
```

关键项（完整清单见 `.env.example`）：

```dotenv
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=你的数据库密码
DB_NAME=enterprise_knowledge_agent_py

LLM_PRIMARY_API_KEY=你的 DeepSeek Key
LLM_FALLBACK_API_KEY=你的 DashScope Key
```

### 4. 启动后端

```bash
cd backend
uv sync                        # 创建 .venv 并安装依赖
uv run python -m app.main      # 开发运行（热重载），监听 8080
```

服务启动后监听 `http://localhost:8080`，健康检查：`GET /health`。

### 5. 启动前端

```bash
cd frontend
pnpm install

# 配置后端地址：复制 .env.example 为 .env.local，按需修改
# NEXT_PUBLIC_API_URL=http://localhost:8080

pnpm dev          # 打开 http://localhost:5173
# 或 pnpm dev:open（自动打开浏览器）
```

---

## 🙏 致谢与许可证

- 前端 `frontend` 基于 [langchain-ai/agent-chat-ui](https://github.com/langchain-ai/agent-chat-ui) 二次开发，沿用其 MIT 协议（见 `frontend/LICENSE`）。
- 后端由 [FastAPI](https://github.com/fastapi/fastapi)、[LangChain](https://github.com/langchain-ai/langchain) 等开源项目驱动。
