# SQLite Client for Incident Storage
import os
import logging
import shutil
from datetime import datetime
from typing import Optional, List, Dict
from sqlalchemy import create_engine, desc, asc
from sqlalchemy.orm import sessionmaker, Session
from .models import Base, Incident

logger = logging.getLogger(__name__)


class SQLiteClient:
    """SQLite client with file size management and auto-archiving"""
    
    def __init__(
        self,
        db_path: str = "data/incidents.db",
        max_size_mb: int = 100,
        archive_path: str = "data/archive"
    ):
        """
        Initialize SQLite client
        
        Args:
            db_path: Path to SQLite database file
            max_size_mb: Maximum database file size in MB
            archive_path: Path to store archived databases
        """
        self.db_path = db_path
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.archive_path = archive_path
        
        # Create directories if needed
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        os.makedirs(archive_path, exist_ok=True)
        
        # Initialize database
        self.engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False}
        )
        self.SessionLocal = sessionmaker(bind=self.engine)
        
        # Create tables
        Base.metadata.create_all(bind=self.engine)
        
        logger.info(f"SQLite initialized: {db_path} (max size: {max_size_mb}MB)")
    
    def get_db_size(self) -> int:
        """Get current database file size in bytes"""
        if os.path.exists(self.db_path):
            return os.path.getsize(self.db_path)
        return 0
    
    def check_size_limit(self) -> bool:
        """Check if database size is within limit"""
        current_size = self.get_db_size()
        return current_size < self.max_size_bytes
    
    def archive_database(self) -> str:
        """Archive current database and create new one"""
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        archive_file = os.path.join(self.archive_path, f"incidents_{timestamp}.db")
        
        # Close existing connections
        self.engine.dispose()
        
        # Copy current database to archive
        shutil.copy2(self.db_path, archive_file)
        logger.warning(f"Database archived to: {archive_file}")
        
        # Remove old database
        os.remove(self.db_path)
        
        # Recreate database
        self.engine = create_engine(
            f"sqlite:///{self.db_path}",
            connect_args={"check_same_thread": False}
        )
        self.SessionLocal = sessionmaker(bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        
        logger.info(f"New database created: {self.db_path}")
        return archive_file
    
    def create_incident(self, incident_data: Dict) -> Incident:
        """
        Create new incident record
        
        Args:
            incident_data: Dictionary with incident data
            
        Returns:
            Created Incident object
        """
        # Check size limit before write
        if not self.check_size_limit():
            logger.warning("Database size limit reached, archiving...")
            self.archive_database()
        
        session: Session = self.SessionLocal()
        try:
            incident = Incident(
                incident_id=incident_data.get("incident_id"),
                created_at=datetime.fromisoformat(incident_data.get("created_at")) if isinstance(incident_data.get("created_at"), str) else incident_data.get("created_at", datetime.utcnow()),
                severity=incident_data.get("severity", "medium"),
                status=incident_data.get("status", "identified"),
                affected_system=incident_data.get("affected_system", "unknown"),
                summary=incident_data.get("summary", ""),
                root_cause_analysis=incident_data.get("root_cause_analysis"),
                evidence_summary=incident_data.get("evidence_summary"),
                impact=incident_data.get("impact"),
                recommendations=incident_data.get("recommendations"),
                notification_sent=int(incident_data.get("notification_sent", False)),
                requires_human_approval=int(incident_data.get("requires_human_approval", True)),
                auto_remediation_enabled=int(incident_data.get("auto_remediation_enabled", False))
            )
            
            session.add(incident)
            session.commit()
            session.refresh(incident)
            
            logger.info(f"Incident created: {incident.incident_id}")
            return incident
            
        except Exception as e:
            session.rollback()
            logger.error(f"Error creating incident: {e}", exc_info=True)
            raise
        finally:
            session.close()
    
    def get_incident_by_id(self, incident_id: str) -> Optional[Incident]:
        """Get incident by ID"""
        session: Session = self.SessionLocal()
        try:
            incident = session.query(Incident).filter(
                Incident.incident_id == incident_id
            ).first()
            return incident
        finally:
            session.close()
    
    def list_incidents(
        self,
        limit: int = 50,
        offset: int = 0,
        severity: Optional[str] = None,
        status: Optional[str] = None,
        order_by: str = "created_at",
        order_desc: bool = True
    ) -> List[Incident]:
        """
        List incidents with filtering and pagination
        
        Args:
            limit: Maximum number of results
            offset: Number of results to skip
            severity: Filter by severity (optional)
            status: Filter by status (optional)
            order_by: Field to order by
            order_desc: Order descending if True
            
        Returns:
            List of Incident objects
        """
        session: Session = self.SessionLocal()
        try:
            query = session.query(Incident)
            
            # Apply filters
            if severity:
                query = query.filter(Incident.severity == severity)
            if status:
                query = query.filter(Incident.status == status)
            
            # Apply ordering
            order_field = getattr(Incident, order_by, Incident.created_at)
            if order_desc:
                query = query.order_by(desc(order_field))
            else:
                query = query.order_by(asc(order_field))
            
            # Apply pagination
            incidents = query.limit(limit).offset(offset).all()
            return incidents
            
        finally:
            session.close()
    
    def update_incident(self, incident_id: str, updates: Dict) -> Optional[Incident]:
        """Update incident fields"""
        session: Session = self.SessionLocal()
        try:
            incident = session.query(Incident).filter(
                Incident.incident_id == incident_id
            ).first()
            
            if not incident:
                return None
            
            # Update allowed fields
            for key, value in updates.items():
                if hasattr(incident, key):
                    setattr(incident, key, value)
            
            session.commit()
            session.refresh(incident)
            
            logger.info(f"Incident updated: {incident_id}")
            return incident
            
        except Exception as e:
            session.rollback()
            logger.error(f"Error updating incident: {e}", exc_info=True)
            raise
        finally:
            session.close()
    
    def get_stats(self) -> Dict:
        """Get database statistics"""
        session: Session = self.SessionLocal()
        try:
            total_count = session.query(Incident).count()
            
            # Count by severity
            severity_counts = {}
            for severity in ["low", "medium", "high", "critical"]:
                count = session.query(Incident).filter(Incident.severity == severity).count()
                severity_counts[severity] = count
            
            # Count by status
            status_counts = {}
            for status in ["identified", "investigating", "resolved", "closed"]:
                count = session.query(Incident).filter(Incident.status == status).count()
                status_counts[status] = count
            
            return {
                "total_incidents": total_count,
                "database_size_bytes": self.get_db_size(),
                "database_size_mb": round(self.get_db_size() / 1024 / 1024, 2),
                "max_size_mb": self.max_size_bytes / 1024 / 1024,
                "size_usage_pct": round((self.get_db_size() / self.max_size_bytes) * 100, 2),
                "severity_breakdown": severity_counts,
                "status_breakdown": status_counts
            }
            
        finally:
            session.close()
