"""EKA Python 后端入口

组装 FastAPI 应用：注册 CORS、全局异常兜底与会话记忆/会话文件两组路由；
默认监听 8080，顶替原 Java 后端供前端直接调用。

@author Luxon
@date 2026/10/10
"""

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.common.exceptions import register_exception_handlers
from app.config import PROJECT_ROOT, get_settings
from app.routers import agent, chat_file, chat_memory

settings = get_settings()

app = FastAPI(title="EKA Python 后端", version="0.1.0")

# 跨域：放行前端 5173，允许携带 Cookie
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局异常兜底：业务异常与未处理异常统一转 Result 返回体
register_exception_handlers(app)

# 业务路由
app.include_router(chat_memory.router)
app.include_router(chat_file.router)
app.include_router(agent.router)


@app.get("/health")
def health():
    """健康检查：供联调时确认服务已就绪"""
    return {"code": 200, "message": "请求成功", "data": None}


def main() -> None:
    """本地启动入口：python -m app.main

    开发期默认开启热重载，且只监视 app 目录（避免把 .venv 等无关目录纳入监视范围），
    并把重启去抖延迟调大，减少连续保存多个文件时反复重启；需要关闭热重载时把
    环境变量 EKA_RELOAD 设为 false。
    """
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.app_port,
        reload=settings.app_reload,
        reload_dirs=[str(PROJECT_ROOT / "app")] if settings.app_reload else None,
        reload_delay=0.5,
    )


if __name__ == "__main__":
    main()
