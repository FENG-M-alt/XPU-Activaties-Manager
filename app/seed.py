"""初始化数据（AUTO_SEED=true 时在启动阶段自动执行，幂等）。

严格按《数据库设计说明书 V1.0》：
- 9.1 字典初始值：activity_type / reject_reason / notice_type
- 9.2 系统参数默认值：settings 表的 key / value / remark
- 示例账号与活动仅用于本地联调，生产请关闭 AUTO_SEED

默认账号：
    超级管理员  superadmin / Admin@123456
    学院管理员  csadmin    / Admin@123456   数据范围 COLLEGE（计算机学院）
    学生        2024010101 / Student@2026
"""
from __future__ import annotations

import logging
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
from app.models.activity import Activity
from app.models.admin import Admin
from app.models.checkin_log import CheckinLog
from app.models.dict_item import DictItem, DictType
from app.models.registration import Registration
from app.models.setting import Setting
from app.models.supplement import Notice
from app.models.user import User
from app.services.activity import issue_checkin_code, issue_share_token

logger = logging.getLogger("lab_checkin.seed")

STUDENT_PASSWORD = "Student@2026"
ADMIN_PASSWORD = "Admin@123456"

# 文档 9.1 字典初始值
DICT_SEED = [
    (DictType.ACTIVITY_TYPE, "日常生活性劳动", "daily", 1),
    (DictType.ACTIVITY_TYPE, "生产性劳动", "productive", 2),
    (DictType.ACTIVITY_TYPE, "服务性劳动", "service", 3),
    (DictType.ACTIVITY_TYPE, "专业性劳动", "professional", 4),
    (DictType.ACTIVITY_TYPE, "创造性劳动", "creative", 5),
    (DictType.REJECT_REASON, "名额已满", "full", 1),
    (DictType.REJECT_REASON, "不符合活动要求", "unqualified", 2),
    (DictType.REJECT_REASON, "信息不完整", "incomplete", 3),
    (DictType.REJECT_REASON, "其他", "other", 4),
    (DictType.NOTICE_TYPE, "系统公告", "system", 1),
    (DictType.NOTICE_TYPE, "活动通知", "activity", 2),
]

# 文档 9.2 系统参数默认值
SETTING_SEED = [
    ("checkin_before_default", "30", "新建活动默认提前签到分钟数"),
    ("checkin_after_default", "30", "新建活动默认延后签到分钟数"),
    ("init_password_rule", "id_card_tail6", "初始密码规则，无身份证号时取学号后 6 位"),
    ("student_login_lock", "5:10", "学生端 5 次失败锁定 10 分钟"),
    ("admin_login_lock", "5:15", "管理端 5 次失败锁定 15 分钟"),
    ("share_expire_days", "0", "0 表示长期有效，N 表示活动结束后 N 天失效"),
    ("allow_student_cancel", "true", "是否允许学生取消报名"),
    ("checkin_makeup_days", "7", "补签期限（活动结束后天数）"),
    ("export_file_retain_days", "7", "异步导出文件保留天数"),
    ("record_checkin_ip", "false", "是否记录签到 IP"),
    # 应用级补充参数
    ("checkin_late_grace_minutes", "0", "迟到判定宽限分钟数（0 表示严格晚于活动开始时间即迟到）"),
]

USERS = [
    ("2024010101", "张一鸣", "计算机学院", "软件工程", "软工2401", 2024, "13800000101"),
    ("2024010102", "李思远", "计算机学院", "计算机科学与技术", "计科2401", 2024, "13800000102"),
    ("2024010103", "王雨桐", "计算机学院", "网络工程", "网工2401", 2024, "13800000103"),
    ("2024010104", "赵梓涵", "计算机学院", "软件工程", "软工2402", 2024, "13800000104"),
    ("2024020101", "陈嘉俊", "外国语学院", "英语", "英语2401", 2024, "13800000201"),
    ("2024020102", "刘欣然", "外国语学院", "日语", "日语2401", 2024, "13800000202"),
    ("2024030101", "孙浩宇", "机械工程学院", "机械设计制造及其自动化", "机械2401", 2024, "13800000301"),
    ("2024030102", "周静怡", "机械工程学院", "车辆工程", "车辆2401", 2024, "13800000302"),
    ("2024040101", "吴子墨", "经济管理学院", "工商管理", "工商2401", 2024, "13800000401"),
    ("2024040102", "郑晓彤", "经济管理学院", "会计学", "会计2401", 2024, "13800000402"),
    ("2023010101", "冯天佑", "计算机学院", "人工智能", "智能2301", 2023, "13800000501"),
    ("2023010102", "许安然", "计算机学院", "数据科学", "数科2301", 2023, "13800000502"),
]


def seed_if_empty(db: Session) -> bool:
    """仅在管理员表为空时灌入初始化数据。"""
    existing = db.scalar(select(func.count()).select_from(Admin)) or 0
    if existing:
        return False
    seed_all(db)
    logger.info("已写入初始化数据（superadmin / Admin@123456，学生 2024010101 / Student@2026）")
    return True


def seed_all(db: Session) -> None:
    now = now_local()

    # ---- 9.2 系统参数
    for key, value, remark in SETTING_SEED:
        if db.get(Setting, key) is None:
            db.add(Setting(key=key, value=value, remark=remark, updated_at=now))

    # ---- 9.1 字典
    dict_ids: dict[str, int] = {}
    for dict_type, label, value, sort in DICT_SEED:
        existing = db.scalars(
            select(DictItem).where(DictItem.dict_type == dict_type, DictItem.value == value)
        ).first()
        if existing is None:
            existing = DictItem(
                dict_type=dict_type, label=label, value=value, sort=sort, enabled=True
            )
            db.add(existing)
            db.flush()
        dict_ids[value] = existing.id

    # ---- 管理员
    super_admin = Admin(
        username="superadmin",
        name="系统管理员",
        password_hash=hash_password(ADMIN_PASSWORD),
        role=AdminRole.SUPER_ADMIN,
        data_scope=DataScope.ALL,
        college=None,
        is_first_login=False,
        login_fail_count=0,
    )
    college_admin = Admin(
        username="csadmin",
        name="计算机学院管理员",
        password_hash=hash_password(ADMIN_PASSWORD),
        role=AdminRole.ADMIN,
        data_scope=DataScope.COLLEGE,
        college="计算机学院",
        is_first_login=False,
        login_fail_count=0,
    )
    db.add_all([super_admin, college_admin])
    db.flush()

    # ---- 学生
    users: list[User] = []
    for student_no, name, college, major, class_name, grade, mobile in USERS:
        user = User(
            student_no=student_no,
            name=name,
            college=college,
            major=major,
            class_name=class_name,
            grade=grade,
            mobile=mobile,
            password_hash=hash_password(STUDENT_PASSWORD),
            is_first_login=(student_no == "2024010101"),
            login_fail_count=0,
        )
        db.add(user)
        users.append(user)
    db.flush()

    # ---- 公告
    db.add_all(
        [
            Notice(
                title="关于 2026 年秋季学期劳动教育活动安排的通知",
                content=(
                    "<p>各学院：</p><p>本学期劳动教育活动将于第 5 周起陆续开展，"
                    "请同学们关注活动列表并按时报名、签到。签到码由现场工作人员公布。</p>"
                ),
                notice_type="system",
                is_pinned=True,
                start_at=now - timedelta(days=3),
                end_at=now + timedelta(days=60),
                created_by=super_admin.id,
            ),
            Notice(
                title="签到规范说明",
                content="<p>请在活动开始前 30 分钟至结束后 30 分钟完成签到，逾期视为缺席。</p>",
                notice_type="system",
                is_pinned=False,
                start_at=now - timedelta(days=10),
                end_at=now + timedelta(days=120),
                created_by=college_admin.id,
            ),
        ]
    )

    # ---- 活动（type_id 取字典项）
    ongoing = Activity(
        title="图书馆志愿整理服务",
        type_id=dict_ids["service"],
        host="校团委",
        organizer="青年志愿者协会",
        location="图书馆三层阅览室",
        description="<p>协助图书馆整理书架、引导读者，服务时长 2 小时。</p>",
        notice="<p>请穿平底鞋，提前 10 分钟到岗。</p>",
        contact_name="王老师",
        contact_phone="0571-88880001",
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
        version=1,
        created_by=super_admin.id,
    )
    auditing = Activity(
        title="敬老院慰问演出",
        type_id=dict_ids["daily"],
        host="校学生会",
        organizer="文艺部",
        location="阳光敬老院",
        description="<p>为敬老院老人表演节目并陪伴交流，需提前审核报名。</p>",
        notice="<p>报名后请等待管理员审核，审核通过后统一乘车前往。</p>",
        contact_name="李同学",
        contact_phone="13800009999",
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
        version=1,
        created_by=college_admin.id,
    )
    finished = Activity(
        title="校园环境清洁行动",
        type_id=dict_ids["daily"],
        host="后勤保障处",
        organizer="学生会生活部",
        location="东校区中心广场",
        description="<p>清理校园公共区域垃圾，美化校园环境。</p>",
        contact_name="赵老师",
        contact_phone="0571-88880002",
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
        version=1,
        created_by=super_admin.id,
    )
    draft = Activity(
        title="劳动教育专题讲座（草稿）",
        type_id=dict_ids["professional"],
        host="教务处",
        organizer="教务处实践科",
        location="学术报告厅",
        description="<p>劳动教育政策解读与实务分享。</p>",
        contact_name="孙老师",
        contact_phone="0571-88880003",
        signup_start=now + timedelta(days=1),
        signup_end=now + timedelta(days=6),
        start_time=now + timedelta(days=7),
        end_time=now + timedelta(days=7, hours=2),
        capacity=100,
        need_audit=False,
        allow_cancel=True,
        hours=Decimal("1.0"),
        checkin_before=30,
        checkin_after=30,
        status=ActivityStatus.DRAFT,
        version=1,
        created_by=college_admin.id,
    )
    db.add_all([ongoing, auditing, finished, draft])
    db.flush()

    for activity in (ongoing, auditing, finished):
        issue_checkin_code(db, activity, admin_id=super_admin.id, reason="初始化示例数据")
        issue_share_token(db, activity, now)
    db.flush()

    # ---- 报名（含快照）+ 签到流水
    logs: list[CheckinLog] = []

    def add_registration(
        activity: Activity,
        user: User,
        status: str,
        *,
        checkin_offset: timedelta | None = None,
        is_late: bool = False,
        audit: bool = False,
    ) -> Registration:
        registration = Registration(
            activity_id=activity.id,
            user_id=user.id,
            status=status,
            college_snapshot=user.college or "",
            major_snapshot=user.major,
            class_snapshot=user.class_name,
            signup_at=now - timedelta(days=2),
            source=SignupSource.SELF,
            is_late=is_late,
        )
        if audit:
            registration.audit_by = college_admin.id
            registration.audit_at = now - timedelta(days=1)
            registration.audit_remark = "符合报名条件"
        if checkin_offset is not None:
            registration.checkin_at = activity.start_time + checkin_offset
            registration.checkin_method = "CODE"
        db.add(registration)
        db.flush()
        if checkin_offset is not None:
            logs.append(
                CheckinLog(
                    registration_id=registration.id,
                    action="CHECKIN",
                    method="CODE",
                    created_at=registration.checkin_at,
                )
            )
        return registration

    for user in users[:6]:
        add_registration(ongoing, user, RegistrationStatus.CHECKED_IN, checkin_offset=timedelta(minutes=5))
    add_registration(ongoing, users[6], RegistrationStatus.APPROVED)
    add_registration(ongoing, users[7], RegistrationStatus.APPROVED)

    for user in users[2:7]:
        add_registration(auditing, user, RegistrationStatus.PENDING)
    add_registration(auditing, users[8], RegistrationStatus.APPROVED, audit=True)
    add_registration(auditing, users[9], RegistrationStatus.REJECTED, audit=True)

    for user in users[4:10]:
        add_registration(finished, user, RegistrationStatus.COMPLETED, checkin_offset=timedelta(minutes=3))
    add_registration(finished, users[0], RegistrationStatus.COMPLETED, checkin_offset=timedelta(minutes=2))
    add_registration(finished, users[1], RegistrationStatus.ABSENT)

    db.add_all(logs)
    db.commit()


def _main() -> None:
    """命令行：python -m app.seed —— 建表并在空库时写入初始化数据。"""
    import logging as _logging

    from app.database import SessionLocal, init_db

    _logging.basicConfig(level=_logging.INFO, format="%(message)s")
    init_db()
    db = SessionLocal()
    try:
        created = seed_if_empty(db)
        print("已写入初始化数据" if created else "数据库已有数据，跳过初始化")
    finally:
        db.close()


if __name__ == "__main__":
    _main()
