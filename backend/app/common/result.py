"""统一接口返回体

复刻原 Java 后端的 Result：所有普通接口一律返回 {code, message, data}，
code 只写死两个值——200 成功、500 失败，具体原因看 message 中文文案。
分页接口额外复刻 MyBatis-Plus IPage 的 records/total/size/current/pages 结构。

@author Luxon
@date 2026/10/10
"""

from math import ceil
from typing import Any

# 成功码，写死
SUCCESS_CODE = 200

# 失败码，写死
FAIL_CODE = 500

# 成功默认文案
SUCCESS_MESSAGE = "请求成功"

# 失败默认文案
FAIL_MESSAGE = "请求失败"


def ok(data: Any = None, message: str | None = None) -> dict:
    """成功返回：可带业务数据，message 为空时回落默认文案"""
    return {"code": SUCCESS_CODE, "message": message or SUCCESS_MESSAGE, "data": data}


def fail(message: str | None = None, data: Any = None) -> dict:
    """失败返回：message 为中文错误原因，为空时回落默认文案"""
    return {"code": FAIL_CODE, "message": message or FAIL_MESSAGE, "data": data}


def page(records: list, total: int, current: int, size: int) -> dict:
    """分页返回：复刻前端依赖的 records/total，并补齐 IPage 其余常用字段"""
    pages = ceil(total / size) if size > 0 else 0
    return {
        "records": records,
        "total": total,
        "size": size,
        "current": current,
        "pages": pages,
    }
