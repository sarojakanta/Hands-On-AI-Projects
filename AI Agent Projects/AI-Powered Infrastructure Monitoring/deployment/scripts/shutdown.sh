#!/bin/bash

# Shutdown script for AI-Powered Infrastructure Monitoring Platform

set -e

echo "=========================================="
echo "AI-Powered Infrastructure Monitoring"
echo "Shutdown Script"
echo "=========================================="
echo ""

# Step 1: Stop Product Machines
echo "Step 1: Stopping Product Machines..."
echo "--------------------------------------"

cd "$(dirname "$0")/../product-01"
docker-compose down
echo "✓ Product-01 stopped"

cd "$(dirname "$0")/../product-02"
docker-compose down
echo "✓ Product-02 stopped"

cd "$(dirname "$0")/../product-03"
docker-compose down
echo "✓ Product-03 stopped"

# Step 2: Stop Monitoring Stack
echo ""
echo "Step 2: Stopping Monitoring Stack..."
echo "--------------------------------------"
cd "$(dirname "$0")/../monitoring"
docker-compose down
echo "✓ Monitoring stack stopped"

# Step 3: Stop Azure AI Platform (if running locally)
echo ""
echo "Step 3: Stopping Azure AI Platform..."
echo "--------------------------------------"
docker stop azure-ai-platform 2>/dev/null && docker rm azure-ai-platform 2>/dev/null || echo "Azure AI Platform not running locally"

echo ""
echo "=========================================="
echo "All services stopped"
echo "=========================================="
echo ""
echo "Data is preserved in Docker volumes."
echo "To completely remove all data, run:"
echo "  docker volume prune"
echo ""
