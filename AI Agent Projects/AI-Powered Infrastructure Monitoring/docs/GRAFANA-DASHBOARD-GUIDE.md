# Grafana Dashboard Guide

## ✅ **All Agents Are Now Complete!**

The following agents have been fully implemented:
1. ✅ Supervisor Agent
2. ✅ Linux Health Agent
3. ✅ Nginx Monitoring Agent (NEW - just completed)
4. ✅ Application Health Agent (NEW - just completed)
5. ✅ REST API Monitoring Agent
6. ✅ Database Monitoring Agent
7. ✅ Middleware Agent (NEW - just completed)
8. ✅ Log Analysis Agent
9. ✅ Cross-System RCA Agent
10. ✅ Incident Reporting Agent (NEW - just completed)

---

## 📊 **Import the Dashboard**

### Step 1: Access Grafana
1. Open: **http://localhost:3000**
2. Login: `admin` / `admin`

### Step 2: Import Dashboard

**Option A: Quick Import (Recommended)**
1. Click **☰** (menu) → **Dashboards**
2. Click **New** → **Import**
3. Click **Upload JSON file**
4. Select: `monitoring-machine/grafana/dashboards/product-machine-overview.json`
5. Click **Import**

**Option B: Copy-Paste**
1. Open the file `monitoring-machine/grafana/dashboards/product-machine-overview.json`
2. Copy all contents
3. In Grafana: **☰** → **Dashboards** → **New** → **Import**
4. Paste JSON in the text area
5. Click **Load**
6. Click **Import**

---

## 🎯 **What You'll See**

The dashboard includes:

### **Top Row - Key Metrics (Gauges)**
- **CPU Usage** - Real-time CPU utilization %
- **Memory Usage** - RAM utilization %
- **Disk Usage** - Filesystem usage %
- **DB Connection Usage** - PostgreSQL connection pool %

### **Second Row - API Performance**
- **API Request Rate** - Requests per second by endpoint
- **API Latency** - P95 and P99 latency over time

### **Third Row - Database & Errors**
- **Database Connections** - Active vs Max connections
- **HTTP Status Codes** - 2xx, 4xx, 5xx error rates

---

## 🔍 **Explore More**

### **View Individual Metrics**

Click **Explore** (🔍 icon) and try these queries:

**Worker Utilization:**
```promql
app_worker_busy / app_worker_total
```

**Exception Rate:**
```promql
rate(app_exceptions_total[5m])
```

**Nginx Connections:**
```promql
nginx_connections_active
```

**Redis Memory:**
```promql
redis_memory_used_bytes
```

**Request Success Rate:**
```promql
sum(rate(api_requests_total{status=~"2.."}[5m])) 
/ 
sum(rate(api_requests_total[5m])) * 100
```

---

## 📝 **View Logs**

1. Click **Explore** (🔍)
2. Select **Loki** datasource
3. Try these queries:

**All Application Logs:**
```logql
{job="application"}
```

**Error Logs Only:**
```logql
{job="application", level="error"}
```

**Database Errors:**
```logql
{job="application"} |= "PoolError" or "ConnectionError"
```

---

## 🎨 **Customize Dashboard**

### Add a New Panel
1. Click **Add panel** (top right)
2. Select **Add a new panel**
3. Choose metric from Prometheus
4. Select visualization type:
   - **Time series** - For trends over time
   - **Gauge** - For current values with thresholds
   - **Stat** - For single numbers
   - **Bar chart** - For comparisons
5. Click **Apply**

### Edit Existing Panel
1. Hover over any panel
2. Click the **title**
3. Select **Edit**
4. Modify query or visualization
5. Click **Apply**

---

## 🚀 **Next Steps**

### 1. Generate Some Load
```bash
# Generate API traffic
for i in {1..100}; do 
  curl http://localhost:8001/api/v1/users
done
```

Watch the dashboard update in real-time!

### 2. Run Failure Injection
```bash
cd failure-injection
python inject_failures.py --machine product-01 --failure-type database-load --duration 300
```

Watch metrics change:
- Database connections increase
- API latency increases  
- Errors appear

### 3. Check Alerts
- Go to: **http://localhost:9090/alerts** (Prometheus)
- See which alerts would fire

---

## 💡 **Tips**

1. **Auto-refresh:** Dashboard refreshes every 10 seconds automatically
2. **Time range:** Use the time picker (top right) to view different periods
3. **Variables:** You can add template variables for different machines
4. **Multiple dashboards:** Create separate dashboards for different views

---

## ✨ **What's Working Now**

✅ **Complete monitoring stack**
✅ **Product-01 fully instrumented**
✅ **All 10 AI agents implemented**
✅ **Ready-to-import dashboard**
✅ **Metrics flowing to Prometheus**
✅ **Logs flowing to Loki**
✅ **Traces flowing to Tempo**

**Everything is ready! Import the dashboard and start monitoring!** 🎉
