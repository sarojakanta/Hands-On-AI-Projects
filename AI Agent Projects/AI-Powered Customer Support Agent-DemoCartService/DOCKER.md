# Docker Deployment Guide

## Quick Start

### Option 1: Docker Compose (Recommended)

```bash
# 1. Create .env file with your API keys
cp .env.example .env
# Edit .env and add your LLM provider API key

# 2. Build and run
docker-compose up --build

# 3. Access the app
# Open http://localhost:5000
```

### Option 2: Docker Only

```bash
# 1. Build the image
docker build -t ai-support-agent .

# 2. Run the container
docker run -p 5000:5000 \
  -e GROQ_API_KEY=your_key_here \
  -e LLM_PROVIDER=groq \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  ai-support-agent

# 3. Access the app
# Open http://localhost:5000
```

---

## Configuration

### Environment Variables

All environment variables from `.env` can be set via:
- `.env` file (docker-compose reads it automatically)
- `-e` flag in `docker run` command
- `environment:` section in docker-compose.yml

**Required:**
- At least one LLM provider API key (GROQ_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, or OPENROUTER_API_KEY)

**Optional:**
- `LLM_PROVIDER` - Default: `auto`
- `DEV_MODE` - Default: `true`
- `SECRET_KEY` - Change in production
- See `.env.example` for all options

---

## With Redis (Optional)

To run with Redis for better session management:

```bash
# Start with Redis
docker-compose --profile with-redis up --build

# Update .env or docker-compose.yml:
REDIS_URL=redis://redis:6379
```

---

## Production Deployment

### Update docker-compose.yml:

```yaml
environment:
  - FLASK_ENV=production
  - DEV_MODE=false
  - SECRET_KEY=your-strong-secret-key-here
  - LLM_PROVIDER=anthropic  # or your preferred provider
  - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
```

### Use external database (optional):

```yaml
environment:
  - DATABASE_URL=postgresql://user:pass@db-host:5432/dbname
```

---

## Commands

```bash
# Build
docker-compose build

# Start
docker-compose up

# Start in background
docker-compose up -d

# View logs
docker-compose logs -f

# Stop
docker-compose down

# Restart
docker-compose restart

# Remove everything (including volumes)
docker-compose down -v
```

---

## Volumes

- `./data:/app/data` - Database and ChromaDB indexes
- `./logs:/app/logs` - Application logs

**Data persists** across container restarts in these folders.

---

## Health Check

The container includes a health check:
```bash
# Check container health
docker ps

# Manual health check
curl http://localhost:5000/health
```

---

## Troubleshooting

### Container won't start
```bash
# Check logs
docker-compose logs app

# Common issues:
# - Missing API key (at least one required)
# - Port 5000 already in use
# - Insufficient disk space for ChromaDB
```

### Database issues
```bash
# Reset database
docker-compose down -v
docker-compose up --build
```

### Access from host machine
If running on a server, bind to 0.0.0.0:
```yaml
environment:
  - HOST=0.0.0.0
ports:
  - "0.0.0.0:5000:5000"
```

---

## Development

To develop with live code reload:

```bash
# Mount source code as volume
docker run -p 5000:5000 \
  -v $(pwd):/app \
  -e GROQ_API_KEY=your_key \
  ai-support-agent
```

Or add to docker-compose.yml:
```yaml
volumes:
  - .:/app
  - ./data:/app/data
  - ./logs:/app/logs
```

---

## File Structure in Container

```
/app/
├── app/              # Application code
├── scripts/          # Setup scripts
├── knowledge_base/   # RAG documents
├── data/             # SQLite DB + ChromaDB (mounted volume)
├── logs/             # Application logs (mounted volume)
└── run.py            # Entry point
```

---

## Notes

- Python 3.12 is used (as specified in README)
- Database and RAG indexes are initialized during build
- Health check runs every 30 seconds
- Container restarts automatically unless stopped
