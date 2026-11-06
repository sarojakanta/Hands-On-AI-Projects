#!/bin/bash

# Startup script for AI-Powered Infrastructure Monitoring Platform
# Deploys all components in correct order

set -e

echo "=========================================="
echo "AI-Powered Infrastructure Monitoring"
echo "Deployment Script"
echo "=========================================="
echo ""

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to wait for service
wait_for_service() {
    local url=$1
    local name=$2
    local max_attempts=30
    local attempt=1
    
    echo -n "Waiting for $name to be ready..."
    while [ $attempt -le $max_attempts ]; do
        if curl -sf "$url" > /dev/null 2>&1; then
            echo -e " ${GREEN}✓${NC}"
            return 0
        fi
        echo -n "."
        sleep 2
        attempt=$((attempt + 1))
    done
    echo -e " ${YELLOW}Failed${NC}"
    return 1
}

# Step 1: Deploy Monitoring Stack
echo ""
echo "Step 1: Deploying Monitoring Stack..."
echo "--------------------------------------"
cd "$(dirname "$0")/../monitoring"
docker-compose up -d

echo "Waiting for services to start..."
sleep 10

wait_for_service "http://localhost:9090/-/healthy" "Prometheus"
wait_for_service "http://localhost:3100/ready" "Loki"
wait_for_service "http://localhost:3000/api/health" "Grafana"

echo -e "${GREEN}✓ Monitoring stack deployed${NC}"

# Step 2: Create monitoring network (if not exists)
echo ""
echo "Step 2: Creating monitoring network..."
echo "--------------------------------------"
docker network create deployment_monitoring 2>/dev/null || echo "Network already exists"
echo -e "${GREEN}✓ Network ready${NC}"

# Step 3: Deploy Product Machines
echo ""
echo "Step 3: Deploying Product Machines..."
echo "--------------------------------------"

echo "Deploying Product Machine 01..."
cd "$(dirname "$0")/../product-01"
docker-compose up -d
sleep 5
wait_for_service "http://localhost:8001/health" "Product-01"

echo "Deploying Product Machine 02..."
cd "$(dirname "$0")/../product-02"
docker-compose up -d
sleep 5
wait_for_service "http://localhost:8002/health" "Product-02"

echo "Deploying Product Machine 03..."
cd "$(dirname "$0")/../product-03"
docker-compose up -d
sleep 5
wait_for_service "http://localhost:8003/health" "Product-03"

echo -e "${GREEN}✓ All product machines deployed${NC}"

# Step 4: Verify Prometheus targets
echo ""
echo "Step 4: Verifying Prometheus targets..."
echo "--------------------------------------"
sleep 10  # Give Prometheus time to scrape
echo "Check targets at: http://localhost:9090/targets"
echo -e "${YELLOW}Note: It may take 1-2 minutes for all targets to be UP${NC}"

# Step 5: Summary
echo ""
echo "=========================================="
echo "Deployment Complete!"
echo "=========================================="
echo ""
echo "Access Points:"
echo "  Grafana:       http://localhost:3000 (admin/admin)"
echo "  Prometheus:    http://localhost:9090"
echo "  Alertmanager:  http://localhost:9093"
echo "  Product-01:    http://localhost:8001"
echo "  Product-02:    http://localhost:8002"
echo "  Product-03:    http://localhost:8003"
echo ""
echo "Next Steps:"
echo "  1. Access Grafana and verify datasources"
echo "  2. Check Prometheus targets: http://localhost:9090/targets"
echo "  3. Deploy Azure AI Platform (see docs/DEPLOYMENT.md)"
echo "  4. Run failure injection tests (see failure-injection/README.md)"
echo ""
echo -e "${GREEN}Happy Monitoring! 🚀${NC}"
echo ""
