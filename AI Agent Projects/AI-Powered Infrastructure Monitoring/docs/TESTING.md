# Testing Guide

## Testing Strategy

### Unit Tests
Test individual agents and clients in isolation.

### Integration Tests
Test agent workflows and monitoring client integration.

### End-to-End Tests
Test complete alert → RCA → incident report flow.

### Failure Injection Tests
Controlled failure scenarios to validate detection and RCA.

## Running Tests

### 1. Unit Tests (Agents)

```bash
cd azure-ai-platform
pytest tests/test_agents.py -v
```

### 2. Integration Tests

```bash
pytest tests/test_integration.py -v
```

### 3. End-to-End Test

**Prerequisites:**
- All product machines running
- Monitoring stack running
- Azure AI Platform running

**Test Scenario: Database Connection Exhaustion**

```bash
# Step 1: Baseline check
curl http://localhost:9090/api/v1/query?query=pg_stat_database_numbackends{instance="product-02"}

# Step 2: Inject failure
cd failure-injection
python inject_failures.py --machine product-02 --failure-type database-load --duration 600

# Step 3: Monitor alert firing (wait 5 minutes)
# http://localhost:9090/alerts

# Step 4: Check Alertmanager
# http://localhost:9093

# Step 5: Monitor Azure AI Platform logs
docker logs -f azure-ai-platform

# Expected log flow:
# 1. "Received alert webhook"
# 2. "Supervisor processing alert"
# 3. "Linux Health Agent analyzing product-02"
# 4. "Database Agent analyzing product-02"
# 5. "API Monitoring Agent analyzing product-02"
# 6. "Log Analysis Agent analyzing product-02"
# 7. "RCA Agent analyzing product-02"
# 8. "RCA completed: Database connection pool exhaustion"

# Step 6: Verify incident report
curl http://localhost:8080/api/v1/incidents/<incident-id> \
  -H "Authorization: Bearer YOUR_API_KEY"
```

## Test Cases

### TC-001: High CPU Alert

**Objective:** Verify CPU monitoring and alerting

**Steps:**
1. Generate CPU load on product-01
2. Wait for alert to fire (5 minutes)
3. Verify alert in Prometheus
4. Verify webhook delivery to Azure
5. Verify RCA execution

**Expected Result:**
- Alert: `HighCPUUsage`
- RCA identifies CPU saturation
- Recommendations provided

### TC-002: API Latency Degradation

**Objective:** Verify API performance monitoring

**Steps:**
```bash
python inject_failures.py --machine product-02 --failure-type api-latency --duration 300
```

**Expected Result:**
- Alert: `HighAPILatency`
- P95 latency > 2s detected
- RCA correlates with worker utilization

### TC-003: Database Connection Pool Exhaustion

**Objective:** Verify cross-system RCA (as documented in design)

**Steps:**
```bash
python inject_failures.py --machine product-02 --failure-type database-load --duration 600
```

**Expected Result:**
- Alert: `CriticalDatabaseConnections`
- Evidence from multiple agents:
  - Database: 92%+ connection utilization
  - API: Latency increase
  - Logs: Connection pool errors
- RCA Output:
  - Root cause: "Database connection pool exhaustion"
  - Confidence: 0.85
  - Timeline showing causality
  - Recommendations including connection pool increase

**Verification:**
```bash
# Check database connections
curl 'http://localhost:9090/api/v1/query?query=pg_stat_database_numbackends{instance="product-02"}'

# Check API latency
curl 'http://localhost:9090/api/v1/query?query=histogram_quantile(0.95,rate(api_request_duration_seconds_bucket{instance="product-02"}[5m]))'

# Check logs
curl 'http://localhost:3100/loki/api/v1/query_range?query={instance="product-02",job="application"}|="PoolError"&start=...'
```

### TC-004: Multiple Concurrent Alerts

**Objective:** Verify handling of multiple alerts

**Steps:**
1. Inject failures on product-01 and product-02 simultaneously
2. Verify both alerts are processed
3. Verify independent RCA for each

**Expected Result:**
- Two incident reports generated
- Correct machine attribution
- No cross-contamination of evidence

### TC-005: Alert Resolution

**Objective:** Verify alert resolution handling

**Steps:**
1. Inject temporary failure (2 minutes)
2. Wait for alert to fire
3. Stop injection
4. Wait for alert to resolve
5. Verify resolved webhook received

**Expected Result:**
- Alert fires
- RCA executes
- Alert resolves
- Resolution webhook received

## Acceptance Criteria

### Monitoring Stack

- [ ] All Prometheus targets UP
- [ ] All alert rules loaded
- [ ] Grafana dashboards render
- [ ] Loki receiving logs from all machines
- [ ] Tempo receiving traces

### Product Machines

- [ ] All exporters returning metrics
- [ ] Application `/metrics` endpoint accessible
- [ ] Logs flowing to Loki
- [ ] Traces flowing to Tempo
- [ ] Health endpoints responding

### Azure AI Platform

- [ ] Webhook endpoint accepting alerts
- [ ] All agents executing without errors
- [ ] Prometheus queries returning data
- [ ] Loki queries returning logs
- [ ] LLM integration working
- [ ] Incident reports generated

### End-to-End

- [ ] Alert fires within 5 minutes of failure
- [ ] Webhook delivered to Azure within 30 seconds
- [ ] RCA completes within 2 minutes
- [ ] Incident report contains:
  - [ ] Root cause
  - [ ] Confidence score
  - [ ] Evidence from multiple sources
  - [ ] Timeline
  - [ ] Recommendations
- [ ] Logs show complete execution flow

## Performance Benchmarks

| Metric | Target | Measurement |
|--------|--------|-------------|
| Alert detection time | < 5 minutes | Time from metric threshold breach to alert firing |
| Webhook delivery | < 30 seconds | Time from alert to webhook received |
| RCA execution time | < 2 minutes | Time from webhook to RCA completion |
| Agent query time | < 10 seconds | Individual agent Prometheus/Loki query |
| Total incident time | < 8 minutes | Total time from failure to report |

## Troubleshooting Tests

### Test: Prometheus Not Scraping

```bash
# Check Prometheus config
docker exec prometheus cat /etc/prometheus/prometheus.yml

# Check target reachability
docker exec prometheus wget -O- http://product-01:9100/metrics

# Check Prometheus logs
docker logs prometheus
```

### Test: Alerts Not Firing

```bash
# Check alert rules loaded
curl http://localhost:9090/api/v1/rules

# Manually trigger alert condition
curl http://localhost:9090/api/v1/query?query=up{job="product-01-node"}

# Check Alertmanager
curl http://localhost:9093/api/v2/alerts
```

### Test: Webhook Not Delivering

```bash
# Check Alertmanager config
docker exec alertmanager cat /etc/alertmanager/alertmanager.yml

# Test webhook manually
curl -X POST http://localhost:8080/api/v1/alerts/webhook \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d @test-alert.json
```

### Test: Agent Failures

```bash
# Check Azure AI Platform logs
docker logs azure-ai-platform | grep ERROR

# Test Prometheus connectivity
docker exec azure-ai-platform curl http://prometheus:9090/-/healthy

# Test individual agent
curl http://localhost:8080/api/v1/metrics/product-01 \
  -H "Authorization: Bearer YOUR_API_KEY"
```

## Continuous Testing

### Automated Test Schedule

- **Hourly:** Health checks for all services
- **Daily:** Baseline metric validation
- **Weekly:** Full failure injection test suite
- **Monthly:** Performance benchmark validation

### Health Check Script

```bash
#!/bin/bash
# health-check.sh

echo "Checking monitoring stack..."
curl -sf http://localhost:9090/-/healthy || echo "Prometheus DOWN"
curl -sf http://localhost:3100/ready || echo "Loki DOWN"
curl -sf http://localhost:3000/api/health || echo "Grafana DOWN"

echo "Checking product machines..."
curl -sf http://localhost:8001/health || echo "Product-01 DOWN"
curl -sf http://localhost:8002/health || echo "Product-02 DOWN"
curl -sf http://localhost:8003/health || echo "Product-03 DOWN"

echo "Checking Azure AI Platform..."
curl -sf http://localhost:8080/health || echo "Azure AI Platform DOWN"
```

Run every hour via cron:
```bash
0 * * * * /path/to/health-check.sh >> /var/log/aiops-health.log
```
