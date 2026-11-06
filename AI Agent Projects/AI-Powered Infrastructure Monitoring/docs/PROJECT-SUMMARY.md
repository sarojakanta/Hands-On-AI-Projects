# Project Summary

## AI-Powered Infrastructure Monitoring Platform

### Overview

Enterprise-grade, AI-powered infrastructure monitoring and root cause analysis platform using LangGraph multi-agent orchestration. Monitors three independent Linux-based product installations through a centralized monitoring server with Azure-hosted intelligent RCA.

### Architecture Components

#### 1. Product Machines (01, 02, 03)
- **OS:** AlmaLinux (containerized)
- **Application:** FastAPI with Prometheus & OpenTelemetry instrumentation
- **Database:** PostgreSQL 15
- **Cache:** Redis 7
- **Web Server:** Nginx
- **Monitoring:**
  - Node Exporter (Linux metrics)
  - Postgres Exporter (DB metrics)
  - Redis Exporter (cache metrics)
  - Nginx Exporter (web server metrics)
  - Grafana Alloy (log collection)

#### 2. Monitoring Machine (04)
- **Prometheus:** Metrics storage, scrapes all product machines every 15s
- **Loki:** Log aggregation with 30-day retention
- **Tempo:** Distributed tracing with 14-day retention
- **Grafana:** Unified visualization and dashboards
- **Alertmanager:** Alert routing and grouping
- **OpenTelemetry Collector:** Trace ingestion and processing

#### 3. Azure AI Platform
- **Framework:** LangGraph for multi-agent orchestration
- **API:** FastAPI webhook receiver
- **LLM:** Azure OpenAI (GPT-4)
- **Agents:**
  1. Supervisor Agent (orchestrator)
  2. Linux Health Agent
  3. Nginx Monitoring Agent
  4. Application Health Agent
  5. REST API Monitoring Agent
  6. Database Monitoring Agent
  7. Middleware Agent (Redis/WebSocket/Workers)
  8. Log Analysis Agent
  9. Cross-System RCA Agent
  10. Incident Reporting Agent

### Monitoring Coverage

| Component | Metrics Collected | Alert Rules |
|-----------|------------------|-------------|
| **Linux Infrastructure** | CPU, Memory, Disk, Network, Load | 8 |
| **Application** | Workers, Exceptions, Process Health | 4 |
| **REST API** | Latency (P95/P99), Error Rate, Throughput | 5 |
| **Database** | Connections, Queries, Locks, Cache Hit Ratio | 6 |
| **Nginx** | Connections, Upstream Time, HTTP Errors | 3 |
| **Logs** | Application errors, exceptions, patterns | - |
| **Traces** | Distributed request flows, span analysis | - |

**Total Alert Rules:** 26

### Data Flow

```
Metrics:   Product Exporters ← Prometheus (Pull, 15s interval)
Logs:      Application → Grafana Alloy → Loki (Push)
Traces:    Application → OTel Collector → Tempo (Push)
Alerts:    Prometheus → Alertmanager → Azure AI Platform (Webhook)
RCA Query: Azure AI ← Prometheus/Loki APIs (Pull)
```

### Key Features

#### 1. Multi-Agent RCA Workflow
- Alert triggers LangGraph orchestration
- Agents execute in parallel and sequential phases
- Evidence collection from metrics, logs, and traces
- Temporal correlation and causality analysis
- Confidence-scored root cause determination
- Structured incident reports with recommendations

#### 2. Cross-System Correlation
Example: Database Connection Pool Exhaustion
1. **Database Agent:** Detects 92% connection utilization
2. **API Agent:** Identifies latency spike on /api/v1/reports
3. **Application Agent:** Observes worker saturation (87.5%)
4. **Log Agent:** Finds connection pool error patterns
5. **RCA Agent:** Correlates evidence, builds timeline, determines root cause
6. **Output:** Confidence 0.85, actionable recommendations

#### 3. Optional Component Detection
- Redis, WebSocket, Background Workers are conditional
- Agents detect presence before monitoring
- No false alerts for uninstalled components

#### 4. Security
- API key authentication (Azure ↔ Monitoring)
- TLS encryption for external communication
- Network segmentation
- No direct Azure access to product machines
- Read-only AI operations (no auto-remediation)

### Technology Stack

**Product Layer:**
- Python 3.11, FastAPI, Uvicorn
- PostgreSQL 15, Redis 7, Nginx
- Prometheus Client, OpenTelemetry SDK

**Monitoring Layer:**
- Prometheus, Loki, Tempo, Grafana
- Alertmanager, OpenTelemetry Collector
- Grafana Alloy

**AI Layer:**
- Python 3.11, FastAPI
- LangGraph 0.0.26, LangChain 0.1.6
- Azure OpenAI (GPT-4)
- Azure Cosmos DB (incident storage)

**Deployment:**
- Docker, Docker Compose
- Container networking

### Repository Structure

```
├── product-machine/
│   ├── application/          # FastAPI app with instrumentation
│   └── instrumentation/      # Exporters and collectors
├── monitoring-machine/
│   ├── prometheus/           # Config and alert rules
│   ├── loki/                 # Log aggregation config
│   ├── tempo/                # Tracing config
│   ├── grafana/              # Dashboards and datasources
│   ├── alertmanager/         # Alert routing
│   └── otel-collector/       # Trace collection
├── azure-ai-platform/
│   ├── agents/               # 10 specialized agents
│   ├── api/                  # FastAPI application
│   ├── core/                 # LangGraph orchestrator
│   ├── clients/              # Prometheus/Loki/Tempo clients
│   ├── models/               # Data schemas
│   └── config/               # Configuration
├── deployment/
│   ├── product-01/           # Docker compose
│   ├── product-02/           # Docker compose
│   ├── product-03/           # Docker compose
│   ├── monitoring/           # Docker compose
│   └── scripts/              # Startup/shutdown scripts
├── failure-injection/        # Testing tools
└── docs/                     # Documentation
```

### Deployment

#### Quick Start

```bash
# 1. Deploy monitoring stack
cd deployment/monitoring
docker-compose up -d

# 2. Create network
docker network create deployment_monitoring

# 3. Deploy product machines
cd deployment/product-01 && docker-compose up -d
cd deployment/product-02 && docker-compose up -d
cd deployment/product-03 && docker-compose up -d

# 4. Verify
curl http://localhost:9090/targets  # Prometheus
curl http://localhost:3000          # Grafana (admin/admin)

# 5. Deploy Azure AI Platform (see docs/DEPLOYMENT.md)
```

#### Automated Deployment

```bash
chmod +x deployment/scripts/startup.sh
./deployment/scripts/startup.sh
```

### Testing

#### Failure Injection

```bash
# Database connection exhaustion (RCA example)
python failure-injection/inject_failures.py \
  --machine product-02 \
  --failure-type database-load \
  --duration 600
```

**Expected Outcome:**
- Alert: `CriticalDatabaseConnections`
- RCA identifies connection pool exhaustion
- Evidence from DB metrics, API latency, and logs
- Confidence: 0.85
- Recommendations: Increase max_connections, optimize queries

### Access Points

| Service | URL | Credentials |
|---------|-----|-------------|
| Grafana | http://localhost:3000 | admin / admin |
| Prometheus | http://localhost:9090 | - |
| Alertmanager | http://localhost:9093 | - |
| Product-01 | http://localhost:8001 | - |
| Product-02 | http://localhost:8002 | - |
| Product-03 | http://localhost:8003 | - |
| Azure AI Platform | http://localhost:8080 | API Key |

### Metrics & Performance

**Monitoring Metrics:**
- ~150 unique metrics per product machine
- ~450 total time series (3 machines)
- 15-second scrape interval
- 30-day retention (Prometheus, Loki)
- 14-day retention (Tempo)

**RCA Performance:**
- Alert detection: < 5 minutes
- Webhook delivery: < 30 seconds
- RCA execution: < 2 minutes
- Total incident time: < 8 minutes

### Design Principles

1. **Separation of Concerns:** Product, monitoring, AI layers isolated
2. **Security-First:** No direct Azure access, API authentication required
3. **Modularity:** Optional components, extensible architecture
4. **Scalability:** Designed to expand beyond 3 machines
5. **Read-Only AI:** No automatic remediation without approval
6. **Evidence-Based:** Multi-source correlation for RCA confidence

### Limitations & Considerations

1. **Container Deployment:**
   - Shares host kernel
   - Limited systemd support
   - Not full VM isolation

2. **Current Scale:**
   - Designed for 3-10 machines
   - Requires Prometheus federation for 20+ machines

3. **LLM Costs:**
   - Each RCA invokes Azure OpenAI
   - Consider caching for repeated incidents

4. **No Auto-Remediation:**
   - Read-only by design
   - Human approval required

### Future Roadmap

**Phase 1 (Current):**
- ✅ Multi-machine monitoring
- ✅ LangGraph RCA
- ✅ Cross-system correlation
- ✅ Incident reporting

**Phase 2 (Planned):**
- [ ] ML-based anomaly detection
- [ ] Historical incident similarity matching
- [ ] SLO tracking and alerting
- [ ] Custom per-component dashboards

**Phase 3 (Future):**
- [ ] Auto-remediation with approval workflow
- [ ] Multi-region federated monitoring
- [ ] Advanced trace analysis
- [ ] Predictive failure detection

### License

Enterprise Internal Use Only

### Support

For issues and questions, contact the DevOps team.

---

**Version:** 1.0.0  
**Last Updated:** 2024  
**Status:** Production Ready
