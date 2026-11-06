        # Simplified FastAPI Application for Local Testing
# Works without Azure OpenAI - demonstrates agent workflow with mock RCA

from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import JSONResponse, PlainTextResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import httpx
import logging
import yaml
from datetime import datetime
import asyncio
from collections import deque

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize FastAPI
app = FastAPI(title="AI-Powered Monitoring Platform (Local)")

# Serve static files
import os
static_dir = os.path.join(os.path.dirname(__file__), 'static')
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Load configuration
with open('config/settings.yaml', 'r') as f:
    config = yaml.safe_load(f)

PROMETHEUS_URL = config['monitoring']['prometheus_url']
LOKI_URL = config['monitoring']['loki_url']
API_KEY = config['monitoring']['api_key']

# Store recent incidents for dashboard
recent_incidents = deque(maxlen=50)
alert_count = 0
agent_execution_count = 0
last_rca_time = 0


@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the agent dashboard UI"""
    html_file = os.path.join(os.path.dirname(__file__), 'static', 'index.html')
    if os.path.exists(html_file):
        with open(html_file, 'r') as f:
            return f.read()
    return HTMLResponse(content="<h1>AI Agent Platform</h1><p>Dashboard not found</p>")


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "prometheus": "connected",
        "loki": "connected"
    }


@app.post("/api/v1/alerts/webhook")
async def alertmanager_webhook(request: Request, authorization: str = Header(None)):
    """Webhook endpoint for Alertmanager"""
    
    # Verify API key
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization")
    
    token = authorization.replace("Bearer ", "")
    if token != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    try:
        payload = await request.json()
        logger.info(f"✅ Received alert webhook from Alertmanager")
        
        alerts = payload.get('alerts', [])
        results = []
        
        for alert in alerts:
            alert_name = alert.get('labels', {}).get('alertname', 'Unknown')
            machine = alert.get('labels', {}).get('machine', 'unknown')
            severity = alert.get('labels', {}).get('severity', 'warning')
            
            logger.info(f"📊 Processing alert: {alert_name} on {machine} (severity: {severity})")
            
            # Simulate agent execution
            agent_results = await execute_agents_mock(machine, alert_name, severity)
            
            # Generate mock RCA
            rca = generate_mock_rca(machine, alert_name, severity, agent_results)
            
            results.append({
                "alert": alert_name,
                "machine": machine,
                "severity": severity,
                "incident_id": rca['incident_id'],
                "root_cause": rca['root_cause'],
                "confidence": rca['confidence'],
                "agents_executed": len(agent_results),
                "timestamp": rca['timestamp'],
                "evidence": agent_results,
                "recommendations": rca['recommendations']
            })
            
            logger.info(f"✅ RCA Complete: {rca['root_cause']} (confidence: {rca['confidence']})")
        
        # Store for dashboard with full details
        global alert_count, agent_execution_count, last_rca_time, recent_incidents
        alert_count += len(alerts)
        agent_execution_count += 1
        last_rca_time = datetime.utcnow().timestamp()
        
        # Store incidents with full evidence and recommendations
        for result in results:
            recent_incidents.append(result)
        
        return JSONResponse(content={
            "status": "processed",
            "alerts_received": len(alerts),
            "results": results
        })
    
    except Exception as e:
        logger.error(f"❌ Error processing webhook: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


async def execute_agents_mock(machine: str, alert_name: str, severity: str):
    """Mock agent execution with real Prometheus queries"""
    logger.info(f"🤖 Starting Agent Execution for {machine}")
    
    agents_results = []
    
    # Agent 1: Supervisor
    logger.info("  [1/10] Supervisor Agent: Parsing alert and selecting agents...")
    await asyncio.sleep(0.5)
    agents_results.append({"agent": "supervisor", "status": "completed"})
    
    # Agent 2: Linux Health
    logger.info("  [2/10] Linux Health Agent: Analyzing system resources...")
    try:
        cpu_usage = await get_prometheus_metric(f'100 - (avg(irate(node_cpu_seconds_total{{mode="idle"}}[5m])) * 100)')
        memory_usage = await get_prometheus_metric(f'(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100')
        
        linux_status = "healthy"
        if cpu_usage and float(cpu_usage) > 80:
            linux_status = "degraded"
        
        logger.info(f"     CPU: {cpu_usage:.1f}%, Memory: {memory_usage:.1f}% - Status: {linux_status}")
        agents_results.append({"agent": "linux_health", "status": linux_status, "cpu": cpu_usage, "memory": memory_usage})
    except Exception as e:
        logger.warning(f"     Could not query metrics: {e}")
        agents_results.append({"agent": "linux_health", "status": "unknown"})
    
    await asyncio.sleep(0.3)
    
    # Agent 3: Nginx Monitoring
    logger.info("  [3/10] Nginx Monitoring Agent: Analyzing web server...")
    agents_results.append({"agent": "nginx", "status": "healthy"})
    await asyncio.sleep(0.3)
    
    # Agent 4: Application Health
    logger.info("  [4/10] Application Health Agent: Checking workers...")
    agents_results.append({"agent": "app_health", "status": "healthy"})
    await asyncio.sleep(0.3)
    
    # Agent 5: Database Monitoring
    logger.info("  [5/10] Database Monitoring Agent: Analyzing PostgreSQL...")
    try:
        db_conn = await get_prometheus_metric('pg_stat_database_numbackends')
        max_conn = await get_prometheus_metric('pg_settings_max_connections')
        
        db_status = "healthy"
        if db_conn and max_conn:
            utilization = (float(db_conn) / float(max_conn)) * 100
            if utilization > 90:
                db_status = "critical"
            elif utilization > 80:
                db_status = "degraded"
            logger.info(f"     DB Connections: {db_conn}/{max_conn} ({utilization:.1f}%) - Status: {db_status}")
        
        agents_results.append({"agent": "database", "status": db_status, "connections": db_conn, "utilization": utilization if db_conn and max_conn else 0})
    except Exception as e:
        logger.warning(f"     Could not query DB metrics: {e}")
        agents_results.append({"agent": "database", "status": "unknown"})
    
    await asyncio.sleep(0.3)
    
    # Agent 6: REST API Monitoring
    logger.info("  [6/10] REST API Monitoring Agent: Analyzing API performance...")
    try:
        p95_latency = await get_prometheus_metric('histogram_quantile(0.95, rate(api_request_duration_seconds_bucket[5m]))')
        api_status = "healthy"
        if p95_latency and float(p95_latency) > 2.0:
            api_status = "degraded"
        logger.info(f"     P95 Latency: {p95_latency:.2f}s - Status: {api_status}")
        agents_results.append({"agent": "api_monitoring", "status": api_status, "p95_latency": p95_latency})
    except Exception as e:
        logger.warning(f"     Could not query API metrics: {e}")
        agents_results.append({"agent": "api_monitoring", "status": "unknown"})
    
    await asyncio.sleep(0.3)
    
    # Agent 7: Middleware
    logger.info("  [7/10] Middleware Agent: Checking Redis, WebSocket, Workers...")
    agents_results.append({"agent": "middleware", "status": "healthy"})
    await asyncio.sleep(0.3)
    
    # Agent 8: Log Analysis
    logger.info("  [8/10] Log Analysis Agent: Scanning logs for errors...")
    agents_results.append({"agent": "log_analysis", "status": "completed", "errors_found": 47})
    await asyncio.sleep(0.3)
    
    # Agent 9: Cross-System RCA
    logger.info("  [9/10] Cross-System RCA Agent: Correlating evidence...")
    await asyncio.sleep(0.5)
    agents_results.append({"agent": "rca", "status": "completed"})
    
    # Agent 10: Incident Reporting
    logger.info("  [10/10] Incident Reporting Agent: Generating report...")
    await asyncio.sleep(0.3)
    agents_results.append({"agent": "reporting", "status": "completed"})
    
    logger.info("✅ All agents completed execution!")
    
    return agents_results


async def get_prometheus_metric(query: str):
    """Query Prometheus"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{PROMETHEUS_URL}/api/v1/query",
                params={"query": query},
                timeout=5.0
            )
            data = response.json()
            if data['data']['result']:
                return float(data['data']['result'][0]['value'][1])
    except Exception as e:
        logger.debug(f"Prometheus query failed: {e}")
        return None


def generate_mock_rca(machine: str, alert_name: str, severity: str, agent_results: list):
    """Generate mock RCA report"""
    import uuid
    
    incident_id = f"INC-{datetime.utcnow().strftime('%Y%m%d')}-{str(uuid.uuid4())[:8]}"
    
    # Determine root cause based on alert
    if "Database" in alert_name:
        root_cause = "Database connection pool exhaustion"
        confidence = 0.85
    elif "API" in alert_name or "Latency" in alert_name:
        root_cause = "API performance degradation due to slow queries"
        confidence = 0.78
    elif "CPU" in alert_name:
        root_cause = "High CPU utilization from background processes"
        confidence = 0.72
    else:
        root_cause = "System resource contention detected"
        confidence = 0.65
    
    return {
        "incident_id": incident_id,
        "root_cause": root_cause,
        "confidence": confidence,
        "timestamp": datetime.utcnow().isoformat(),
        "agents_executed": [a['agent'] for a in agent_results],
        "recommendations": [
            "Increase connection pool size",
            "Optimize slow queries",
            "Review application connection lifecycle"
        ]
    }


@app.get("/api/v1/metrics/{machine}")
async def get_machine_metrics(machine: str, authorization: str = Header(None)):
    """Get current metrics for a machine"""
    
    # Verify API key
    if not authorization or authorization.replace("Bearer ", "") != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    try:
        cpu = await get_prometheus_metric('100 - (avg(irate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)')
        memory = await get_prometheus_metric('(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100')
        
        return {
            "machine": machine,
            "timestamp": datetime.utcnow().isoformat(),
            "metrics": {
                "cpu_usage_pct": cpu,
                "memory_usage_pct": memory
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics():
    """Prometheus metrics endpoint for agent execution"""
    global alert_count, agent_execution_count, last_rca_time, recent_incidents
    
    # Calculate success rate
    total_incidents = len(recent_incidents)
    
    metrics_output = f"""# HELP aiops_alerts_total Total alerts received
# TYPE aiops_alerts_total counter
aiops_alerts_total {alert_count}

# HELP aiops_agent_executions_total Total agent workflow executions
# TYPE aiops_agent_executions_total counter
aiops_agent_executions_total {agent_execution_count}

# HELP aiops_last_rca_timestamp Last RCA execution timestamp
# TYPE aiops_last_rca_timestamp gauge
aiops_last_rca_timestamp {last_rca_time}

# HELP aiops_incidents_total Total incidents processed
# TYPE aiops_incidents_total gauge
aiops_incidents_total {total_incidents}

# HELP aiops_agent_status Agent platform status (1=up, 0=down)
# TYPE aiops_agent_status gauge
aiops_agent_status 1
"""
    return metrics_output


@app.get("/api/v1/incidents/recent")
async def get_recent_incidents():
    """Get recent incidents for dashboard"""
    return {
        "incidents": list(recent_incidents),
        "total": len(recent_incidents),
        "timestamp": datetime.utcnow().isoformat()
    }


if __name__ == "__main__":
    import uvicorn
    logger.info("🚀 Starting AI-Powered Monitoring Platform (Local Mode)")
    logger.info(f"📊 Prometheus: {PROMETHEUS_URL}")
    logger.info(f"📝 Loki: {LOKI_URL}")
    uvicorn.run(app, host="0.0.0.0", port=8085)
