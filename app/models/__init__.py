"""模型注册表：导入即完成 Base.metadata 注册。"""
from app.models.activity import Activity, ActivityCheckinCode, ActivityType
from app.models.admin import Admin
from app.models.audit import AuthSession, CheckinAttempt, LoginAttempt, OperationLog
from app.models.base import Base
from app.models.notice import Notice
from app.models.registration import Registration
from app.models.student import Student
from app.models.system import DictItem, ExportTask, ImportBatch, SystemSetting

__all__ = [
    "Activity",
    "ActivityCheckinCode",
    "ActivityType",
    "Admin",
    "AuthSession",
    "Base",
    "CheckinAttempt",
    "DictItem",
    "ExportTask",
    "ImportBatch",
    "LoginAttempt",
    "Notice",
    "OperationLog",
    "Registration",
    "Student",
    "SystemSetting",
]
