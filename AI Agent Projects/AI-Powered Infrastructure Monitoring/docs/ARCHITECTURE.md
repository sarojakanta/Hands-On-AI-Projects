# Architecture Documentation

## System Overview

The AI-Powered Infrastructure Monitoring Platform is a multi-tier observability and automated RCA system designed to monitor three independent product installations.

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                  AZURE AI PLATFORM                          │
│  ┌────────────────────────────────────────────────────┐     │
│  │  LangGraph Multi-Agent Orchestration               │     │
│  │  - Supervisor Agent                                │     │
│  │  - 9 Specialized Agents                            │     │
│  │  - FastAPI Webhook Receiver                        │     │
│  └────────────────────────────────────────────────────┘     │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTPS/TLS
                           │ API Queries Only
                           ▼
┌─────────────────────────────────────────────────────────────┐
│              MONITORING MACHINE 04                          │
│  ┌────────────────────────────────────────────────────┐     │
│  │  Prometheus | Loki | Tempo | Grafana | Alertmgr   │     │
│  └────────────────────────────────────────────────────┘     │
└──────┬───────────────┬───────────────┬──────────────────────┘
       │               │               │
       ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│  Product 01  │ │  Product 02  │ │  Product 03  │
│  AlmaLinux   │ │  AlmaLinux   │ │  AlmaLinux   │
└──────────────┘ └──────────────┘ └──────────────┘
```

## Component Responsibilities

### Product Machines (01, 02, 03)

Each product machine is a complete, independent installation:

**Application Stack:**
- Nginx (web server)
- FastAPI application
- PostgreSQL database
- Redis (optional)
- Background workers (optional)

**Monitoring Instrumentation:**
- Node Exporter (Linux metrics)
- Postgres Exporter (DB metrics)
- Redis Exporter (cache metrics)
- Nginx Exporter (web server metrics)
- Application instrumentation (Prometheus client)
- OpenTelemetry SDK (distributed tracing)
- Grafana Alloy (log collection)

### Monitoring Machine (04)

Centralized observability platform:

**Metrics:**
- Prometheus (scrapes metrics from all 3 machines)
- Stores 30 days retention
- Executes alerting rules

**Logs:**
- Loki (receives logs from Grafana Alloy)
- 30 days retention
- LogQL query engine

**Traces:**
- OpenTelemetry Collector (receives spans)
- Tempo (trace storage)
- 14 days retention

**Visualization:**
- Grafana (unified dashboards)
- Correlates metrics, logs, traces

**Alerting:**
- Alertmanager (routes alerts to Azure)

### Azure AI Platform

Intelligent RCA and incident management:

**API Layer:**
- FastAPI application
- Webhook receiver for Alertmanager
- REST API for incident queries

**LangGraph Orchestration:**
- Supervisor Agent (coordinates workflow)
- Parallel agent execution
- State management

**Specialized Agents:**
1. Linux Health Agent
2. Nginx Monitoring Agent
3. Application Health Agent
4. REST API Monitoring Agent
5. Database Monitoring Agent
6. Middleware Agent
7. Log Analysis Agent
8. Cross-System RCA Agent
9. Incident Reporting Agent

**LLM Integration:**
- Azure OpenAI (GPT-4)
- Structured prompts
- Root cause reasoning

**Storage:**
- Cosmos DB (incident history)
- Blob Storage (reports)

## Data Flow

### Telemetry Collection

**Metrics (Pull):**
```
Product Machine Exporters ← Prometheus (scrapes every 15s)
```

**Logs (Push):**
```
Application → Grafana Alloy → Loki
```

**Traces (Push):**
```
Application → OTel Collector → Tempo
```

### Alert Flow

```
1. Prometheus evaluates rules
2. Alert fires
3. Alertmanager receives alert
4. Alertmanager sends webhook to Azure AI Platform
5. Azure AI Platform processes alert:
   - Supervisor parses alert
   - Agents execute (query Prometheus/Loki)
   - RCA agent correlates findings
   - Incident report generated
   - Notification sent
```

## Network Architecture

**Monitoring Network:**
- Prometheus can reach all product machine exporters
- Loki receives from all Alloy instances
- OTel Collector receives from all applications

**Security:**
- Azure AI Platform uses API key authentication
- TLS for external communication
- No direct Azure access to product machines
- All communication via monitoring APIs

## Scalability

**Current Design:**
- 3 product machines
- 1 monitoring machine
- 1 Azure AI platform

**Expansion:**
- Add product machines by updating Prometheus scrape configs
- Scale monitoring machine vertically or federate Prometheus
- Scale Azure AI Platform horizontally

## Technology Stack

| Layer | Technology |
|-------|-----------|
| **Product Application** | FastAPI, Python |
| **Database** | PostgreSQL (primary), MySQL (supported) |
| **Cache** | Redis |
| **Web Server** | Nginx |
| **Metrics** | Prometheus, Node Exporter, Postgres Exporter |
| **Logs** | Loki, Grafana Alloy |
| **Traces** | OpenTelemetry, Tempo |
| **Visualization** | Grafana |
| **Alerting** | Alertmanager |
| **AI Platform** | Python, FastAPI, LangGraph, LangChain |
| **LLM** | Azure OpenAI (GPT-4) |
| **Storage** | Azure Cosmos DB, Blob Storage |
| **Deployment** | Docker, Docker Compose |

## Design Decisions

### Why Pull for Metrics?

- Standard Prometheus model
- No credential management on product machines
- Monitoring controls scrape frequency
- Easy service discovery

### Why Push for Logs?

- Grafana Alloy standard model
- No exposed ports on product machines
- Centralized ingestion
- Better for high-volume logs

### Why LangGraph?

- Orchestrates complex multi-agent workflows
- State management
- Conditional execution paths
- Parallel agent execution
- Checkpointing for long-running RCA

### Why Separate Azure Platform?

- Security isolation
- Independent scaling
- Managed LLM access
- Persistent incident storage
- Cost optimization (only run when needed)

## Monitoring Coverage

Each product machine monitors:

| Category | Metrics | Alerts |
|----------|---------|--------|
| **Infrastructure** | CPU, Memory, Disk, Network | 8 rules |
| **Application** | Workers, Exceptions, Health | 4 rules |
| **API** | Latency, Errors, Throughput | 5 rules |
| **Database** | Connections, Queries, Locks | 6 rules |
| **Web Server** | Connections, Response Times | 3 rules |
| **Logs** | Errors, Exceptions, Patterns | N/A |
| **Traces** | Distributed request flows | N/A |

## Limitations

1. **Container-based deployment:**
   - Shares kernel with host
   - Limited systemd support
   - Not full Linux VM isolation

2. **Horizontal scaling:**
   - Current design for 3 machines
   - Requires Prometheus federation for 10+ machines

3. **LLM costs:**
   - Each RCA invokes Azure OpenAI
   - Consider caching for repeated issues

4. **No auto-remediation:**
   - Read-only by design
   - Requires human approval

## Future Enhancements

1. **Anomaly detection:** ML-based baseline learning
2. **Auto-remediation:** With approval workflow
3. **Incident similarity:** Historical pattern matching
4. **Custom dashboards:** Per-component views
5. **SLO tracking:** Service-level objectives
6. **Multi-region:** Federated monitoring
