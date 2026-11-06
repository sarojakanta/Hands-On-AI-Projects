# Failure Injection Guide

This directory contains tools for controlled failure injection to test the AIOps platform.

## Available Failure Types

### 1. API Latency
Generates slow API requests to trigger latency alerts.

```bash
python inject_failures.py --machine product-02 --failure-type api-latency --duration 300
```

**Expected Outcome:**
- P95 API latency increases above 2s
- `HighAPILatency` alert triggered
- RCA identifies slow query pattern

### 2. API Errors
Generates 5xx errors by repeatedly calling endpoints.

```bash
python inject_failures.py --machine product-01 --failure-type api-errors --duration 180
```

**Expected Outcome:**
- 5xx error rate exceeds 5%
- `HighAPIErrorRate` alert triggered
- Log analysis detects error patterns

### 3. Database Load
Creates connection pool saturation by generating concurrent requests.

```bash
python inject_failures.py --machine product-02 --failure-type database-load --duration 300
```

**Expected Outcome:**
- Database connections approach limit (92-95%)
- `CriticalDatabaseConnections` alert triggered
- RCA identifies connection pool exhaustion
- Log shows connection timeout errors

### 4. Traffic Spike
Generates sudden traffic increase.

```bash
python inject_failures.py --machine product-03 --failure-type traffic-spike --duration 180
```

**Expected Outcome:**
- Request rate increases significantly
- Worker utilization increases
- Possible latency degradation

## Testing Scenarios

### Scenario 1: Database Connection Exhaustion (Cross-System RCA Example)

```bash
# This mimics the documented RCA workflow example
python inject_failures.py --machine product-02 --failure-type database-load --duration 600
```

**Monitoring:**
1. Watch Grafana dashboards for Product 02
2. Monitor Alertmanager for triggered alerts
3. Check Azure AI Platform logs for RCA execution
4. Review generated incident report

**Expected RCA Output:**
- Root Cause: Database connection pool exhaustion
- Confidence: 0.85
- Evidence: Connection metrics, slow queries, log errors
- Recommendations: Increase max_connections, optimize queries

### Scenario 2: API Performance Degradation

```bash
python inject_failures.py --machine product-01 --failure-type api-latency --duration 300
```

### Scenario 3: Multi-Component Failure

Run simultaneously on different machines:

```bash
# Terminal 1
python inject_failures.py --machine product-01 --failure-type api-latency --duration 300

# Terminal 2
python inject_failures.py --machine product-02 --failure-type database-load --duration 300
```

## Safety

- All injections are **controlled** and **reversible**
- No permanent changes to infrastructure
- Injections stop when script terminates
- Use `Ctrl+C` to stop early

## Requirements

```bash
pip install requests
```

## Notes

- High CPU and memory pressure injections require application-level implementation
- Always test in non-production environments first
- Monitor resource usage during injection
- Allow time for metrics to propagate (15-30 seconds)
