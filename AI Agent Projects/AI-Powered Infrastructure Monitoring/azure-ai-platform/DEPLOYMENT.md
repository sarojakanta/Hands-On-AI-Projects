# Azure AI Agent - Container Deployment Guide

## Quick Start with Docker Compose

### 1. Prerequisites
- Docker and Docker Compose installed
- Azure OpenAI API credentials

### 2. Setup Environment Variables

Copy `env-template.txt` to `.env` and fill in your credentials:

```bash
cp env-template.txt .env
```

Edit `.env`:
```bash
AZURE_OPENAI_API_KEY=your-actual-api-key
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
MONITORING_API_KEY=your-secure-api-key
```

### 3. Deploy the Agent

```bash
# Build and start
docker-compose up -d --build

# Check status
docker-compose ps

# View logs
docker-compose logs -f
```

The agent will be available at: **http://localhost:8085**

### 4. Verify Deployment

```bash
# Health check
curl http://localhost:8085/health

# Expected response:
# {"status":"healthy","timestamp":"2024-01-15T10:30:00.000000"}
```

## Management Commands

### Start the Agent
```bash
docker-compose up -d
```

### Stop the Agent
```bash
docker-compose down
```

### Restart the Agent
```bash
docker-compose restart
```

### View Logs
```bash
# Real-time logs
docker-compose logs -f

# Last 100 lines
docker-compose logs --tail=100
```

### Update the Agent
```bash
# Pull latest code and rebuild
docker-compose down
docker-compose up -d --build
```

## Connect to Monitoring Stack

If you have the monitoring stack running, the agent will automatically connect to it via the `deployment_monitoring` network.

### Update Alertmanager Webhook

Edit `deployment/monitoring/alertmanager/alertmanager.yml`:

```yaml
receivers:
  - name: 'ai-agent'
    webhook_configs:
      - url: 'http://azure-ai-agent:8080/api/v1/alerts/webhook'
        send_resolved: true
        http_config:
          bearer_token: 'your-monitoring-api-key'
```

Restart Alertmanager:
```bash
cd deployment/monitoring
docker-compose restart alertmanager
```

## API Endpoints

### Health Check
```bash
GET http://localhost:8085/health
```

### Webhook (for Alertmanager)
```bash
POST http://localhost:8085/api/v1/alerts/webhook
Authorization: Bearer your-monitoring-api-key
```

### Get Incident
```bash
GET http://localhost:8085/api/v1/incidents/{incident_id}
Authorization: Bearer your-monitoring-api-key
```

### List Incidents
```bash
GET http://localhost:8085/api/v1/incidents?limit=50
Authorization: Bearer your-monitoring-api-key
```

## Data Persistence

All data is stored in a Docker volume `agent-data`:

```bash
# Inspect volume
docker volume inspect azure-ai-platform_agent-data

# Backup data
docker run --rm -v azure-ai-platform_agent-data:/data -v $(pwd):/backup alpine tar czf /backup/agent-data-backup.tar.gz /data

# Restore data
docker run --rm -v azure-ai-platform_agent-data:/data -v $(pwd):/backup alpine tar xzf /backup/agent-data-backup.tar.gz -C /
```

## Troubleshooting

### Container won't start

Check logs:
```bash
docker-compose logs azure-ai-agent
```

Common issues:
- Missing Azure OpenAI credentials
- Port 8085 already in use
- Network `deployment_monitoring` doesn't exist

### Fix network issue
```bash
# Create monitoring network if it doesn't exist
docker network create deployment_monitoring
```

### Port conflict
Edit `docker-compose.yml` and change:
```yaml
ports:
  - "8086:8080"  # Use different port
```

### Database errors

Reset the database:
```bash
docker-compose down -v  # Warning: deletes all data
docker-compose up -d
```

## Production Deployment

### Environment-specific configurations

**Development:**
```bash
docker-compose up
```

**Production:**
```bash
# Use production environment file
docker-compose --env-file .env.production up -d

# Set resource limits
docker-compose up -d --scale azure-ai-agent=1 \
  --memory="2g" \
  --cpus="2"
```

### Security Best Practices

1. **Use strong API keys**
   ```bash
   # Generate secure key
   openssl rand -base64 32
   ```

2. **Enable TLS** - Use reverse proxy (nginx/traefik)

3. **Restrict network access**
   ```yaml
   networks:
     ai-agent-network:
       internal: true
   ```

4. **Regular backups** - Schedule automated backups

## Monitoring the Agent

### View real-time stats
```bash
docker stats azure-ai-agent
```

### Check health endpoint
```bash
watch -n 5 curl http://localhost:8085/health
```

### Integration with Prometheus (if available)
```bash
curl http://localhost:8085/metrics
```

## Cleanup

### Remove everything
```bash
# Stop and remove containers
docker-compose down

# Remove volumes (deletes all data)
docker-compose down -v

# Remove images
docker rmi azure-ai-platform_azure-ai-agent
```

## Support

For issues or questions, check:
- Application logs: `docker-compose logs`
- Database stats: `GET /api/v1/database/stats`
- Health status: `GET /health`
