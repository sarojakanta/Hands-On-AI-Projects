# Complete RCA Workflow Test Guide

## 🎯 **Objective**
Test the complete AI-powered Root Cause Analysis workflow from failure injection to incident report generation.

---

## 📋 **Test Scenario: Database Connection Pool Exhaustion**

This scenario demonstrates how the platform detects, correlates, and analyzes a cascading failure.

### **What Will Happen:**
1. ✅ We inject database load (high concurrent connections)
2. ✅ Database connection pool approaches 100% utilization
3. ✅ Application workers start blocking (waiting for DB connections)
4. ✅ API latency increases dramatically
5. ✅ Prometheus fires alerts
6. ✅ Alertmanager sends webhook to AI Platform
7. ✅ All 10 agents execute in sequence
8. ✅ Cross-System RCA correlates evidence
9. ✅ Incident report is generated with recommendations

---

## 🚀 **Step-by-Step Test**

### **Step 1: Establish Baseline (2 minutes)**

#### Check Current Metrics:

**Prometheus:**
Open http://localhost:9090/graph and run:

```promql
# Database connections (should be low, ~5-10)
pg_stat_database_numbackends

# API P95 latency (should be < 1s)
histogram_quantile(0.95, rate(api_request_duration_seconds_bucket[5m]))

# Worker utilization (should be < 50%)
app_worker_busy / app_worker_total
```

**Grafana Dashboard:**
- Open http://localhost:3000
- Go to the imported "Product Machine Overview" dashboard
- Note the baseline values

**Expected Baseline:**
- CPU: 5-20%
- Memory: 30-50%
- DB Connections: 5-15 out of 100 (5-15%)
- API P95 Latency: 0.1-0.5s
- Worker Utilization: 20-50%

---

### **Step 2: Inject Database Load (5 minutes)**

Open a new terminal and run:

```bash
cd failure-injection
python inject_failures.py --machine product-01 --failure-type database-load --duration 600
```

**What This Does:**
- Generates 50 concurrent requests
- Each request hits `/api/v1/reports` (slow query endpoint)
- Saturates the database connection pool
- Runs for 10 minutes

**Expected Output:**
```
INFO - Starting failure injection: database-load on product-01
INFO - Injecting database load on product-01 for 600s
INFO - Using ThreadPoolExecutor with 50 workers
```

---

### **Step 3: Monitor Metrics Evolution (Real-time)**

#### Watch in Grafana Dashboard (http://localhost:3000)

**Timeline of Changes (approximately):**

**T+1 minute:**
- ✅ Request rate increases
- ✅ DB connections climb (30 → 60)
- ⚠️ API latency starts increasing (0.5s → 2s)

**T+2 minutes:**
- ⚠️ DB connections reach 80-90
- ⚠️ Worker utilization increases (60-80%)
- 🔴 API latency spikes (2s → 5s)

**T+3-5 minutes:**
- 🔴 DB connections saturate (92-98 out of 100)
- 🔴 Worker utilization peaks (85-95%)
- 🔴 API P95 latency exceeds 5 seconds
- 🚨 **ALERTS START FIRING**

---

### **Step 4: Watch Alerts Fire**

#### Prometheus Alerts:
Open http://localhost:9090/alerts

**Expected Alerts (in order):**

1. **T+3min:** `HighDatabaseConnections`
   - Severity: Warning
   - Trigger: Connection usage > 80%

2. **T+4min:** `CriticalDatabaseConnections`
   - Severity: Critical
   - Trigger: Connection usage > 95%

3. **T+4min:** `HighAPILatency`
   - Severity: Warning
   - Trigger: P95 latency > 2s

4. **T+5min:** `CriticalAPILatency`
   - Severity: Critical
   - Trigger: P95 latency > 5s

5. **T+5min:** `HighWorkerUtilization`
   - Severity: Warning
   - Trigger: Worker utilization > 90%

**Screenshot of Alert:**
```
FIRING
HighDatabaseConnections
  instance: product-01
  severity: warning
  machine: product-01
  
  Database connection usage is 92% on product-01
```

---

### **Step 5: Alertmanager Processing**

Open http://localhost:9093

**What You'll See:**
- Alert groups by `machine` and `component`
- Alerts are routed to `azure-ai-platform` receiver
- Webhook delivery status (if AI platform is deployed)

**Expected State:**
```
Group: {machine="product-01", severity="critical"}
  - CriticalDatabaseConnections (FIRING)
  - CriticalAPILatency (FIRING)

Group: {machine="product-01", severity="warning"}
  - HighDatabaseConnections (RESOLVED - suppressed by critical)
  - HighWorkerUtilization (FIRING)
```

---

### **Step 6: AI Platform RCA Execution** ⭐

**Note:** This requires Azure AI Platform to be deployed. If not deployed yet, we can simulate the workflow.

#### If AI Platform is Running:

**Watch Logs:**
```bash
docker logs -f azure-ai-platform
```

**Expected Log Flow:**
```
INFO - Received alert webhook: CriticalDatabaseConnections
INFO - Alertmanager payload parsed: 3 alerts
INFO - Processing alert: CriticalDatabaseConnections on product-01

INFO - Supervisor Agent: Selected agents: ['linux_health', 'nginx_monitoring', 'app_health', 'rest_api_monitoring', 'database_monitoring', 'middleware', 'log_analysis']

INFO - Linux Health Agent analyzing product-01
INFO - Linux Health Agent completed: healthy

INFO - Nginx Monitoring Agent analyzing product-01
INFO - Nginx Monitoring Agent completed: degraded (upstream latency)

INFO - Application Health Agent analyzing product-01
INFO - Application Health Agent completed: degraded (worker_utilization: 87%)

INFO - REST API Monitoring Agent analyzing product-01
INFO - REST API Monitoring Agent completed: critical (P95 latency: 5.2s)

INFO - Database Monitoring Agent analyzing product-01
INFO - Database Monitoring Agent completed: critical (connection_utilization: 92%)

INFO - Middleware Agent analyzing product-01
INFO - Middleware Agent: Redis enabled and healthy

INFO - Log Analysis Agent analyzing product-01
INFO - Log Analysis: Found 47 PoolError exceptions
INFO - Log Analysis Agent completed: critical (connection pool errors)

INFO - Cross-System RCA Agent analyzing product-01
INFO - RCA: Building evidence timeline
INFO - RCA: Correlating symptoms across components
INFO - RCA: Root cause determined with confidence 0.85
INFO - RCA Agent completed: Database connection pool exhaustion

INFO - Incident Reporting Agent generating report
INFO - Incident report generated: INC-20240115-a3f2b1c4
```

---

### **Step 7: Analyze RCA Output**

#### Query the Incident API:

```bash
curl http://localhost:8080/api/v1/incidents/INC-20240115-a3f2b1c4 \
  -H "Authorization: Bearer YOUR_API_KEY" | jq
```

**Expected RCA Report:**
```json
{
  "incident_id": "INC-20240115-a3f2b1c4",
  "created_at": "2024-01-15T10:35:00Z",
  "severity": "high",
  "status": "identified",
  "affected_system": "product-01",
  "summary": "CriticalDatabaseConnections on product-01: Database connection pool exhaustion",
  
  "root_cause_analysis": {
    "root_cause": "Database connection pool exhaustion",
    "confidence": 0.85,
    "confidence_basis": "Strong correlation across multiple telemetry sources with clear causality chain",
    
    "evidence": [
      {
        "source": "database_monitoring_agent",
        "type": "metric",
        "finding": "Connection pool 92% utilized (92/100)",
        "weight": "high"
      },
      {
        "source": "log_analysis_agent",
        "type": "log",
        "finding": "47 connection pool exhaustion errors",
        "weight": "high"
      },
      {
        "source": "rest_api_monitoring_agent",
        "type": "metric",
        "finding": "/api/v1/reports endpoint P95 latency 5.2s (baseline 0.95s)",
        "weight": "high"
      },
      {
        "source": "app_health_agent",
        "type": "metric",
        "finding": "Worker utilization 87.5%",
        "weight": "medium"
      }
    ],
    
    "timeline": [
      {"time": "T-10min", "event": "Normal operation"},
      {"time": "T-6min", "event": "Database connections reach 85%"},
      {"time": "T-5min", "event": "API latency degradation begins"},
      {"time": "T-2min", "event": "Connection pool errors appear in logs"},
      {"time": "T-0min", "event": "Alert triggered: CriticalDatabaseConnections"}
    ],
    
    "affected_components": [
      "PostgreSQL connection pool",
      "Application workers",
      "REST API endpoint: /api/v1/reports",
      "Nginx upstream"
    ],
    
    "recommendations": [
      {
        "priority": "immediate",
        "action": "Increase PostgreSQL max_connections from 100 to 200",
        "rationale": "Current pool size insufficient; immediate relief needed"
      },
      {
        "priority": "short-term",
        "action": "Review application connection pooling configuration",
        "rationale": "Ensure proper connection lifecycle management"
      },
      {
        "priority": "medium-term",
        "action": "Investigate /api/v1/reports endpoint for query optimization",
        "rationale": "Endpoint shows disproportionate latency increase"
      }
    ]
  }
}
```

---

### **Step 8: Verify Recovery**

**Stop the failure injection:**
- Press `Ctrl+C` in the terminal running the injection script

**Watch Metrics Return to Normal:**

**T+1 minute after stop:**
- Request rate drops
- DB connections decrease (92 → 60)
- API latency improves (5s → 2s)

**T+3 minutes:**
- DB connections normalize (60 → 15)
- Worker utilization returns to baseline
- API latency returns to normal (2s → 0.5s)

**T+5 minutes:**
- All alerts resolve
- Dashboard shows green status

---

## 📊 **Verification Checklist**

Check each item as you complete the test:

### Metrics Collection:
- [ ] Prometheus scraping all targets (http://localhost:9090/targets)
- [ ] Grafana dashboard showing real-time data
- [ ] Loki receiving logs

### Failure Detection:
- [ ] Database connections increased to 92%+
- [ ] API latency exceeded 2 seconds
- [ ] Worker utilization exceeded 85%

### Alert System:
- [ ] `HighDatabaseConnections` fired
- [ ] `CriticalDatabaseConnections` fired
- [ ] `HighAPILatency` fired
- [ ] Alertmanager grouped alerts correctly

### AI Agent Execution (if deployed):
- [ ] Supervisor agent selected correct agents
- [ ] All 10 agents executed successfully
- [ ] Evidence collected from multiple sources
- [ ] Timeline constructed correctly
- [ ] Root cause identified with confidence score
- [ ] Recommendations generated

### Recovery:
- [ ] Metrics returned to baseline
- [ ] Alerts resolved
- [ ] No residual issues

---

## 🎯 **Success Criteria**

**The test is successful if:**

1. ✅ All monitoring components collected data
2. ✅ Prometheus fired at least 2-3 alerts
3. ✅ Alertmanager routed alerts correctly
4. ✅ AI agents (if deployed) executed without errors
5. ✅ RCA identified "Database connection pool exhaustion" as root cause
6. ✅ Evidence included metrics AND logs
7. ✅ Confidence score was ≥ 0.80
8. ✅ System recovered after stopping injection

---

## 🔍 **Manual RCA Verification** (Without AI Platform)

If you haven't deployed the AI platform yet, you can manually verify the RCA logic:

### Query Evidence:

**1. Database Connections:**
```promql
pg_stat_database_numbackends / pg_settings_max_connections
```
Expected: > 0.90

**2. API Latency:**
```promql
histogram_quantile(0.95, rate(api_request_duration_seconds_bucket[5m]))
```
Expected: > 2.0 seconds

**3. Worker Utilization:**
```promql
app_worker_busy / app_worker_total
```
Expected: > 0.85

**4. Logs (in Grafana → Explore → Loki):**
```logql
{job="application"} |= "PoolError" or "ConnectionError"
```
Expected: Multiple error messages

### Correlation:
- ✅ High DB connections (cause)
- ✅ Workers blocked waiting for connections (effect)
- ✅ API latency increases (symptom)
- ✅ Log errors confirm diagnosis (evidence)

**Root Cause:** ✅ Connection pool exhaustion

---

## 📝 **Alternative Test Scenarios**

### Scenario 2: API Latency Only
```bash
python inject_failures.py --machine product-01 --failure-type api-latency --duration 180
```
**Expected:** Slow response times without DB saturation

### Scenario 3: Traffic Spike
```bash
python inject_failures.py --machine product-01 --failure-type traffic-spike --duration 120
```
**Expected:** High request volume, possible worker saturation

---

## 🎉 **Congratulations!**

If you've completed this test successfully, you now have:

✅ **Working end-to-end monitoring**
✅ **Alert detection and firing**
✅ **Evidence collection from multiple sources**
✅ **Cross-component correlation**
✅ **Root cause identification**
✅ **Actionable recommendations**

**Your AI-Powered Infrastructure Monitoring Platform is fully operational!** 🚀
