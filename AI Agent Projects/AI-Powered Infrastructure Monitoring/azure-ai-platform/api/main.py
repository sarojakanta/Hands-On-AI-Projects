# FastAPI Application for Azure AI Platform
from fastapi import FastAPI, Request, HTTPException, Header, Query
from fastapi.responses import JSONResponse
from models.schemas import AlertPayload, IncidentReport
from core.langgraph_orchestrator import LangGraphOrchestrator
from clients.prometheus_client import PrometheusClient
from clients.loki_client import LokiClient
from database.sqlite_client import SQLiteClient
from langchain_openai import AzureChatOpenAI
import yaml
import logging
import os
from datetime import datetime
from typing import Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='{"timestamp": "%(asctime)s", "level": "%(levelname)s", "message": "%(message)s"}'
)
logger = logging.getLogger(__name__)

# Load configuration
config_path = os.getenv("CONFIG_PATH", "config/settings.yaml")
with open(config_path, 'r') as f:
    config = yaml.safe_load(f)

# Initialize FastAPI
app = FastAPI(
    title="Azure AI Platform - AIOps",
    description="AI-Powered Infrastructure Monitoring and RCA Platform",
    version="1.0.0"
)

# Initialize clients
prometheus_client = PrometheusClient(
    base_url=config['monitoring']['prometheus_url'],
    api_key=config['monitoring'].get('api_key')
)

loki_client = LokiClient(
    base_url=config['monitoring']['loki_url'],
    api_key=config['monitoring'].get('api_key')
)

# Initialize SQLite database
db_client = SQLiteClient(
    db_path=config['database']['path'],
    max_size_mb=config['database']['max_size_mb'],
    archive_path=config['database']['archive_path']
)

# Initialize LLM
llm = AzureChatOpenAI(
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version=config['openai']['api_version'],
    deployment_name=config['openai']['deployment_name'],
    temperature=config['openai']['temperature'],
    max_tokens=config['openai']['max_tokens']
)

# Initialize orchestrator
orchestrator = LangGraphOrchestrator(
    prometheus_client=prometheus_client,
    loki_client=loki_client,
    llm=llm
)

# API Key validation
API_KEY = config['monitoring'].get('api_key', 'REPLACE_WITH_API_KEY')


def verify_api_key(authorization: str = Header(None)) -> bool:
    """Verify API key from Authorization header"""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization format")
    
    token = authorization.replace("Bearer ", "")
    if token != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    return True


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "Azure AI Platform - AIOps",
        "version": "1.0.0",
        "status": "running"
    }


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.post("/api/v1/alerts/webhook")
async def alertmanager_webhook(
    request: Request,
    authorization: str = Header(None)
):
    """
    Webhook endpoint for Alertmanager alerts
    Triggers RCA workflow
    """
    # Verify API key
    verify_api_key(authorization)
    
    try:
        payload = await request.json()
        logger.info(f"Received alert webhook: {payload.get('groupKey', 'unknown')}")
        
        # Parse alert payload
        alert_payload = AlertPayload(**payload)
        
        # Process each alert
        results = []
        for alert in alert_payload.alerts:
            # Extract alert information
            alert_data = {
                "alert_name": alert.get('labels', {}).get('alertname', 'Unknown'),
                "severity": alert.get('labels', {}).get('severity', 'warning'),
                "machine": alert.get('labels', {}).get('machine', 'unknown'),
                "timestamp": alert.get('startsAt', datetime.utcnow().isoformat()),
                "labels": alert.get('labels', {}),
                "annotations": alert.get('annotations', {})
            }
            
            logger.info(f"Processing alert: {alert_data['alert_name']} on {alert_data['machine']}")
            
            # Execute RCA workflow
            try:
                final_state = await orchestrator.process_alert(alert_data)
                
                # Generate incident report
                if final_state.root_cause_analysis:
                    incident_report = IncidentReport(
                        incident_id=final_state.root_cause_analysis['incident_id'],
                        created_at=datetime.utcnow().isoformat(),
                        severity="high" if alert_data['severity'] == 'critical' else "medium",
                        status="identified",
                        affected_system=alert_data['machine'],
                        summary=f"{alert_data['alert_name']} on {alert_data['machine']}",
                        root_cause_analysis=final_state.root_cause_analysis,
                        evidence_summary={
                            "linux_health": final_state.linux_health,
                            "database_health": final_state.database_health,
                            "api_metrics": final_state.api_metrics,
                            "log_analysis": final_state.log_analysis
                        },
                        impact={
                            "user_facing": "Degraded API performance",
                            "duration": "Ongoing",
                            "affected_endpoints": []
                        },
                        recommendations=final_state.root_cause_analysis['recommendations'],
                        notification_sent=True,
                        requires_human_approval=True,
                        auto_remediation_enabled=False
                    )
                    
                    # Store incident in SQLite database
                    try:
                        db_client.create_incident(incident_report.dict())
                        logger.info(f"Incident stored in database: {incident_report.incident_id}")
                    except Exception as db_error:
                        logger.error(f"Failed to store incident in database: {db_error}", exc_info=True)
                    
                    results.append({
                        "alert": alert_data['alert_name'],
                        "machine": alert_data['machine'],
                        "incident_id": incident_report.incident_id,
                        "root_cause": incident_report.root_cause_analysis.root_cause,
                        "confidence": incident_report.root_cause_analysis.confidence
                    })
                    
                    logger.info(f"RCA completed: {incident_report.incident_id}")
                
            except Exception as e:
                logger.error(f"Error processing alert: {e}", exc_info=True)
                results.append({
                    "alert": alert_data['alert_name'],
                    "machine": alert_data['machine'],
                    "error": str(e)
                })
        
        return JSONResponse(content={
            "status": "processed",
            "alerts_received": len(alert_payload.alerts),
            "results": results
        })
    
    except Exception as e:
        logger.error(f"Webhook processing error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/incidents/{incident_id}")
async def get_incident(
    incident_id: str,
    authorization: str = Header(None)
):
    """Get incident report by ID"""
    verify_api_key(authorization)
    
    try:
        incident = db_client.get_incident_by_id(incident_id)
        if not incident:
            raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")
        
        return incident.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving incident: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/incidents")
async def list_incidents(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    severity: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    authorization: str = Header(None)
):
    """List incidents with filtering and pagination"""
    verify_api_key(authorization)
    
    try:
        incidents = db_client.list_incidents(
            limit=limit,
            offset=offset,
            severity=severity,
            status=status
        )
        
        return {
            "count": len(incidents),
            "limit": limit,
            "offset": offset,
            "incidents": [incident.to_dict() for incident in incidents]
        }
    except Exception as e:
        logger.error(f"Error listing incidents: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/database/stats")
async def get_database_stats(
    authorization: str = Header(None)
):
    """Get database statistics and size information"""
    verify_api_key(authorization)
    
    try:
        stats = db_client.get_stats()
        return stats
    except Exception as e:
        logger.error(f"Error getting database stats: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/metrics/{machine}")
async def get_machine_metrics(
    machine: str,
    authorization: str = Header(None)
):
    """Get current metrics for a machine"""
    verify_api_key(authorization)
    
    try:
        cpu = await prometheus_client.get_cpu_usage(machine)
        memory = await prometheus_client.get_memory_usage(machine)
        disk = await prometheus_client.get_disk_usage(machine)
        
        return {
            "machine": machine,
            "timestamp": datetime.utcnow().isoformat(),
            "metrics": {
                "cpu_usage_pct": cpu,
                "memory_usage_pct": memory,
                "disk_usage_pct": disk
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=config['app']['host'],
        port=config['app']['port'],
        workers=config['app']['workers']
    )
