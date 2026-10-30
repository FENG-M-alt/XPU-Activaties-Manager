"""统一业务错误定义（文档第 8 章错误码总表）。"""
from __future__ import annotations

from typing import Any


class ErrorCode:
    OK = 0
    PARAM_INVALID = 40001
    UNAUTHORIZED = 40100            # 未登录或令牌无效
    BAD_CREDENTIALS = 40101         # 账号或密码错误
    ACCOUNT_DISABLED = 40102        # 账号已停用
    LOGIN_LOCKED = 40103            # 登录失败次数过多
    FORBIDDEN = 40300               # 无权限访问该资源
    NOT_FOUND = 40400               # 资源不存在
    CAPACITY_FULL = 40901           # 名额已满
    DUPLICATE_SIGNUP = 40902        # 已报名该活动
    SIGNUP_WINDOW_CLOSED = 40903    # 报名未开始或已截止
    ILLEGAL_STATE = 40904           # 活动状态不允许该操作 / 版本冲突
    CHECKIN_CODE_WRONG = 40905      # 签到码错误
    CHECKIN_WINDOW_CLOSED = 40906   # 不在签到时间窗内
    NOT_APPROVED = 40907            # 报名未通过审核
    ALREADY_CHECKED_IN = 40908      # 已签到
    CHECKIN_LOCKED = 40909          # 签到尝试过于频繁
    CHECKIN_CODE_EXPIRED = 40910    # 签到码已失效
    TOO_MANY_REQUESTS = 42900       # 请求过于频繁
    INTERNAL_ERROR = 50000          # 服务器内部错误


# 业务码 -> 默认提示语
DEFAULT_MESSAGES: dict[int, str] = {
    ErrorCode.OK: "ok",
    ErrorCode.PARAM_INVALID: "参数校验失败",
    ErrorCode.UNAUTHORIZED: "未登录或登录已失效，请重新登录",
    ErrorCode.BAD_CREDENTIALS: "学号或密码错误",
    ErrorCode.ACCOUNT_DISABLED: "账号已停用，请联系管理员",
    ErrorCode.LOGIN_LOCKED: "登录失败次数过多，请稍后再试",
    ErrorCode.FORBIDDEN: "无权限访问该资源",
    ErrorCode.NOT_FOUND: "资源不存在",
    ErrorCode.CAPACITY_FULL: "名额已满",
    ErrorCode.DUPLICATE_SIGNUP: "已报名该活动",
    ErrorCode.SIGNUP_WINDOW_CLOSED: "报名未开始或已截止",
    ErrorCode.ILLEGAL_STATE: "活动状态不允许该操作",
    ErrorCode.CHECKIN_CODE_WRONG: "签到码错误，请向现场工作人员确认",
    ErrorCode.CHECKIN_WINDOW_CLOSED: "不在签到时间窗内",
    ErrorCode.NOT_APPROVED: "报名未通过审核，无法签到",
    ErrorCode.ALREADY_CHECKED_IN: "已签到，无需重复签到",
    ErrorCode.CHECKIN_LOCKED: "签到尝试过于频繁，请 10 分钟后重试",
    ErrorCode.CHECKIN_CODE_EXPIRED: "签到码已失效，请向现场工作人员确认",
    ErrorCode.TOO_MANY_REQUESTS: "请求过于频繁，请稍后重试",
    ErrorCode.INTERNAL_ERROR: "服务器内部错误，请稍后重试",
}

# 业务码 -> HTTP 状态码（文档 2.3 / 第 8 章）
DEFAULT_HTTP_STATUS: dict[int, int] = {
    ErrorCode.OK: 200,
    ErrorCode.PARAM_INVALID: 400,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.BAD_CREDENTIALS: 401,
    ErrorCode.ACCOUNT_DISABLED: 403,
    ErrorCode.LOGIN_LOCKED: 429,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CAPACITY_FULL: 409,
    ErrorCode.DUPLICATE_SIGNUP: 409,
    ErrorCode.SIGNUP_WINDOW_CLOSED: 409,
    ErrorCode.ILLEGAL_STATE: 409,
    ErrorCode.CHECKIN_CODE_WRONG: 409,
    ErrorCode.CHECKIN_WINDOW_CLOSED: 409,
    ErrorCode.NOT_APPROVED: 409,
    ErrorCode.ALREADY_CHECKED_IN: 409,
    ErrorCode.CHECKIN_LOCKED: 429,
    ErrorCode.CHECKIN_CODE_EXPIRED: 409,
    ErrorCode.TOO_MANY_REQUESTS: 429,
    ErrorCode.INTERNAL_ERROR: 500,
}


class BizError(Exception):
    """业务异常：由全局异常处理器转换为统一响应信封。"""

    def __init__(
        self,
        code: int,
        message: str | None = None,
        *,
        http_status: int | None = None,
        data: Any = None,
    ) -> None:
        self.code = code
        self.message = message or DEFAULT_MESSAGES.get(code, "请求失败")
        self.http_status = http_status or DEFAULT_HTTP_STATUS.get(code, 400)
        self.data = data
        super().__init__(self.message)


def fail(code: int, message: str | None = None, *, http_status: int | None = None, data: Any = None):
    """抛出业务异常。"""
    raise BizError(code, message, http_status=http_status, data=data)


def param_error(field: str, reason: str, message: str | None = None):
    """参数错误，data 中给出字段级错误（文档 2.9）。"""
    raise BizError(
        ErrorCode.PARAM_INVALID,
        message or f"参数校验失败：{reason}",
        data={"field": field, "reason": reason},
    )
