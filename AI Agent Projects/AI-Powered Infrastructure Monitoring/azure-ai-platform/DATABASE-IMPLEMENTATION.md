# SQLite Database Implementation

## Overview
This document describes the SQLite file-based database implementation for incident storage in the Azure AI Platform.

## Why SQLite?
- **No container needed**: File-based database, no separate database container required
- **Lightweight**: Perfect for incident storage with controlled growth
- **Zero configuration**: Works out of the box with Python
- **File size management**: Automatic archiving when size limit is reached

## Configuration

### Environment Variables (.env)
```bash
# SQLite Database Configuration (file-based, no container needed)
DB_PATH=data/incidents.db
DB_MAX_SIZE_MB=100
DB_ARCHIVE_PATH=data/archive
```

### Settings (config/settings.yaml)
```yaml
database:
  path: "${DB_PATH:-data/incidents.db}"
  max_size_mb: ${DB_MAX_SIZE_MB:-100}
  archive_path: "${DB_ARCHIVE_PATH:-data/archive}"
```

## Database Schema

### Incidents Table
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key (auto-increment) |
| incident_id | STRING(100) | Unique incident ID (indexed) |
| created_at | DATETIME | Incident creation timestamp (indexed) |
| severity | STRING(20) | Severity level (low/medium/high/critical) (indexed) |
| status | STRING(50) | Current status (identified/investigating/resolved/closed) (indexed) |
| affected_system | STRING(100) | Machine/system affected |
| summary | TEXT | Brief incident summary |
| root_cause_analysis | JSON | Complete RCA results |
| evidence_summary | JSON | Evidence from all agents |
| impact | JSON | Impact assessment |
| recommendations | JSON | Remediation recommendations |
| notification_sent | INTEGER | Boolean flag (0/1) |
| requires_human_approval | INTEGER | Boolean flag (0/1) |
| auto_remediation_enabled | INTEGER | Boolean flag (0/1) |

## Features

### 1. Automatic Size Management
- Monitors database file size before each write
- When max size is reached, automatically:
  - Archives current database to `data/archive/incidents_YYYYMMDD_HHMMSS.db`
  - Creates fresh database with same schema
  - Continues operation seamlessly

### 2. CRUD Operations

#### Create Incident
```python
incident_data = {
    "incident_id": "INC-20240115-abc123",
    "created_at": datetime.utcnow().isoformat(),
    "severity": "high",
    "status": "identified",
    "affected_system": "product-02",
    "summary": "Database connection pool exhaustion",
    "root_cause_analysis": {...},
    "evidence_summary": {...},
    "impact": {...},
    "recommendations": [...]
}
db_client.create_incident(incident_data)
```

#### Get Incident by ID
```python
incident = db_client.get_incident_by_id("INC-20240115-abc123")
if incident:
    print(incident.to_dict())
```

#### List Incidents with Filtering
```python
incidents = db_client.list_incidents(
    limit=50,
    offset=0,
    severity="high",
    status="identified",
    order_by="created_at",
    order_desc=True
)
```

#### Update Incident
```python
db_client.update_incident(
    incident_id="INC-20240115-abc123",
    updates={"status": "resolved"}
)
```

#### Get Database Statistics
```python
stats = db_client.get_stats()
# Returns:
# {
#   "total_incidents": 42,
#   "database_size_bytes": 5242880,
#   "database_size_mb": 5.0,
#   "max_size_mb": 100,
#   "size_usage_pct": 5.0,
#   "severity_breakdown": {"low": 5, "medium": 20, "high": 15, "critical": 2},
#   "status_breakdown": {"identified": 10, "investigating": 5, "resolved": 25, "closed": 2}
# }
```

## API Endpoints

### 1. Create Incident (Automatic via Webhook)
**Endpoint**: `POST /api/v1/alerts/webhook`

Automatically stores incidents when RCA completes.

### 2. Get Incident by ID
**Endpoint**: `GET /api/v1/incidents/{incident_id}`

**Headers**: 
```
Authorization: Bearer your-api-key
```

**Response**:
```json
{
  "incident_id": "INC-20240115-abc123",
  "created_at": "2024-01-15T10:30:00",
  "severity": "high",
  "status": "identified",
  "affected_system": "product-02",
  "summary": "Database connection pool exhaustion",
  "root_cause_analysis": {...},
  "evidence_summary": {...},
  "impact": {...},
  "recommendations": [...]
}
```

### 3. List Incidents
**Endpoint**: `GET /api/v1/incidents`

**Query Parameters**:
- `limit` (default: 50, max: 100)
- `offset` (default: 0)
- `severity` (optional: low/medium/high/critical)
- `status` (optional: identified/investigating/resolved/closed)

**Example**:
```bash
curl -H "Authorization: Bearer test-api-key-12345" \
  "http://localhost:8080/api/v1/incidents?limit=20&severity=high"
```

**Response**:
```json
{
  "count": 20,
  "limit": 20,
  "offset": 0,
  "incidents": [...]
}
```

### 4. Database Statistics
**Endpoint**: `GET /api/v1/database/stats`

**Response**:
```json
{
  "total_incidents": 42,
  "database_size_bytes": 5242880,
  "database_size_mb": 5.0,
  "max_size_mb": 100,
  "size_usage_pct": 5.0,
  "severity_breakdown": {
    "low": 5,
    "medium": 20,
    "high": 15,
    "critical": 2
  },
  "status_breakdown": {
    "identified": 10,
    "investigating": 5,
    "resolved": 25,
    "closed": 2
  }
}
```

## File Structure

```
azure-ai-platform/
├── database/
│   ├── __init__.py           # Package initialization
│   ├── models.py             # SQLAlchemy ORM models
│   └── sqlite_client.py      # Database client with size management
├── data/
│   ├── incidents.db          # Active database (auto-created)
│   └── archive/              # Archived databases (auto-created)
│       ├── incidents_20240115_103000.db
│       └── incidents_20240120_150000.db
└── ...
```

## Migration from Cosmos DB

All references to Azure Cosmos DB have been removed:
- ❌ Removed: `AZURE_COSMOS_ENDPOINT` and `AZURE_COSMOS_KEY` from env-template.txt
- ❌ Removed: `cosmos` section from settings.yaml
- ✅ Added: `database` section with SQLite configuration
- ✅ Added: Database client initialization in main.py
- ✅ Added: Automatic incident storage in webhook handler
- ✅ Implemented: All incident retrieval endpoints

## Dependencies

Added to `requirements.txt`:
- `sqlalchemy` - ORM and database abstraction
- `aiosqlite` - Async SQLite support (for future async operations)

## Size Management Example

With `DB_MAX_SIZE_MB=100`:
1. Database grows to 99 MB - works normally
2. New incident arrives, would exceed 100 MB
3. System automatically:
   - Copies `data/incidents.db` → `data/archive/incidents_20240115_103000.db`
   - Deletes old `data/incidents.db`
   - Creates fresh `data/incidents.db` with schema
   - Stores new incident in fresh database
4. Operations continue without interruption

## Testing

```bash
# Install dependencies
cd azure-ai-platform
pip install -r requirements.txt

# Run the application
python api/main.py

# Test incident storage (trigger an alert)
cd ../failure-injection
python inject_failures.py --machine product-02 --failure-type database-load --duration 300

# Check incident was stored
curl -H "Authorization: Bearer test-api-key-12345" \
  http://localhost:8080/api/v1/incidents

# Check database stats
curl -H "Authorization: Bearer test-api-key-12345" \
  http://localhost:8080/api/v1/database/stats
```

## Advantages over Cosmos DB

| Feature | SQLite | Cosmos DB |
|---------|--------|-----------|
| **Setup** | Zero config, file-based | Requires Azure account, setup |
| **Cost** | Free | Pay per RU/s and storage |
| **Container** | None needed | Separate service |
| **Local Dev** | Perfect for local testing | Requires cloud connection |
| **Size Control** | Built-in with auto-archive | Manual management |
| **Queries** | Standard SQL | SQL-like with limitations |
| **Backup** | Simple file copy | Azure backup tools |

## Production Considerations

For production deployments:
1. Consider increasing `DB_MAX_SIZE_MB` based on incident volume
2. Set up regular backups of `data/` directory
3. Monitor `data/archive/` directory size
4. Optionally implement archive cleanup after X days
5. For very high volume (>1000 incidents/day), consider PostgreSQL or actual Cosmos DB

## Troubleshooting

### Database locked error
- Ensure no other process is accessing the database
- SQLite uses file locking, only one writer at a time

### Archive directory full
- Manually delete old archives from `data/archive/`
- Or implement automated cleanup script

### Database not created
- Ensure `data/` directory has write permissions
- Check logs for initialization errors

## Conclusion

The SQLite implementation provides a simple, robust, file-based storage solution for incidents without requiring additional containers or cloud services. It's perfect for local development and small-to-medium deployments.
