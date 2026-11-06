# SQLAlchemy Models for Incident Storage
from sqlalchemy import Column, String, Integer, DateTime, Text, JSON
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()


class Incident(Base):
    """Incident table for storing RCA results"""
    
    __tablename__ = 'incidents'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    incident_id = Column(String(100), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    severity = Column(String(20), nullable=False, index=True)
    status = Column(String(50), default="identified", index=True)
    affected_system = Column(String(100), nullable=False)
    summary = Column(Text, nullable=False)
    
    # JSON fields for complex data
    root_cause_analysis = Column(JSON, nullable=True)
    evidence_summary = Column(JSON, nullable=True)
    impact = Column(JSON, nullable=True)
    recommendations = Column(JSON, nullable=True)
    
    # Notification and remediation flags
    notification_sent = Column(Integer, default=0)  # SQLite uses 0/1 for boolean
    requires_human_approval = Column(Integer, default=1)
    auto_remediation_enabled = Column(Integer, default=0)
    
    def to_dict(self):
        """Convert to dictionary"""
        return {
            "incident_id": self.incident_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "severity": self.severity,
            "status": self.status,
            "affected_system": self.affected_system,
            "summary": self.summary,
            "root_cause_analysis": self.root_cause_analysis,
            "evidence_summary": self.evidence_summary,
            "impact": self.impact,
            "recommendations": self.recommendations,
            "notification_sent": bool(self.notification_sent),
            "requires_human_approval": bool(self.requires_human_approval),
            "auto_remediation_enabled": bool(self.auto_remediation_enabled)
        }
