"""输出序列化：ORM → 接口契约 DTO（时间统一 ISO 8601 +08:00）。

字段来源已按《数据库设计说明书 V1.0》调整：
- 学生表为 users，报名表用 user_id 与三个快照字段；
- 活动不再有 registered_count / college / published_at 等列，报名人数与剩余名额实时聚合；
- 活动类型来自 dict_items（type_id → label）。
"""
from __future__ import annotations

from datetime import datetime

from fastapi import Request
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.enums import RegistrationStatus
from app.core.security import mask_mobile, mask_student_no
from app.core.timeutil import now_local, to_iso
from app.models.activity import Activity
from app.models.dict_item import DictItem
from app.models.registration import Registration
from app.models.supplement import Notice
from app.models.user import User
from app.services.activity import checkin_window, compute_signup_state, effective_status
from app.services.stats import rate_text


CHECKED_IN_STATUSES = (RegistrationStatus.CHECKED_IN, RegistrationStatus.COMPLETED)


def api_base(request: Request | None) -> str:
    if request is None:
        return ""
    return f"{str(request.base_url).rstrip('/')}{settings.API_PREFIX}"


def share_url_for(activity: Activity, base: str = "") -> str | None:
    if not activity.share_token:
        return None
    prefix = base or settings.API_PREFIX
    return f"{prefix.rstrip('/')}/share/{activity.share_token}"


# ---------------------------------------------------------------- 用户
def student_public(user: User) -> dict:
    """登录响应中的 user：学号、姓名、学院、专业、班级、年级（接口文档 3.1）。"""
    return {
        "student_no": user.student_no,
        "name": user.name,
        "college": user.college,
        "major": user.major,
        "class_name": user.class_name,
        "grade": user.grade,
    }


def student_me(user: User, aggregates: dict | None = None) -> dict:
    """GET /me（接口文档 3.5）。累计参与/学时实时聚合，不存冗余列。"""
    aggregates = aggregates or {}
    return {
        **student_public(user),
        "id": user.id,
        "mobile": mask_mobile(user.mobile),
        "mobile_raw": user.mobile if settings.DEBUG else None,
        "is_first_login": user.is_first_login,
        "status": user.status,
        "total_activities": int(aggregates.get("activity_count", 0)),
        "total_hours": round(float(aggregates.get("hours_total", 0)), 1),
        "last_login_at": to_iso(user.last_login_at),
        "created_at": to_iso(user.created_at),
    }


def admin_profile(admin, permissions: list[str] | None = None) -> dict:
    """GET /admin/me（接口文档 3.7）。"""
    return {
        "id": admin.id,
        "username": admin.username,
        "name": admin.name,
        "role": admin.role,
        "data_scope": admin.data_scope,
        "college": admin.college,
        "status": admin.status,
        "permissions": permissions or [],
        "last_login_at": to_iso(admin.last_login_at),
    }


def admin_brief(admin) -> dict | None:
    if admin is None:
        return None
    return {"id": admin.id, "username": admin.username, "name": admin.name, "role": admin.role}


# ---------------------------------------------------------------- 活动
def activity_type_out(activity_type: DictItem | None) -> dict | None:
    if activity_type is None:
        return None
    return {"id": activity_type.id, "name": activity_type.label, "value": activity_type.value}


def my_registration_out(registration: Registration | None) -> dict | None:
    """活动详情中的 my_registration（接口文档 4.4）。"""
    if registration is None:
        return None
    activity = registration.activity
    hours = (
        float(activity.hours or 0)
        if activity and registration.status in CHECKED_IN_STATUSES
        else 0.0
    )
    return {
        "id": registration.id,
        "status": registration.status,
        "signup_at": to_iso(registration.signup_at),
        "audit_remark": registration.audit_remark,
        "checkin_at": to_iso(registration.checkin_at),
        "checkin_method": registration.checkin_method,
        "is_late": registration.is_late,
        "hours": hours,
    }


def activity_list_item(
    activity: Activity,
    *,
    now: datetime | None = None,
    registration: Registration | None = None,
    remaining: int = 0,
    registered_count: int = 0,
) -> dict:
    """U-08 列表项（接口文档 4.3）。"""
    now = now or now_local()
    start, end = checkin_window(activity)
    return {
        "id": activity.id,
        "title": activity.title,
        "type": activity_type_out(activity.activity_type),
        "start_time": to_iso(activity.start_time),
        "end_time": to_iso(activity.end_time),
        "location": activity.location,
        "host": activity.host,
        "cover_url": activity.cover_url,
        "capacity": activity.capacity,
        "registered_count": registered_count,
        "remaining": remaining,
        "status": effective_status(activity, now),
        "signup_state": compute_signup_state(activity, registration, remaining, now),
        "hours": float(activity.hours or 0),
        "checkin_window": {"start": to_iso(start), "end": to_iso(end)},
    }


def activity_detail(
    activity: Activity,
    *,
    now: datetime | None = None,
    registration: Registration | None = None,
    remaining: int = 0,
    registered_count: int = 0,
    base_url: str = "",
) -> dict:
    """U-09 详情（接口文档 4.4）。"""
    now = now or now_local()
    data = activity_list_item(
        activity,
        now=now,
        registration=registration,
        remaining=remaining,
        registered_count=registered_count,
    )
    data.update(
        {
            "organizer": activity.organizer,
            "description": activity.description,
            "notice": activity.notice,
            "contact_name": activity.contact_name,
            "contact_phone": activity.contact_phone,
            "signup_start": to_iso(activity.signup_start),
            "signup_end": to_iso(activity.signup_end),
            "allow_cancel": activity.allow_cancel,
            "need_audit": activity.need_audit,
            "checkin_before": activity.checkin_before,
            "checkin_after": activity.checkin_after,
            "share_url": share_url_for(activity, base_url),
            "my_registration": my_registration_out(registration),
            "checkin_code": None,  # 学生端不返回签到码
        }
    )
    return data


def admin_activity_item(
    activity: Activity,
    *,
    now: datetime | None = None,
    checkin_count: int | None = None,
    registered_count: int = 0,
    creator_name: str | None = None,
) -> dict:
    """A-04 列表项（接口文档 5.3）。"""
    now = now or now_local()
    return {
        "id": activity.id,
        "title": activity.title,
        "type": activity_type_out(activity.activity_type),
        "start_time": to_iso(activity.start_time),
        "end_time": to_iso(activity.end_time),
        "location": activity.location,
        "capacity": activity.capacity,
        "registered_count": registered_count,
        "remaining": max(activity.capacity - registered_count, 0),
        "checkin_count": checkin_count or 0,
        "status": effective_status(activity, now),
        "version": activity.version,
        "need_audit": activity.need_audit,
        "hours": float(activity.hours or 0),
        "created_by": activity.created_by,
        "created_by_name": creator_name,
        "created_at": to_iso(activity.created_at),
        "updated_at": to_iso(activity.updated_at),
    }


def admin_activity_detail(
    activity: Activity,
    *,
    stats: dict | None = None,
    base_url: str = "",
    now: datetime | None = None,
    registered_count: int = 0,
    checkin_count: int | None = None,
    creator_name: str | None = None,
) -> dict:
    """A-06 详情：全部字段 + 签到码、分享链接与统计（接口文档 5.4）。"""
    now = now or now_local()
    start, end = checkin_window(activity)
    return {
        **admin_activity_item(
            activity,
            now=now,
            checkin_count=checkin_count,
            registered_count=registered_count,
            creator_name=creator_name,
        ),
        "type_id": activity.type_id,
        "host": activity.host,
        "organizer": activity.organizer,
        "cover_url": activity.cover_url,
        "description": activity.description,
        "notice": activity.notice,
        "contact_name": activity.contact_name,
        "contact_phone": activity.contact_phone,
        "signup_start": to_iso(activity.signup_start),
        "signup_end": to_iso(activity.signup_end),
        "allow_cancel": activity.allow_cancel,
        "checkin_before": activity.checkin_before,
        "checkin_after": activity.checkin_after,
        "checkin_window": {"start": to_iso(start), "end": to_iso(end)},
        "checkin_code": activity.checkin_code,
        "checkin_code_generated_at": to_iso(activity.checkin_code_generated_at),
        "checkin_code_reset_at": to_iso(activity.checkin_code_reset_at),
        "share_token": activity.share_token,
        "share_url": share_url_for(activity, base_url),
        "share_expire_at": to_iso(activity.share_expire_at),
        "deleted_at": to_iso(activity.deleted_at),
        "stats": stats or {},
    }


def activity_stats(db: Session, activity: Activity) -> dict:
    """A-06 的 stats：报名、通过、签到、缺席、学时合计（学时取活动配置）。"""
    row = db.execute(
        select(
            func.count(Registration.id),
            func.sum(
                case(
                    (
                        Registration.status.in_(
                            [
                                RegistrationStatus.APPROVED,
                                *CHECKED_IN_STATUSES,
                                RegistrationStatus.ABSENT,
                            ]
                        ),
                        1,
                    ),
                    else_=0,
                )
            ),
            func.sum(case((Registration.status.in_(CHECKED_IN_STATUSES), 1), else_=0)),
            func.sum(case((Registration.status == RegistrationStatus.ABSENT, 1), else_=0)),
        ).where(Registration.activity_id == activity.id)
    ).one()
    signup, approved, checked, absent = row
    checked_i, absent_i = int(checked or 0), int(absent or 0)
    return {
        "signup_count": int(signup or 0),
        "approved_count": int(approved or 0),
        "checkin_count": checked_i,
        "absent_count": absent_i,
        "checkin_rate": rate_text(checked_i, checked_i + absent_i),
        "hours_total": round(float(activity.hours or 0) * checked_i, 1),
    }


# ---------------------------------------------------------------- 报名
def registration_student_item(registration: Registration) -> dict:
    """U-13 我的报名列表项（接口文档 4.8）。"""
    activity = registration.activity
    hours = (
        float(activity.hours or 0)
        if activity and registration.status in CHECKED_IN_STATUSES
        else 0.0
    )
    return {
        "id": registration.id,
        "activity_id": registration.activity_id,
        "activity_title": activity.title if activity else None,
        "start_time": to_iso(activity.start_time) if activity else None,
        "end_time": to_iso(activity.end_time) if activity else None,
        "location": activity.location if activity else None,
        "cover_url": activity.cover_url if activity else None,
        "status": registration.status,
        "signup_at": to_iso(registration.signup_at),
        "checkin_at": to_iso(registration.checkin_at),
        "checkin_method": registration.checkin_method,
        "hours": hours,
    }


def registration_student_detail(registration: Registration) -> dict:
    """U-14 报名详情与签到信息（接口文档 4.8）。"""
    activity = registration.activity
    return {
        **registration_student_item(registration),
        "audit_status": registration.status,
        "audit_remark": registration.audit_remark,
        "audit_at": to_iso(registration.audit_at),
        "is_late": registration.is_late,
        "source": registration.source,
        "cancel_reason": registration.cancel_reason,
        "activity": {
            "id": activity.id,
            "title": activity.title,
            "type": activity_type_out(activity.activity_type),
            "host": activity.host,
            "location": activity.location,
            "start_time": to_iso(activity.start_time),
            "end_time": to_iso(activity.end_time),
            "hours": float(activity.hours or 0),
            "status": activity.status,
        }
        if activity
        else None,
    }


def registration_admin_item(registration: Registration, *, mask: bool = False) -> dict:
    """A-14 报名名单项（接口文档 5.7），学院/班级优先用报名快照。"""
    user = registration.user
    activity = registration.activity
    return {
        "id": registration.id,
        "activity_id": registration.activity_id,
        "user_id": registration.user_id,
        "student_id": registration.user_id,  # 兼容旧字段名
        "student_no": (
            mask_student_no(user.student_no) if mask and user else (user.student_no if user else None)
        ),
        "name": user.name if user else None,
        "college": registration.college_snapshot or (user.college if user else None),
        "major": registration.major_snapshot or (user.major if user else None),
        "class_name": registration.class_snapshot or (user.class_name if user else None),
        "grade": user.grade if user else None,
        "status": registration.status,
        "signup_at": to_iso(registration.signup_at),
        "source": registration.source,
        "audit_by": registration.audit_by,
        "audit_at": to_iso(registration.audit_at),
        "audit_remark": registration.audit_remark,
        "checkin_at": to_iso(registration.checkin_at),
        "checkin_method": registration.checkin_method,
        "is_late": registration.is_late,
        "hours": (
            float(activity.hours or 0)
            if activity and registration.status in CHECKED_IN_STATUSES
            else 0.0
        ),
        "cancel_reason": registration.cancel_reason,
    }


# ---------------------------------------------------------------- 公告
def notice_out(notice: Notice) -> dict:
    return {
        "id": notice.id,
        "title": notice.title,
        "content": notice.content,
        "notice_type": notice.notice_type,
        "is_pinned": notice.is_pinned,
        "start_at": to_iso(notice.start_at),
        "end_at": to_iso(notice.end_at),
        "created_at": to_iso(notice.created_at),
    }


def notice_admin_item(notice: Notice) -> dict:
    return {
        **notice_out(notice),
        "is_active": notice.is_active,
        "is_effective": notice.is_effective(),
        "created_by": notice.created_by,
    }
