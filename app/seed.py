"""示例数据初始化（AUTO_SEED=true 时在启动阶段自动执行，幂等）。

默认账号：
    超级管理员  superadmin / Admin@123456
    学院管理员  csadmin    / Admin@123456   （数据范围：计算机学院）
    学生        2024010101 / Student@2026   （其余学生同密码）
"""
from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.enums import (
    ActivityStatus,
    AdminRole,
    DataScope,
    RegistrationStatus,
    SignupSource,
)
from app.core.security import hash_password
from app.core.timeutil import now_local
from app.models.activity import Activity, ActivityType
from app.models.admin import Admin
from app.models.notice import Notice
from app.models.registration import Registration
from app.models.student import Student
from app.models.system import DictItem, SystemSetting
from app.services.activity import issue_checkin_code, issue_share_token

logger = logging.getLogger("lab_checkin.seed")

STUDENT_PASSWORD = "Student@2026"
ADMIN_PASSWORD = "Admin@123456"

ACTIVITY_TYPES = [
    ("志愿服务", "volunteer", 10),
    ("社会实践", "practice", 20),
    ("劳动教育", "labor", 30),
    ("校园服务", "campus", 40),
    ("公益活动", "charity", 50),
]

DEFAULT_SETTINGS = [
    ("checkin_late_grace_minutes", "0", "迟到判定宽限分钟数（0 表示严格晚于活动开始时间即迟到）", "checkin"),
    ("checkin_record_ip", "true", "签到是否记录来源 IP", "checkin"),
    ("makeup_checkin_days", "7", "补签期限（活动结束后天数）", "checkin"),
    ("checkin_before_default", "30", "默认提前多少分钟开放签到", "checkin"),
    ("signup_need_audit_default", "false", "新建活动是否默认需要审核", "activity"),
    ("export_sync_limit", "10000", "同步导出最大行数，超出转异步", "export"),
    ("notice_default_days", "30", "公告默认有效期天数", "notice"),
]

DICT_ITEMS = [
    ("college", "计算机学院", "CS", 10),
    ("college", "外国语学院", "FL", 20),
    ("college", "机械工程学院", "ME", 30),
    ("college", "经济管理学院", "EM", 40),
    ("grade", "2023 级", "2023", 10),
    ("grade", "2024 级", "2024", 20),
    ("grade", "2025 级", "2025", 30),
]

STUDENTS = [
    ("2024010101", "张一鸣", "计算机学院", "软件工程", "软工2401", "2024", "13800000101"),
    ("2024010102", "李思远", "计算机学院", "计算机科学与技术", "计科2401", "2024", "13800000102"),
    ("2024010103", "王雨桐", "计算机学院", "网络工程", "网工2401", "2024", "13800000103"),
    ("2024010104", "赵梓涵", "计算机学院", "软件工程", "软工2402", "2024", "13800000104"),
    ("2024020101", "陈嘉俊", "外国语学院", "英语", "英语2401", "2024", "13800000201"),
    ("2024020102", "刘欣然", "外国语学院", "日语", "日语2401", "2024", "13800000202"),
    ("2024030101", "孙浩宇", "机械工程学院", "机械设计制造及其自动化", "机械2401", "2024", "13800000301"),
    ("2024030102", "周静怡", "机械工程学院", "车辆工程", "车辆2401", "2024", "13800000302"),
    ("2024040101", "吴子墨", "经济管理学院", "工商管理", "工商2401", "2024", "13800000401"),
    ("2024040102", "郑晓彤", "经济管理学院", "会计学", "会计2401", "2024", "13800000402"),
    ("2023010101", "冯天佑", "计算机学院", "人工智能", "智能2301", "2023", "13800000501"),
    ("2023010102", "许安然", "计算机学院", "数据科学", "数科2301", "2023", "13800000502"),
]


def seed_if_empty(db: Session) -> bool:
    """仅在管理员表为空时灌入示例数据。"""
    existing = db.scalar(select(func.count()).select_from(Admin)) or 0
    if existing:
        return False
    seed_all(db)
    logger.info("已写入示例数据（superadmin / Admin@123456，学生 2024010101 / Student@2026）")
    return True


def seed_all(db: Session) -> None:
    now = now_local()

    for key, value, description, group in DEFAULT_SETTINGS:
        if db.get(SystemSetting, key) is None:
            db.add(SystemSetting(key=key, value=value, default_value=value, description=description, group=group))

    for dict_type, label, value, sort in DICT_ITEMS:
        if not db.scalars(
            select(DictItem).where(DictItem.dict_type == dict_type, DictItem.value == value)
        ).first():
            db.add(DictItem(dict_type=dict_type, label=label, value=value, sort=sort))

    types: dict[str, ActivityType] = {}
    for name, code, sort in ACTIVITY_TYPES:
        activity_type = db.scalars(select(ActivityType).where(ActivityType.code == code)).first()
        if activity_type is None:
            activity_type = ActivityType(name=name, code=code, sort=sort)
            db.add(activity_type)
            db.flush()
        types[name] = activity_type

    super_admin = Admin(
        username="superadmin",
        name="系统管理员",
        password_hash=hash_password(ADMIN_PASSWORD),
        role=AdminRole.SUPER_ADMIN,
        data_scope=DataScope.ALL,
        college=None,
        last_login_at=None,
    )
    college_admin = Admin(
        username="csadmin",
        name="计算机学院管理员",
        password_hash=hash_password(ADMIN_PASSWORD),
        role=AdminRole.ADMIN,
        data_scope=DataScope.COLLEGE,
        college="计算机学院",
    )
    db.add_all([super_admin, college_admin])
    db.flush()

    students: list[Student] = []
    for student_no, name, college, major, class_name, grade, mobile in STUDENTS:
        student = Student(
            student_no=student_no,
            name=name,
            college=college,
            major=major,
            class_name=class_name,
            grade=grade,
            mobile=mobile,
            password_hash=hash_password(STUDENT_PASSWORD),
            is_first_login=(student_no == "2024010101"),
        )
        db.add(student)
        students.append(student)
    db.flush()

    # 公告
    db.add_all(
        [
            Notice(
                title="关于 2026 年秋季学期劳动教育活动安排的通知",
                content=(
                    "<p>各学院：</p><p>本学期劳动教育活动将于第 5 周起陆续开展，"
                    "请同学们关注活动列表并按时报名、签到。签到码由现场工作人员公布。</p>"
                ),
                is_pinned=True,
                start_at=now - timedelta(days=3),
                end_at=now + timedelta(days=60),
                created_by=super_admin.id,
            ),
            Notice(
                title="签到规范说明",
                content="<p>请在活动开始前 30 分钟至结束后 30 分钟内完成签到，逾期视为缺席。</p>",
                is_pinned=False,
                start_at=now - timedelta(days=10),
                end_at=now + timedelta(days=120),
                created_by=college_admin.id,
            ),
        ]
    )

    # 活动 1：进行中（可签到）
    ongoing = Activity(
        title="图书馆志愿整理服务",
        type_id=types["志愿服务"].id,
        host="校团委",
        organizer="青年志愿者协会",
        location="图书馆三层阅览室",
        description="<p>协助图书馆整理书架、引导读者，服务时长 2 小时。</p>",
        notice="<p>请穿平底鞋，提前 10 分钟到岗。</p>",
        contact_name="王老师",
        contact_phone="0571-88880001",
        college="计算机学院",
        signup_start=now - timedelta(days=5),
        signup_end=now - timedelta(hours=1),
        start_time=now - timedelta(hours=1),
        end_time=now + timedelta(hours=3),
        capacity=30,
        need_audit=False,
        allow_cancel=True,
        hours=Decimal("2.0"),
        checkin_before=60,
        checkin_after=60,
        status=ActivityStatus.ONGOING,
        created_by=super_admin.id,
        published_at=now - timedelta(days=6),
    )
    # 活动 2：报名中（需审核）
    auditing = Activity(
        title="敬老院慰问演出",
        type_id=types["公益活动"].id,
        host="校学生会",
        organizer="文艺部",
        location="阳光敬老院",
        description="<p>为敬老院老人表演节目并陪伴交流，需提前审核报名。</p>",
        notice="<p>报名后请等待管理员审核，审核通过后统一乘车前往。</p>",
        contact_name="李同学",
        contact_phone="13800009999",
        college="计算机学院",
        signup_start=now - timedelta(days=1),
        signup_end=now + timedelta(days=2),
        start_time=now + timedelta(days=3),
        end_time=now + timedelta(days=3, hours=3),
        capacity=20,
        need_audit=True,
        allow_cancel=True,
        hours=Decimal("3.0"),
        checkin_before=30,
        checkin_after=30,
        status=ActivityStatus.REGISTERING,
        created_by=college_admin.id,
        published_at=now - timedelta(days=1),
    )
    # 活动 3：已结束
    finished = Activity(
        title="校园环境清洁行动",
        type_id=types["劳动教育"].id,
        host="后勤保障处",
        organizer="学生会生活部",
        location="东校区中心广场",
        description="<p>清理校园公共区域垃圾，美化校园环境。</p>",
        college="外国语学院",
        signup_start=now - timedelta(days=20),
        signup_end=now - timedelta(days=15),
        start_time=now - timedelta(days=12),
        end_time=now - timedelta(days=12) + timedelta(hours=2),
        capacity=40,
        need_audit=False,
        allow_cancel=True,
        hours=Decimal("1.5"),
        checkin_before=30,
        checkin_after=30,
        status=ActivityStatus.FINISHED,
        created_by=super_admin.id,
        published_at=now - timedelta(days=21),
        finished_at=now - timedelta(days=12) + timedelta(hours=2),
    )
    # 活动 4：草稿
    draft = Activity(
        title="劳动教育专题讲座（草稿）",
        type_id=types["劳动教育"].id,
        host="教务处",
        location="学术报告厅",
        description="<p>劳动教育政策解读与实务分享。</p>",
        signup_start=now + timedelta(days=1),
        signup_end=now + timedelta(days=6),
        start_time=now + timedelta(days=7),
        end_time=now + timedelta(days=7, hours=2),
        capacity=100,
        hours=Decimal("1.0"),
        status=ActivityStatus.DRAFT,
        created_by=college_admin.id,
        college="计算机学院",
    )
    db.add_all([ongoing, auditing, finished, draft])
    db.flush()

    for activity in (ongoing, auditing, finished):
        issue_checkin_code(db, activity, admin_id=super_admin.id, reason="初始化示例数据")
        issue_share_token(db, activity, now)

    db.flush()

    # 报名数据
    def add_registration(activity: Activity, student: Student, status: str, *, checkin_offset: timedelta | None = None, late: bool = False, audit: bool = False):
        registration = Registration(
            activity_id=activity.id,
            student_id=student.id,
            status=status,
            signup_at=now - timedelta(days=2),
            source=SignupSource.SELF,
            is_late=late,
            hours_awarded=Decimal("0.0") if checkin_offset is None else activity.hours,
        )
        if checkin_offset is not None:
            registration.checkin_at = activity.start_time + checkin_offset
            registration.checkin_method = "CODE"
            registration.checkin_ip = "127.0.0.1"
        if audit:
            registration.audit_by = college_admin.id
            registration.audit_at = now - timedelta(days=1)
            registration.audit_remark = "符合报名条件"
        db.add(registration)
        return registration

    checked_students = students[:6]
    for student in checked_students:
        add_registration(ongoing, student, RegistrationStatus.CHECKED_IN, checkin_offset=timedelta(minutes=5))
    add_registration(ongoing, students[6], RegistrationStatus.APPROVED)
    add_registration(ongoing, students[7], RegistrationStatus.APPROVED)
    ongoing.registered_count = len(checked_students) + 2

    for student in students[2:7]:
        add_registration(auditing, student, RegistrationStatus.PENDING)
    add_registration(auditing, students[8], RegistrationStatus.APPROVED, audit=True)
    add_registration(auditing, students[9], RegistrationStatus.REJECTED, audit=True)
    auditing.registered_count = 6

    for student in students[4:10]:
        add_registration(finished, student, RegistrationStatus.CHECKED_IN, checkin_offset=timedelta(minutes=3))
    add_registration(finished, students[0], RegistrationStatus.COMPLETED, checkin_offset=timedelta(minutes=2))
    add_registration(finished, students[1], RegistrationStatus.ABSENT)
    finished.registered_count = 8

    db.flush()
    from app.services.checkin import _apply_student_totals

    for student in students:
        _apply_student_totals(db, student.id)

    db.commit()


def _main() -> None:
    """命令行：python -m app.seed —— 建表并在空库时写入示例数据。"""
    import logging

    from app.database import SessionLocal, init_db

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    init_db()
    db = SessionLocal()
    try:
        created = seed_if_empty(db)
        print("已写入示例数据" if created else "数据库已有数据，跳过初始化")
    finally:
        db.close()


if __name__ == "__main__":
    _main()
