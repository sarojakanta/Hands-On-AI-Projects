# Quick Start Guide

Get the AI-Powered Infrastructure Monitoring Platform running in 10 minutes.

## Prerequisites

✅ Docker and Docker Compose installed  
✅ 16GB+ RAM  
✅ 50GB+ disk space  
✅ Ports available: 3000, 8080-8082, 9090, 9093, 9100-9115, 9187-9189

## 1. Clone/Extract Repository

```bash
cd AI-Powered-Infrastructure-Monitoring
```

## 2. Start Everything

### Option A: Automated Script (Recommended)

```bash
chmod +x deployment/scripts/startup.sh
./deployment/scripts/startup.sh
```

### Option B: Manual Deployment

```bash
# Start monitoring stack
cd deployment/monitoring
docker-compose up -d

# Create network
docker network create deployment_monitoring

# Start product machines
cd ../product-01 && docker-compose up -d
cd ../product-02 && docker-compose up -d
cd ../product-03 && docker-compose up -d
```

## 3. Verify Deployment

Wait 2-3 minutes for all services to start, then check:

```bash
# Check services
docker ps | grep product
docker ps | grep prometheus

# Test endpoints
curl http://localhost:9090/-/healthy    # Prometheus
curl http://localhost:3000/api/health   # Grafana
curl http://localhost:8001/health       # Product-01
curl http://localhost:8002/health       # Product-02
curl http://localhost:8003/health       # Product-03
```

## 4. Access Dashboards

### Grafana
🔗 http://localhost:3000  
👤 Username: `admin`  
🔑 Password: `admin`

**First Login:**
1. Skip password change (or set new password)
2. Go to Configuration → Data Sources
3. Verify Prometheus, Loki, Tempo are connected ✅

### Prometheus
🔗 http://localhost:9090  
👁️ Check Status → Targets (all should be UP)

### Alertmanager
🔗 http://localhost:9093

## 5. Test the System

### Generate Test Traffic

```bash
# Test API endpoints
curl http://localhost:8001/api/v1/users
curl http://localhost:8002/api/v1/reports
curl http://localhost:8003/api/v1/data
```

### View Metrics in Prometheus

Navigate to http://localhost:9090/graph and try these queries:

```promql
# CPU usage
rate(node_cpu_seconds_total{mode!="idle"}[5m])

# Database connections
pg_stat_database_numbackends

# API latency
histogram_quantile(0.95, rate(api_request_duration_seconds_bucket[5m]))

# Request rate
rate(api_requests_total[5m])
```

### Check Logs in Grafana

1. Go to Grafana → Explore
2. Select "Loki" datasource
3. Query: `{job="application"}`
4. See application logs flowing

## 6. Trigger a Test Alert

### Option 1: Database Load (Recommended)

```bash
cd failure-injection
python inject_failures.py --machine product-02 --failure-type database-load --duration 300
```

**What happens:**
1. Database connections increase (wait 5 min)
2. Alert fires: `HighDatabaseConnections`
3. View in Prometheus: http://localhost:9090/alerts
4. View in Alertmanager: http://localhost:9093

### Option 2: API Latency

```bash
python inject_failures.py --machine product-01 --failure-type api-latency --duration 180
```

## 7. Deploy Azure AI Platform (Optional)

### Local Deployment

```bash
cd azure-ai-platform

# Set environment variables
export AZURE_OPENAI_API_KEY="your-key"
export AZURE_OPENAI_ENDPOINT="https://your-resource.openai.azure.com/"

# Build and run
docker build -t azure-ai-platform:latest .
docker run -d \
  --name azure-ai-platform \
  --network deployment_monitoring \
  -p 8080:8080 \
  -e AZURE_OPENAI_API_KEY=$AZURE_OPENAI_API_KEY \
  -e AZURE_OPENAI_ENDPOINT=$AZURE_OPENAI_ENDPOINT \
  azure-ai-platform:latest

# Test
curl http://localhost:8080/health
```

### Update Alertmanager Webhook

```bash
# Edit monitoring-machine/alertmanager/alertmanager.yml
# Change webhook URL to: http://azure-ai-platform:8080/api/v1/alerts/webhook

# Restart Alertmanager
cd deployment/monitoring
docker-compose restart alertmanager
```

## 8. Monitor the RCA Workflow

When an alert fires:

```bash
# Watch Azure AI Platform logs
docker logs -f azure-ai-platform
```

**Expected log flow:**
```
INFO - Received alert webhook: HighDatabaseConnections
INFO - Supervisor processing alert: HighDatabaseConnections on product-02
INFO - Linux Health Agent analyzing product-02
INFO - Database Agent analyzing product-02: status=critical
INFO - API Monitoring Agent analyzing product-02
INFO - Log Analysis Agent analyzing product-02
INFO - RCA Agent analyzing product-02
INFO - RCA completed: Database connection pool exhaustion
```

## Troubleshooting

### Services Not Starting

```bash
# Check logs
docker-compose logs prometheus
docker-compose logs loki

# Restart specific service
docker-compose restart prometheus
```

### Targets Not Showing in Prometheus

```bash
# Wait 1-2 minutes for initial scrape
# Check network connectivity
docker network inspect deployment_monitoring

# Verify product machine is running
docker ps | grep product-01
```

### No Metrics in Grafana

```bash
# Test Prometheus datasource
curl http://localhost:9090/api/v1/query?query=up

# Check Grafana logs
docker logs grafana
```

### Alerts Not Firing

```bash
# Check if metrics are flowing
curl 'http://localhost:9090/api/v1/query?query=node_memory_MemAvailable_bytes'

# Check alert rules loaded
curl http://localhost:9090/api/v1/rules

# Manually evaluate expression
curl 'http://localhost:9090/api/v1/query?query=rate(node_cpu_seconds_total[5m])'
```

## Shutdown

```bash
# Automated
./deployment/scripts/shutdown.sh

# Manual
cd deployment/product-01 && docker-compose down
cd ../product-02 && docker-compose down
cd ../product-03 && docker-compose down
cd ../monitoring && docker-compose down
docker stop azure-ai-platform
```

## Next Steps

✅ Explore Grafana dashboards  
✅ Review alert rules in `monitoring-machine/prometheus/rules/`  
✅ Run failure injection tests  
✅ Read full documentation in `docs/`  
✅ Customize thresholds and agents  

## Useful Commands

```bash
# View all running containers
docker ps

# Check resource usage
docker stats

# View logs for a service
docker logs -f product-01-app

# Restart a service
cd deployment/product-01
docker-compose restart app

# Check Prometheus targets
curl http://localhost:9090/api/v1/targets

# Query metrics
curl 'http://localhost:9090/api/v1/query?query=up'

# Health check all services
curl http://localhost:9090/-/healthy
curl http://localhost:3000/api/health
curl http://localhost:8001/health
curl http://localhost:8002/health
curl http://localhost:8003/health
```

## Support

📖 Full Documentation: `docs/`  
🐛 Issues: Contact DevOps team  
💬 Questions: See `docs/ARCHITECTURE.md`

---

**Happy Monitoring! 🚀**
