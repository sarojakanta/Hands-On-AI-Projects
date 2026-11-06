"""ORM models package.

Importing this package registers every model on ``Base.metadata`` so that
``Base.metadata.create_all()`` creates all tables.
"""
from app.database.base import Base
from app.models.conversation import ChatMessage, Conversation, Escalation
from app.models.course import Batch, Course
from app.models.institute import AdminUser, Institute
from app.models.knowledge import KnowledgeDocument
from app.models.lead import FollowUp, StudentLead

__all__ = [
    "Base",
    "Institute",
    "AdminUser",
    "Course",
    "Batch",
    "StudentLead",
    "FollowUp",
    "Conversation",
    "ChatMessage",
    "Escalation",
    "KnowledgeDocument",
]
