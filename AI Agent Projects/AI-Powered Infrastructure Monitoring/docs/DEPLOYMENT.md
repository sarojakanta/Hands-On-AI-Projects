# Deployment Guide

Complete step-by-step deployment guide for the AI-Powered Infrastructure Monitoring Platform.

## Prerequisites

- Docker and Docker Compose installed
- 16GB+ RAM recommended
- 50GB+ disk space
- Network connectivity between containers
- Azure subscription (for AI platform)
- Azure OpenAI access

## Architecture Reminder

```
Product Machines (01, 02, 03) → Monitoring Machine (04) → Azure AI Platform
```

## Step 1: Deploy Monitoring Stack

The monitoring stack must be deployed first as product machines depend on it.

```bash
cd deployment/monitoring
docker-compose up -d
```

**Verify:**
```bash
# Check all services are running
docker-compose ps

# Access Grafana
# http://localhost:3000 (admin/admin)

# Access Prometheus
# http://localhost:9090

# Access Alertmanager
# http://localhost:9093
```

**Create monitoring network:**
```bash
docker network create deployment_monitoring
```

## Step 2: Deploy Product Machines

### Product Machine 01

```bash
cd deployment/product-01
docker-compose up -d
```

**Verify:**
```bash
# Check containers
docker-compose ps

# Test application
curl http://localhost:8001/health

# Test Nginx
curl http://localhost:8080/

# Check metrics
curl http://localhost:9100/metrics  # Node Exporter
curl http://localhost:9187/metrics  # Postgres Exporter
curl http://localhost:8001/metrics  # Application metrics
```

### Product Machine 02

```bash
cd deployment/product-02
docker-compose up -d
```

**Verify:**
```bash
curl http://localhost:8002/health
curl http://localhost:8081/
```

### Product Machine 03

```bash
cd deployment/product-03
docker-compose up -d
```

**Verify:**
```bash
curl http://localhost:8003/health
curl http://localhost:8082/
```

## Step 3: Verify Prometheus Scraping

```bash
# Access Prometheus UI
# http://localhost:9090

# Check targets: Status → Targets
# All product machine targets should be UP
```

**Expected targets:**
- product-01-node, product-01-postgres, product-01-redis, product-01-application, product-01-nginx
- product-02-node, product-02-postgres, product-02-redis, product-02-application, product-02-nginx
- product-03-node, product-03-postgres, product-03-redis, product-03-application, product-03-nginx

## Step 4: Configure Grafana

```bash
# Access Grafana: http://localhost:3000
# Login: admin / admin
```

**Verify datasources:**
1. Go to Configuration → Data Sources
2. Verify Prometheus, Loki, Tempo are connected

**Import dashboards:**
1. Dashboards → Import
2. Upload dashboard JSON files from `monitoring-machine/grafana/dashboards/`

## Step 5: Deploy Azure AI Platform

### Configure Azure Resources

1. **Create Azure OpenAI resource:**
   - Deploy GPT-4 model
   - Note endpoint and API key

2. **Create Azure Cosmos DB (optional):**
   - Create database: `aiops`
   - Create container: `incidents`

### Configure Application

```bash
cd azure-ai-platform

# Copy environment template
cp env-template.txt .env

# Edit .env with your Azure credentials
# AZURE_OPENAI_API_KEY=...
# AZURE_OPENAI_ENDPOINT=...
```

### Update settings.yaml

```bash
cd config
# Edit settings.yaml
# Update prometheus_url, loki_url, tempo_url if needed
# Update api_key with secure key
```

### Deploy Locally (for testing)

```bash
# Build Docker image
docker build -t azure-ai-platform:latest .

# Run container
docker run -d \
  --name azure-ai-platform \
  --network deployment_monitoring \
  -p 8080:8080 \
  --env-file .env \
  -v $(pwd)/config:/app/config \
  azure-ai-platform:latest
```

**Verify:**
```bash
curl http://localhost:8080/health
```

### Deploy to Azure (Production)

**Option 1: Azure Container Instances**

```bash
# Create resource group
az group create --name aiops-rg --location eastus

# Create container instance
az container create \
  --resource-group aiops-rg \
  --name aiops-platform \
  --image azure-ai-platform:latest \
  --cpu 2 \
  --memory 4 \
  --ports 8080 \
  --environment-variables \
    AZURE_OPENAI_API_KEY=$AZURE_OPENAI_API_KEY \
    AZURE_OPENAI_ENDPOINT=$AZURE_OPENAI_ENDPOINT
```

**Option 2: Azure App Service**

```bash
# Create App Service plan
az appservice plan create \
  --name aiops-plan \
  --resource-group aiops-rg \
  --is-linux \
  --sku B2

# Create web app
az webapp create \
  --resource-group aiops-rg \
  --plan aiops-plan \
  --name aiops-platform \
  --deployment-container-image-name azure-ai-platform:latest
```

## Step 6: Configure Alertmanager Webhook

Update Alertmanager configuration:

```bash
cd monitoring-machine/alertmanager
# Edit alertmanager.yml
# Update webhook URL to point to Azure AI Platform
```

**If Azure AI Platform is at https://aiops-platform.azurewebsites.net:**

```yaml
receivers:
  - name: 'azure-ai-platform'
    webhook_configs:
      - url: 'https://aiops-platform.azurewebsites.net/api/v1/alerts/webhook'
        http_config:
          authorization:
            type: Bearer
            credentials: 'YOUR_API_KEY'
```

**Restart Alertmanager:**
```bash
cd deployment/monitoring
docker-compose restart alertmanager
```

## Step 7: Test End-to-End

### Trigger a Test Alert

```bash
# Generate API latency on product-02
cd failure-injection
python inject_failures.py --machine product-02 --failure-type database-load --duration 300
```

### Monitor Workflow

1. **Prometheus** (http://localhost:9090)
   - Check alerts: Alerts tab
   - Should see `HighDatabaseConnections` firing

2. **Alertmanager** (http://localhost:9093)
   - Verify alert is routed
   - Check webhook delivery

3. **Azure AI Platform logs**
   ```bash
   docker logs -f azure-ai-platform
   ```
   - Should see alert received
   - Agent execution logs
   - RCA completion

4. **Grafana** (http://localhost:3000)
   - View Product 02 dashboard
   - Observe metrics during failure

## Troubleshooting

### Product machines can't connect to monitoring

```bash
# Check network
docker network inspect deployment_monitoring

# Ensure monitoring stack is running
cd deployment/monitoring
docker-compose ps
```

### Prometheus targets down

```bash
# Check product machine is running
docker ps | grep product-01

# Check network connectivity
docker exec prometheus ping product-01-node-exporter
```

### Alertmanager webhook not delivering

```bash
# Check Alertmanager logs
docker logs alertmanager

# Test webhook manually
curl -X POST http://localhost:8080/api/v1/alerts/webhook \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{...}'
```

### Azure AI Platform errors

```bash
# Check logs
docker logs azure-ai-platform

# Verify environment variables
docker exec azure-ai-platform env | grep AZURE

# Test Prometheus connectivity
docker exec azure-ai-platform curl http://prometheus:9090/-/healthy
```

## Cleanup

```bash
# Stop all product machines
cd deployment/product-01 && docker-compose down
cd deployment/product-02 && docker-compose down
cd deployment/product-03 && docker-compose down

# Stop monitoring stack
cd deployment/monitoring && docker-compose down

# Stop Azure AI Platform
docker stop azure-ai-platform
docker rm azure-ai-platform

# Remove volumes (optional - deletes data)
docker volume prune
```

## Next Steps

1. Import Grafana dashboards
2. Configure alert thresholds
3. Test failure injection scenarios
4. Review incident reports
5. Tune agent prompts and thresholds
