"""请求体校验模型（入库前统一转换为东八区 naive 时间）。"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

from app.core.sanitize import sanitize_html
from app.core.timeutil import to_naive_local


def _parse_datetime(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return to_naive_local(value)
    if isinstance(value, (int, float)):
        return to_naive_local(datetime.fromtimestamp(value))
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return to_naive_local(datetime.fromisoformat(text))
    except ValueError as exc:
        raise ValueError("时间格式须为 ISO 8601，例如 2026-10-21T14:30:00+08:00") from exc


LocalDateTime = Annotated[datetime, BeforeValidator(_parse_datetime)]
Mobile = Annotated[str, Field(pattern=r"^1[3-9]\d{9}$")]


# ---------------------------------------------------------------- 认证
class StudentLoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    student_no: str = Field(min_length=6, max_length=20, pattern=r"^[A-Za-z0-9]+$")
    password: str = Field(min_length=1, max_length=64)


class AdminLoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    username: str = Field(min_length=2, max_length=50)
    password: str = Field(min_length=1, max_length=64)
    captcha_id: str = Field(min_length=1, max_length=64)
    captcha_code: str = Field(min_length=1, max_length=8)


class ChangePasswordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    old_password: str = Field(min_length=1, max_length=64)
    new_password: str = Field(min_length=8, max_length=20)


class UpdateMeIn(BaseModel):
    """仅允许修改手机号；提交其他字段返回 40001（文档 3.5）。"""

    model_config = ConfigDict(extra="forbid")

    mobile: Mobile


# ---------------------------------------------------------------- 活动
class ActivityCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=60)
    type_id: int | None = Field(default=None, ge=1)
    host: str | None = Field(default=None, max_length=100)
    organizer: str | None = Field(default=None, max_length=100)
    location: str | None = Field(default=None, max_length=200)
    cover_url: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=5000)
    notice: str | None = Field(default=None, max_length=2000)
    contact_name: str | None = Field(default=None, max_length=50)
    contact_phone: str | None = Field(default=None, max_length=50)
    college: str | None = Field(default=None, max_length=100)

    signup_start: LocalDateTime | None = None
    signup_end: LocalDateTime | None = None
    start_time: LocalDateTime | None = None
    end_time: LocalDateTime | None = None

    capacity: int | None = Field(default=None, ge=1, le=9999)
    need_audit: bool | None = None
    allow_cancel: bool | None = None
    hours: Decimal | None = Field(default=None, ge=0, le=16, decimal_places=1)
    checkin_before: int | None = Field(default=None, ge=0, le=180)
    checkin_after: int | None = Field(default=None, ge=0, le=180)

    @field_validator("description")
    @classmethod
    def _clean_description(cls, v: str | None) -> str | None:
        return sanitize_html(v, max_length=5000)

    @field_validator("notice")
    @classmethod
    def _clean_notice(cls, v: str | None) -> str | None:
        return sanitize_html(v, max_length=2000)


class ActivityUpdateIn(ActivityCreateIn):
    """部分更新：version 必填（乐观锁）。"""

    title: str | None = Field(default=None, min_length=1, max_length=60)  # type: ignore[assignment]
    version: int = Field(ge=1)


class ActivityReasonIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=1, max_length=500)


class ActivityCancelIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=5, max_length=500)


class ResetCodeIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=1, max_length=500)


class BatchAuditIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: list[int] = Field(min_length=1, max_length=200)
    action: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=500)


class AuditIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str | None = Field(default=None, max_length=500)


class ManualCheckinIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    checkin_at: LocalDateTime | None = None
    reason: str = Field(min_length=5, max_length=500)


class CheckinIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(min_length=1, max_length=6, pattern=r"^\d{1,6}$")


# ---------------------------------------------------------------- 学生
class StudentPatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: Literal["ACTIVE", "DISABLED"] | None = None
    reset_password: bool | None = None
    mobile: str | None = Field(default=None, pattern=r"^1[3-9]\d{9}$")


class ImportConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duplicate_strategy: Literal["skip", "update"] = "skip"


# ---------------------------------------------------------------- 系统
class SettingItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=100)
    value: str | None = Field(default=None, max_length=500)


class SettingsUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[SettingItemIn] = Field(min_length=1, max_length=100)


class DictItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    dict_type: str = Field(min_length=1, max_length=50)
    label: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=100)
    sort: int = Field(default=0, ge=0, le=9999)
    is_active: bool = True
    remark: str | None = Field(default=None, max_length=255)


class DictItemPatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    label: str | None = Field(default=None, min_length=1, max_length=100)
    value: str | None = Field(default=None, min_length=1, max_length=100)
    sort: int | None = Field(default=None, ge=0, le=9999)
    is_active: bool | None = None
    remark: str | None = Field(default=None, max_length=255)


class AdminCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=8, max_length=20)
    role: Literal["ADMIN", "SUPER_ADMIN"] = "ADMIN"
    data_scope: Literal["ALL", "COLLEGE", "SELF"] = "SELF"
    college: str | None = Field(default=None, max_length=100)
    status: Literal["ACTIVE", "DISABLED"] = "ACTIVE"


class AdminPatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=50)
    password: str | None = Field(default=None, min_length=8, max_length=20)
    role: Literal["ADMIN", "SUPER_ADMIN"] | None = None
    data_scope: Literal["ALL", "COLLEGE", "SELF"] | None = None
    college: str | None = Field(default=None, max_length=100)
    status: Literal["ACTIVE", "DISABLED"] | None = None


class NoticeIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=5000)
    is_pinned: bool = False
    start_at: LocalDateTime | None = None
    end_at: LocalDateTime | None = None
    is_active: bool = True

    @field_validator("content")
    @classmethod
    def _clean_content(cls, v: str) -> str:
        return sanitize_html(v, max_length=5000) or ""


class NoticePatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = Field(default=None, min_length=1, max_length=5000)
    is_pinned: bool | None = None
    start_at: LocalDateTime | None = None
    end_at: LocalDateTime | None = None
    is_active: bool | None = None

    @field_validator("content")
    @classmethod
    def _clean_content(cls, v: str | None) -> str | None:
        return sanitize_html(v, max_length=5000) if v else v
