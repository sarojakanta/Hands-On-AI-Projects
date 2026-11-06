# AI-Powered Infrastructure Monitoring Platform

Enterprise-grade, AI-powered infrastructure monitoring and root cause analysis platform using LangGraph multi-agent orchestration.

## Architecture Overview

This platform monitors three independent Linux-based product installations through a centralized monitoring server and leverages Azure-hosted AI agents for intelligent anomaly detection and root cause analysis.

### System Components

1. **Product Machines (01, 02, 03)** - Independent AlmaLinux installations with full monitoring instrumentation
2. **Monitoring Machine (04)** - Centralized observability stack (Prometheus, Loki, Tempo, Grafana)
3. **Azure AI Platform** - LangGraph-based multi-agent system for intelligent RCA

## Repository Structure

```
├── product-machine/          # Product installation instrumentation
│   ├── instrumentation/      # Monitoring exporters and collectors
│   ├── application/          # Sample application with instrumentation
│   └── scripts/              # Setup and utility scripts
├── monitoring-machine/       # Centralized monitoring stack
│   ├── prometheus/           # Metrics storage and querying
│   ├── loki/                 # Log aggregation
│   ├── tempo/                # Distributed tracing
│   ├── grafana/              # Visualization and dashboards
│   ├── alertmanager/         # Alert routing
│   ├── otel-collector/       # OpenTelemetry collection
│   └── nginx/                # Reverse proxy for secure API access
├── azure-ai-platform/        # LangGraph AI platform
│   ├── agents/               # Specialized monitoring agents
│   ├── api/                  # FastAPI application
│   ├── core/                 # LangGraph orchestration
│   ├── models/               # Data models
│   ├── clients/              # Prometheus/Loki/Tempo API clients
│   └── config/               # Configuration
├── deployment/               # Docker compose and deployment configs
│   ├── product-01/           # Product machine 01 deployment
│   ├── product-02/           # Product machine 02 deployment
│   ├── product-03/           # Product machine 03 deployment
│   ├── monitoring/           # Monitoring stack deployment
│   └── scripts/              # Deployment automation
├── failure-injection/        # Controlled failure testing
└── docs/                     # Architecture and design documentation
```

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Python 3.11+
- Azure subscription (for AI platform)
- Network connectivity between machines

### 1. Deploy Monitoring Stack

```bash
cd deployment/monitoring
docker-compose up -d
```

### 2. Deploy Product Machines

```bash
# Deploy Product 01
cd deployment/product-01
docker-compose up -d

# Deploy Product 02
cd deployment/product-02
docker-compose up -d

# Deploy Product 03
cd deployment/product-03
docker-compose up -d
```

### 3. Deploy Azure AI Platform

```bash
cd azure-ai-platform
# Configure Azure credentials
cp config/config.example.yaml config/config.yaml
# Edit config.yaml with your settings

# Deploy to Azure (instructions in docs/azure-deployment.md)
```

## Monitoring Scope

Each product machine monitors:

- **Infrastructure**: CPU, memory, disk, network (Node Exporter)
- **Web Server**: Nginx connections, response times, error rates
- **Application**: Process health, worker utilization, exceptions
- **Database**: PostgreSQL connections, queries, locks
- **REST API**: Latency, throughput, error rates per endpoint
- **Optional Components**: Redis, WebSocket, Background Workers
- **Logs**: Application, system, and error logs

## AI Agents

The platform includes specialized LangGraph agents:

1. **Supervisor Agent** - Orchestrates RCA workflow
2. **Linux Health Agent** - OS-level resource analysis
3. **Nginx Monitoring Agent** - Web server health
4. **Application Health Agent** - Application process monitoring
5. **REST API Monitoring Agent** - API performance analysis
6. **Database Monitoring Agent** - Database health and queries
7. **Middleware Agent** - Redis, WebSocket, Workers (conditional)
8. **Log Analysis Agent** - Error pattern detection
9. **Cross-System RCA Agent** - Root cause correlation
10. **Incident Reporting Agent** - Report generation and notification

## Security

- TLS encryption for all external communication
- API key authentication for Azure ↔ Monitoring communication
- Network segmentation between product and monitoring networks
- No direct Azure access to product machines
- Read-only AI operations (no automatic remediation)

## Documentation

- [Architecture Design](docs/architecture-design.md) - Complete technical design
- [Deployment Guide](docs/deployment-guide.md) - Step-by-step deployment
- [Monitoring Metrics Catalogue](docs/metrics-catalogue.md) - All metrics reference
- [Agent Architecture](docs/agent-architecture.md) - LangGraph agent details
- [Failure Injection Guide](docs/failure-injection.md) - Testing procedures

## Technology Stack

- **Monitoring**: Prometheus, Loki, Tempo, Grafana, Alertmanager
- **Instrumentation**: Node Exporter, Postgres Exporter, Redis Exporter, Grafana Alloy
- **Tracing**: OpenTelemetry (SDK and Collector)
- **AI Platform**: Python, FastAPI, LangGraph, LangChain, Azure OpenAI
- **Deployment**: Docker, Docker Compose
- **Database**: PostgreSQL (primary), MySQL (supported)

## License

Enterprise Internal Use Only

## Support

For issues and questions, contact the DevOps team.
