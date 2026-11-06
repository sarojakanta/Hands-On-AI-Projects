#!/bin/bash

# Azure AI Agent - Startup Script

set -e

echo "========================================="
echo "   Azure AI Agent - Container Deployment"
echo "========================================="
echo ""

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Error: Docker is not running"
    echo "Please start Docker and try again"
    exit 1
fi

echo "✅ Docker is running"

# Check if .env file exists
if [ ! -f .env ]; then
    echo ""
    echo "⚠️  .env file not found"
    echo "Creating .env from template..."
    cp env-template.txt .env
    echo ""
    echo "⚠️  IMPORTANT: Edit .env file with your Azure OpenAI credentials"
    echo "   AZURE_OPENAI_API_KEY=your-key"
    echo "   AZURE_OPENAI_ENDPOINT=your-endpoint"
    echo ""
    read -p "Press Enter after updating .env file..."
fi

echo "✅ Environment file found"

# Check if monitoring network exists, create if not
if ! docker network inspect deployment_monitoring > /dev/null 2>&1; then
    echo ""
    echo "📡 Creating monitoring network..."
    docker network create deployment_monitoring
    echo "✅ Network created"
else
    echo "✅ Monitoring network exists"
fi

# Build and start the agent
echo ""
echo "🚀 Building and starting Azure AI Agent..."
echo ""
docker-compose up -d --build

echo ""
echo "⏳ Waiting for agent to be ready..."
sleep 10

# Health check
echo ""
echo "🏥 Checking agent health..."
if curl -f http://localhost:8085/health > /dev/null 2>&1; then
    echo ""
    echo "========================================="
    echo "   ✅ Azure AI Agent is running!"
    echo "========================================="
    echo ""
    echo "📍 Agent URL: http://localhost:8085"
    echo "🏥 Health: http://localhost:8085/health"
    echo ""
    echo "Useful commands:"
    echo "  View logs:    docker-compose logs -f"
    echo "  Stop agent:   docker-compose down"
    echo "  Restart:      docker-compose restart"
    echo ""
else
    echo ""
    echo "⚠️  Agent started but health check failed"
    echo "Check logs: docker-compose logs"
    echo ""
    echo "Common issues:"
    echo "  - Missing Azure OpenAI credentials in .env"
    echo "  - Invalid API key or endpoint"
    echo ""
fi
