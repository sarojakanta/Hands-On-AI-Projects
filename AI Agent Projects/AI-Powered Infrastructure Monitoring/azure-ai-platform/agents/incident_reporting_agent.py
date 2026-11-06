# Incident Reporting Agent
from typing import Dict, Any
from models.schemas import AgentState, IncidentReport, RCAOutput
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class IncidentReportingAgent:
    """Generates final incident reports and handles notifications"""
    
    def __init__(self):
        self.name = "incident_reporting"
    
    async def execute(self, state: AgentState) -> IncidentReport:
        """Generate final incident report"""
        logger.info(f"Incident Reporting Agent generating report for {state.affected_machine}")
        
        try:
            if not state.root_cause_analysis:
                raise ValueError("No RCA analysis available")
            
            rca = state.root_cause_analysis
            
            # Build evidence summary
            evidence_summary = {
                "metrics": {},
                "logs": {}
            }
            
            if state.linux_health:
                evidence_summary["metrics"]["linux"] = {
                    "status": state.linux_health.get("status"),
                    "anomaly_count": len(state.linux_health.get("anomalies", []))
                }
            
            if state.database_health:
                evidence_summary["metrics"]["database"] = {
                    "status": state.database_health.get("status"),
                    "connection_utilization": state.database_health.get("metrics", {}).get("connection_utilization"),
                    "anomaly_count": len(state.database_health.get("anomalies", []))
                }
            
            if state.api_metrics:
                evidence_summary["metrics"]["api"] = {
                    "status": state.api_metrics.get("status"),
                    "p95_latency": state.api_metrics.get("metrics", {}).get("p95_latency_seconds"),
                    "anomaly_count": len(state.api_metrics.get("anomalies", []))
                }
            
            if state.log_analysis:
                evidence_summary["logs"]["error_patterns"] = state.log_analysis.get("metrics", {}).get("exception_patterns", {})
                evidence_summary["logs"]["total_errors"] = state.log_analysis.get("metrics", {}).get("total_errors_30min", 0)
            
            # Determine severity
            severity = "medium"
            if state.alert_severity == "critical":
                severity = "high"
            elif state.alert_severity == "warning":
                severity = "medium"
            
            # Build impact assessment
            impact = {
                "user_facing": "Potential service degradation",
                "duration": "Ongoing",
                "affected_endpoints": [],
                "error_rate": evidence_summary.get("metrics", {}).get("api", {}).get("error_rate", "unknown")
            }
            
            # Extract affected endpoints from API metrics
            if state.api_metrics and state.api_metrics.get("anomalies"):
                for anomaly in state.api_metrics["anomalies"]:
                    if "endpoint" in anomaly.get("details", ""):
                        impact["affected_endpoints"].append(anomaly.get("endpoint", "unknown"))
            
            # Create incident report
            incident_report = IncidentReport(
                incident_id=rca["incident_id"],
                created_at=datetime.utcnow().isoformat(),
                severity=severity,
                status="identified",
                affected_system=state.affected_machine,
                summary=f"{state.alert_name} on {state.affected_machine}: {rca['root_cause']}",
                root_cause_analysis=rca,
                evidence_summary=evidence_summary,
                impact=impact,
                recommendations=[r["action"] for r in rca.get("recommendations", [])],
                similar_incidents=[],
                notification_sent=True,
                requires_human_approval=True,
                auto_remediation_enabled=False
            )
            
            state.incident_report = incident_report.dict()
            state.execution_steps.append(f"Incident report generated: {incident_report.incident_id}")
            
            logger.info(f"Incident report completed: {incident_report.incident_id}")
            
            return incident_report
        
        except Exception as e:
            logger.error(f"Incident Reporting Agent error: {e}")
            state.errors.append(f"Incident Reporting Agent: {str(e)}")
            raise
