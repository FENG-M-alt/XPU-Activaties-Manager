"""输出序列化：把 ORM 对象转换为文档约定的 DTO（时间统一 ISO 8601 +08:00）。"""
from __future__ import annotations

from datetime import datetime

from fastapi import Request
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.enums import RegistrationStatus
from app.core.security import mask_mobile, mask_student_no
from app.core.timeutil import now_local, to_iso
from app.models.activity import Activity, ActivityType
from app.models.admin import Admin
from app.models.notice import Notice
from app.models.registration import Registration
from app.models.student import Student
from app.services.activity import checkin_window, compute_signup_state, effective_status


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
def student_public(student: Student) -> dict:
    """登录响应中的 user：学号、姓名、学院、专业、班级、年级（文档 3.1）。"""
    return {
        "student_no": student.student_no,
        "name": student.name,
        "college": student.college,
        "major": student.major,
        "class_name": student.class_name,
        "grade": student.grade,
    }


def student_me(student: Student) -> dict:
    """GET /me（文档 3.5）。"""
    return {
        **student_public(student),
        "id": student.id,
        "mobile": mask_mobile(student.mobile),
        "mobile_raw": student.mobile if settings.DEBUG else None,
        "is_first_login": student.is_first_login,
        "status": student.status,
        "total_activities": student.total_activities,
        "total_hours": round(float(student.total_hours or 0), 1),
        "last_login_at": to_iso(student.last_login_at),
        "created_at": to_iso(student.created_at),
    }


def admin_profile(admin: Admin, permissions: list[str] | None = None) -> dict:
    """GET /admin/me（文档 3.7）。"""
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


def admin_brief(admin: Admin | None) -> dict | None:
    if admin is None:
        return None
    return {"id": admin.id, "username": admin.username, "name": admin.name, "role": admin.role}


# ---------------------------------------------------------------- 活动
def activity_type_out(activity_type: ActivityType | None) -> dict | None:
    if activity_type is None:
        return None
    return {"id": activity_type.id, "name": activity_type.name}


def my_registration_out(registration: Registration | None) -> dict | None:
    """活动详情中的 my_registration（文档 4.4）。"""
    if registration is None:
        return None
    return {
        "id": registration.id,
        "status": registration.status,
        "signup_at": to_iso(registration.signup_at),
        "audit_remark": registration.audit_remark,
        "checkin_at": to_iso(registration.checkin_at),
        "checkin_method": registration.checkin_method,
        "is_late": registration.is_late,
        "hours": float(registration.hours_awarded or 0),
    }


def activity_list_item(
    activity: Activity,
    *,
    now: datetime | None = None,
    registration: Registration | None = None,
) -> dict:
    """U-08 列表项（文档 4.3）。"""
    now = now or now_local()
    start, end = checkin_window(activity)
    return {
        "id": activity.id,
        "title": activity.title,
        "type": activity_type_out(activity.type),
        "start_time": to_iso(activity.start_time),
        "end_time": to_iso(activity.end_time),
        "location": activity.location,
        "host": activity.host,
        "cover_url": activity.cover_url,
        "capacity": activity.capacity,
        "registered_count": activity.registered_count,
        "remaining": activity.remaining,
        "status": effective_status(activity, now),
        "signup_state": compute_signup_state(activity, registration, now),
        "hours": float(activity.hours or 0),
        "checkin_window": {"start": to_iso(start), "end": to_iso(end)},
    }


def activity_detail(
    activity: Activity,
    *,
    now: datetime | None = None,
    registration: Registration | None = None,
    base_url: str = "",
) -> dict:
    """U-09 详情（文档 4.4）。"""
    now = now or now_local()
    start, end = checkin_window(activity)
    data = activity_list_item(activity, now=now, registration=registration)
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
            "checkin_code": None,
        }
    )
    return data


def admin_activity_item(activity: Activity, *, now: datetime | None = None) -> dict:
    """A-04 列表项（文档 5.3）。"""
    now = now or now_local()
    return {
        "id": activity.id,
        "title": activity.title,
        "type": activity_type_out(activity.type),
        "start_time": to_iso(activity.start_time),
        "end_time": to_iso(activity.end_time),
        "location": activity.location,
        "capacity": activity.capacity,
        "registered_count": activity.registered_count,
        "checkin_count": getattr(activity, "_checkin_count", None),
        "status": effective_status(activity, now),
        "version": activity.version,
        "need_audit": activity.need_audit,
        "hours": float(activity.hours or 0),
        "created_by": activity.created_by,
        "created_by_name": activity.creator.name if activity.creator else None,
        "college": activity.college,
        "created_at": to_iso(activity.created_at),
        "updated_at": to_iso(activity.updated_at),
    }


def admin_activity_detail(activity: Activity, *, stats: dict | None = None, base_url: str = "", now: datetime | None = None) -> dict:
    """A-06 详情：返回全部字段并附带签到码、分享链接与统计（文档 5.4）。"""
    now = now or now_local()
    start, end = checkin_window(activity)
    return {
        **admin_activity_item(activity, now=now),
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
        "remaining": activity.remaining,
        "checkin_code": activity.checkin_code,
        "checkin_code_generated_at": to_iso(activity.checkin_code_generated_at),
        "checkin_code_reset_at": to_iso(activity.checkin_code_reset_at),
        "share_token": activity.share_token,
        "share_url": share_url_for(activity, base_url),
        "share_expire_at": to_iso(activity.share_expire_at),
        "published_at": to_iso(activity.published_at),
        "cancelled_at": to_iso(activity.cancelled_at),
        "cancel_reason": activity.cancel_reason,
        "finished_at": to_iso(activity.finished_at),
        "stats": stats or {},
    }


def activity_stats(db: Session, activity_id: int) -> dict:
    """A-06 的 stats：报名、通过、签到、缺席、学时合计。"""
    row = db.execute(
        select(
            func.count(Registration.id),
            func.sum(case((Registration.status.in_([RegistrationStatus.APPROVED, *CHECKED_IN_STATUSES, RegistrationStatus.ABSENT]), 1), else_=0)),
            func.sum(case((Registration.status.in_(CHECKED_IN_STATUSES), 1), else_=0)),
            func.sum(case((Registration.status == RegistrationStatus.ABSENT, 1), else_=0)),
            func.coalesce(func.sum(Registration.hours_awarded), 0),
        ).where(Registration.activity_id == activity_id)
    ).one()
    signup, approved, checked, absent, hours = row
    checked_i, absent_i = int(checked or 0), int(absent or 0)
    from app.services.stats import rate_text

    return {
        "signup_count": int(signup or 0),
        "approved_count": int(approved or 0),
        "checkin_count": checked_i,
        "absent_count": absent_i,
        "checkin_rate": rate_text(checked_i, checked_i + absent_i),
        "hours_total": round(float(hours or 0), 1),
    }


# ---------------------------------------------------------------- 报名
def registration_student_item(registration: Registration) -> dict:
    """U-13 我的报名列表项（文档 4.8）。"""
    activity = registration.activity
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
        "hours": float(registration.hours_awarded or 0),
    }


def registration_student_detail(registration: Registration) -> dict:
    """U-14 报名详情与签到信息（文档 4.8）。"""
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
            "type": activity_type_out(activity.type),
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
    """A-14 报名名单项（文档 5.7）。"""
    student = registration.student
    return {
        "id": registration.id,
        "activity_id": registration.activity_id,
        "student_id": registration.student_id,
        "student_no": mask_student_no(student.student_no) if mask and student else (student.student_no if student else None),
        "name": student.name if student else None,
        "college": student.college if student else None,
        "major": student.major if student else None,
        "class_name": student.class_name if student else None,
        "grade": student.grade if student else None,
        "status": registration.status,
        "signup_at": to_iso(registration.signup_at),
        "source": registration.source,
        "audit_by": registration.audit_by,
        "audit_at": to_iso(registration.audit_at),
        "audit_remark": registration.audit_remark,
        "checkin_at": to_iso(registration.checkin_at),
        "checkin_method": registration.checkin_method,
        "is_late": registration.is_late,
        "hours": float(registration.hours_awarded or 0),
        "cancel_reason": registration.cancel_reason,
    }


# ---------------------------------------------------------------- 公告
def notice_out(notice: Notice) -> dict:
    return {
        "id": notice.id,
        "title": notice.title,
        "content": notice.content,
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
