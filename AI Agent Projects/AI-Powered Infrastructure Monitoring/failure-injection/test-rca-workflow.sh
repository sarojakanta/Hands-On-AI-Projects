#!/bin/bash

# Complete RCA Workflow Test Script
# This script guides you through the entire RCA testing process

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}"
echo "=========================================="
echo "  RCA WORKFLOW COMPLETE TEST"
echo "=========================================="
echo -e "${NC}"

# Step 1: Check prerequisites
echo -e "\n${YELLOW}Step 1: Checking Prerequisites...${NC}"
echo -n "  Checking Prometheus... "
if curl -sf http://localhost:9090/-/healthy > /dev/null 2>&1; then
    echo -e "${GREEN}✓${NC}"
else
    echo -e "${RED}✗ Prometheus not available${NC}"
    exit 1
fi

echo -n "  Checking Grafana... "
if curl -sf http://localhost:3000/api/health > /dev/null 2>&1; then
    echo -e "${GREEN}✓${NC}"
else
    echo -e "${RED}✗ Grafana not available${NC}"
    exit 1
fi

echo -n "  Checking Product-01 App... "
if curl -sf http://localhost:8001/health > /dev/null 2>&1; then
    echo -e "${GREEN}✓${NC}"
else
    echo -e "${RED}✗ Product-01 not available${NC}"
    exit 1
fi

echo -e "${GREEN}All prerequisites met!${NC}"

# Step 2: Establish baseline
echo -e "\n${YELLOW}Step 2: Establishing Baseline...${NC}"
echo "  Current metrics:"

DB_CONN=$(curl -s "http://localhost:9090/api/v1/query?query=pg_stat_database_numbackends" | jq -r '.data.result[0].value[1] // "0"')
echo -e "  - Database Connections: ${GREEN}${DB_CONN}${NC}"

MAX_CONN=$(curl -s "http://localhost:9090/api/v1/query?query=pg_settings_max_connections" | jq -r '.data.result[0].value[1] // "100"')
echo -e "  - Max Connections: ${GREEN}${MAX_CONN}${NC}"

CONN_PCT=$(echo "scale=1; ($DB_CONN / $MAX_CONN) * 100" | bc)
echo -e "  - Connection Utilization: ${GREEN}${CONN_PCT}%${NC}"

echo ""
echo -e "${CYAN}Baseline established. Ready to inject failure.${NC}"
echo ""

# Step 3: User confirmation
read -p "Press ENTER to start database load injection (10 minutes)..."

echo -e "\n${YELLOW}Step 3: Injecting Database Load...${NC}"
echo -e "${CYAN}This will run for 10 minutes. Watch these URLs:${NC}"
echo -e "  - Grafana Dashboard: ${GREEN}http://localhost:3000${NC}"
echo -e "  - Prometheus Alerts: ${GREEN}http://localhost:9090/alerts${NC}"
echo -e "  - Alertmanager: ${GREEN}http://localhost:9093${NC}"
echo ""
echo -e "${YELLOW}Expected Timeline:${NC}"
echo "  T+1min: DB connections increase (30→60)"
echo "  T+2min: API latency starts increasing"
echo "  T+3min: DB connections reach 80-90"
echo "  T+4min: Alerts start firing"
echo "  T+5min: Critical alerts active"
echo ""

# Run injection
python inject_failures.py --machine product-01 --failure-type database-load --duration 600 &
INJECTION_PID=$!

echo -e "${GREEN}Injection started! (PID: $INJECTION_PID)${NC}"
echo ""
echo -e "${CYAN}Monitoring for alerts...${NC}"
echo "  (Press Ctrl+C to stop early)"
echo ""

# Monitor alerts
for i in {1..20}; do
    sleep 30
    
    # Check if alerts are firing
    ALERTS=$(curl -s http://localhost:9090/api/v1/alerts | jq -r '.data.alerts[] | select(.state=="firing") | .labels.alertname' 2>/dev/null | wc -l)
    
    if [ "$ALERTS" -gt 0 ]; then
        echo -e "${RED}[T+$(($i/2))min] ${ALERTS} alerts firing!${NC}"
        curl -s http://localhost:9090/api/v1/alerts | jq -r '.data.alerts[] | select(.state=="firing") | "  - \(.labels.alertname) (\(.labels.severity))"' 2>/dev/null
    else
        echo -e "${YELLOW}[T+$(($i/2))min] No alerts yet...${NC}"
    fi
done

# Cleanup
kill $INJECTION_PID 2>/dev/null || true

echo ""
echo -e "${GREEN}Test complete!${NC}"
echo ""
echo -e "${CYAN}=========================================="
echo "  NEXT STEPS"
echo "==========================================${NC}"
echo ""
echo "1. Review Grafana Dashboard: http://localhost:3000"
echo "2. Check fired alerts: http://localhost:9090/alerts"
echo "3. View Alertmanager: http://localhost:9093"
echo "4. If AI Platform deployed, check incident reports"
echo ""
echo -e "${GREEN}RCA Workflow Test Complete! 🎉${NC}"
