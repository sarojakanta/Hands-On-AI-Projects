# AI Agent Platform - Automated Root Cause Analysis

A containerized AI agent platform that performs automated Root Cause Analysis (RCA) for infrastructure incidents using 10 specialized AI agents. Features a web dashboard, persistent SQLite database, and seamless integration with Prometheus and Loki.

## 🚀 Quick Start

### Prerequisites
- Docker and Docker Compose
- 4GB+ RAM available  
- Port 8085 available

### Start the Agent (3 steps)

```bash
# 1. Navigate to platform directory
cd azure-ai-platform

# 2. Copy environment template  
copy env-template.txt .env

# 3. Start the container
docker-compose up -d --build
```

**Dashboard:** http://localhost:8085

## ✨ Features

✅ **10 Specialized AI Agents** - Automated multi-system analysis  
✅ **Persistent SQLite Database** - All incidents saved permanently  
✅ **Interactive Web Dashboard** - Real-time incident monitoring  
✅ **Prometheus Integration** - Live metrics collection  
✅ **Loki Integration** - Log analysis  
✅ **Cloud-Agnostic** - Deploy anywhere (laptop, Azure, AWS, GCP)  
✅ **Auto-Archiving** - Database backup when size limit reached  

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────┐
│          Docker Container (Port 8085)                │
│                                                      │
│  ┌──────────────┐      ┌────────────────────────┐  │
│  │   FastAPI    │◄────►│   10 AI Agents         │  │
│  │  + Web GUI   │      │  - Supervisor          │  │
│  └──────────────┘      │  - Linux Health        │  │
│                        │  - Database            │  │
│  ┌──────────────┐      │  - Nginx               │  │
│  │   SQLite DB  │      │  - API Monitoring      │  │
│  │  Persistent  │      │  - Application Health  │  │
│  └──────────────┘      │  - Middleware          │  │
│                        │  - Log Analysis        │  │
│  ┌──────────────┐      │  - RCA Engine          │  │
│  │ Prometheus/  │      │  - Incident Reporter   │  │
│  │ Loki Clients │      └────────────────────────┘  │
│  └──────────────┘                                   │
└──────────────────────────────────────────────────────┘
         ▲                           │
         │                           ▼
   Alertmanager              Data Volume
    Webhooks              (incidents.db + archive)
```

## 📊 Dashboard

The web dashboard at http://localhost:8085 provides:

- **Real-time Statistics**: Alerts processed, incidents, agent executions
- **Incident Table**: All RCA reports with severity and confidence scoring
- **Agent Status**: Visual display of all 10 agents
- **Detailed Reports**: Click any incident to view full RCA with evidence and recommendations
- **Database Stats**: Storage usage and auto-archive status

## 🔧 Configuration

Edit `.env` file (created from `env-template.txt`):

```bash
# Database Settings
DB_PATH=data/incidents.db          # Database location
DB_MAX_SIZE_MB=100                 # Max size before auto-archive
DB_ARCHIVE_PATH=data/archive       # Archive directory

# Security
MONITORING_API_KEY=test-api-key-12345  # Change in production!

# Application
LOG_LEVEL=INFO                     # DEBUG, INFO, WARNING, ERROR
```

Monitoring endpoints are configured in `config/settings.yaml`:
```yaml
monitoring:
  prometheus_url: "http://localhost:9090"
  loki_url: "http://localhost:3100"
  api_key: "test-api-key-12345"
```

## 📡 API Endpoints

### Dashboard
```http
GET /
Returns: HTML dashboard interface
```

### Alert Webhook (from Alertmanager)
```http
POST /api/v1/alerts/webhook
Authorization: Bearer <api-key>
Content-Type: application/json

Body: Alertmanager alert payload
```

### Get Recent Incidents
```http
GET /api/v1/incidents/recent
Returns: JSON array of incidents from database
```

### Database Statistics
```http
GET /api/v1/database/stats
Returns: {
  "total_incidents": 4,
  "database_size_mb": 0.02,
  "max_size_mb": 100.0,
  "severity_breakdown": {...},
  "status_breakdown": {...}
}
```

### Health Check
```http
GET /health
Returns: {"status": "healthy", "database": "connected"}
```

### Metrics (Prometheus format)
```http
GET /metrics
Returns: Prometheus-compatible metrics
```

## 🧪 Testing

### Send Test Alerts

Create `test_alert.json`:
```json
{
  "alerts": [{
    "labels": {
      "alertname": "HighDatabaseConnections",
      "machine": "product-01",
      "severity": "critical"
    },
    "annotations": {
      "summary": "Database connections at 95%"
    },
    "startsAt": "2026-11-06T04:00:00Z"
  }]
}
```

Send to agent:
```powershell
Invoke-RestMethod -Uri "http://localhost:8085/api/v1/alerts/webhook" `
  -Method POST `
  -Headers @{"Authorization"="Bearer test-api-key-12345"; "Content-Type"="application/json"} `
  -Body (Get-Content test_alert.json -Raw)
```

## 💾 Data Persistence

All data stored in Docker volume `azure-ai-platform_agent-data`:

```
/app/data/
├── incidents.db          # SQLite database with all RCA incidents
└── archive/              # Archived databases (when size limit reached)
```

### Backup/Restore

```bash
# Backup database
docker run --rm -v azure-ai-platform_agent-data:/data -v $(pwd):/backup alpine tar czf /backup/incidents-backup.tar.gz /data

# Restore database
docker run --rm -v azure-ai-platform_agent-data:/data -v $(pwd):/backup alpine tar xzf /backup/incidents-backup.tar.gz -C /
```

## 🛠️ Management Commands

```bash
# View logs
docker-compose logs -f

# Restart agent
docker-compose restart

# Stop agent
docker-compose down

# Rebuild and start
docker-compose down
docker-compose up -d --build

# Access container shell
docker exec -it azure-ai-agent bash

# View database stats
curl http://localhost:8085/api/v1/database/stats
```

## 🌐 Deployment to Cloud

### Azure Container Instances

```bash
# Build and push image
docker build -t your-registry.azurecr.io/ai-agent:latest .
az acr login --name your-registry
docker push your-registry.azurecr.io/ai-agent:latest

# Deploy
az container create \
  --resource-group your-rg \
  --name ai-agent \
  --image your-registry.azurecr.io/ai-agent:latest \
  --ports 8085 \
  --environment-variables \
    DB_PATH=/app/data/incidents.db \
    MONITORING_API_KEY=your-secure-key
```

### AWS ECS/Fargate

```bash
# Build and push to ECR
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account>.dkr.ecr.us-east-1.amazonaws.com
docker build -t ai-agent .
docker tag ai-agent:latest <account>.dkr.ecr.us-east-1.amazonaws.com/ai-agent:latest
docker push <account>.dkr.ecr.us-east-1.amazonaws.com/ai-agent:latest

# Deploy via ECS console or CLI
```

## 🐛 Troubleshooting

### Container won't start
```bash
# Check logs
docker-compose logs

# Verify port 8085 is free
netstat -ano | findstr :8085
```

### Dashboard shows no incidents
- Wait 30 seconds after sending alerts
- Refresh browser
- Check database: `curl http://localhost:8085/api/v1/database/stats`
- View logs: `docker-compose logs -f`

### Alerts not triggering RCA
- Verify Alertmanager webhook configured to http://azure-ai-agent:8080/api/v1/alerts/webhook
- Check API key matches in `.env` and `alertmanager.yml`
- Test manually with curl/Postman

### Database errors
```bash
# Check stats
curl http://localhost:8085/api/v1/database/stats

# Reset database (WARNING: deletes all data)
docker-compose down -v
docker-compose up -d
```

## 📁 Project Structure

```
azure-ai-platform/
├── agents/                    # 10 specialized AI agents
│   ├── supervisor_agent.py
│   ├── linux_health_agent.py
│   ├── database_agent.py
│   ├── nginx_agent.py
│   ├── app_health_agent.py
│   ├── api_monitoring_agent.py
│   ├── middleware_agent.py
│   ├── log_analysis_agent.py
│   ├── rca_agent.py
│   └── incident_reporting_agent.py
├── api/
│   ├── simple_main_db.py      # Main app with GUI + Database
│   └── static/                # Dashboard HTML/CSS/JS
├── database/
│   ├── sqlite_client.py       # SQLite client with auto-archiving
│   └── models.py              # Database models
├── clients/
│   ├── prometheus_client.py   # Prometheus API client
│   └── loki_client.py         # Loki API client
├── config/
│   └── settings.yaml          # Application configuration
├── docker-compose.yml         # Container orchestration
├── Dockerfile                 # Container image
└── env-template.txt           # Environment template
```

## 🤖 The 10 AI Agents

1. **Supervisor Agent** - Orchestrates workflow, selects relevant agents
2. **Linux Health Agent** - Analyzes CPU, memory, disk usage
3. **Database Agent** - PostgreSQL connections, queries, performance
4. **Nginx Agent** - Web server health, connections, response times
5. **Application Health Agent** - Process health, workers, exceptions
6. **API Monitoring Agent** - Latency, throughput, error rates
7. **Middleware Agent** - Redis, WebSocket, background workers
8. **Log Analysis Agent** - Error pattern detection from logs
9. **RCA Agent** - Cross-system correlation, root cause identification
10. **Incident Reporting Agent** - Report generation, recommendations

## 📈 How It Works

1. **Alert Received** - Alertmanager sends webhook to agent
2. **Agent Execution** - All 10 agents analyze their respective areas
3. **Evidence Collection** - Each agent gathers system data and metrics
4. **Correlation** - RCA agent correlates evidence across systems
5. **Root Cause Identification** - Identifies most likely root cause with confidence score
6. **Report Generation** - Creates detailed incident report with recommendations
7. **Database Storage** - Saves incident to SQLite for persistence
8. **Dashboard Update** - New incident appears in web dashboard

## 📝 License

MIT License

## 💬 Support

- View logs: `docker-compose logs`
- Check database: `curl http://localhost:8085/api/v1/database/stats`
- Dashboard: http://localhost:8085
- API docs: http://localhost:8085/docs (FastAPI auto-generated)

---

**Happy Monitoring! 🚀**
