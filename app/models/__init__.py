"""模型注册表：导入即完成 Base.metadata 注册。

官方 9 张表（《数据库设计说明书 V1.0》第 4 章）：
    users / admins / activities / registrations / checkin_logs /
    import_batches / operation_logs / settings / dict_items

补充 3 张表（接口必需、文档未建模，见 app/models/supplement.py）：
    auth_sessions / notices / export_tasks
"""
from app.models.activity import Activity
from app.models.admin import Admin
from app.models.base import Base
from app.models.checkin_log import CheckinLog
from app.models.dict_item import DictItem, DictType
from app.models.import_batch import ImportBatch
from app.models.operation_log import OperationLog
from app.models.registration import Registration
from app.models.setting import Setting
from app.models.supplement import AuthSession, ExportTask, Notice
from app.models.user import Student, User

# 官方 9 张表
CORE_TABLES = (
    "users",
    "admins",
    "activities",
    "registrations",
    "checkin_logs",
    "import_batches",
    "operation_logs",
    "settings",
    "dict_items",
)

# 补充表
SUPPLEMENT_TABLES = ("auth_sessions", "notices", "export_tasks")

__all__ = [
    "Activity",
    "Admin",
    "AuthSession",
    "Base",
    "CheckinLog",
    "CORE_TABLES",
    "DictItem",
    "DictType",
    "ExportTask",
    "ImportBatch",
    "Notice",
    "OperationLog",
    "Registration",
    "SUPPLEMENT_TABLES",
    "Setting",
    "Student",
    "User",
]
