"""ORM models. Importing this package registers every table on Base.metadata."""

from app.core.database import Base
from app.models.ai_conversation import AIConversation, AIMessage, MessageRole
from app.models.alert import EXPIRY_ALERT_TYPES, SEVERITY_RANK, Alert, AlertSeverity, AlertType
from app.models.audit_log import AuditAction, AuditLog
from app.models.batch import Batch, BatchStatus
from app.models.category import Category
from app.models.company import Company
from app.models.import_job import ImportFileType, ImportJob, ImportStatus
from app.models.inventory_movement import InventoryMovement, MovementType
from app.models.notification import Notification, NotificationChannel, NotificationStatus
from app.models.insight import Insight, InsightCategory
from app.models.product import Product
from app.models.sale import Sale
from app.models.user import RevokedToken, User, UserRole

__all__ = [
    "Base",
    "AIConversation",
    "AIMessage",
    "Insight",
    "InsightCategory",
    "MessageRole",
    "Alert",
    "AlertSeverity",
    "AlertType",
    "AuditAction",
    "AuditLog",
    "Batch",
    "BatchStatus",
    "Category",
    "Company",
    "EXPIRY_ALERT_TYPES",
    "ImportFileType",
    "ImportJob",
    "ImportStatus",
    "InventoryMovement",
    "MovementType",
    "Notification",
    "NotificationChannel",
    "NotificationStatus",
    "Product",
    "RevokedToken",
    "SEVERITY_RANK",
    "Sale",
    "User",
    "UserRole",
]
