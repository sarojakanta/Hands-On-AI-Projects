# FastAPI Application with Prometheus and OpenTelemetry Instrumentation
import time
import random
import logging
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
import os

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='{"timestamp": "%(asctime)s", "level": "%(levelname)s", "message": "%(message)s", "logger": "%(name)s"}'
)
logger = logging.getLogger(__name__)

# Environment variables
MACHINE_ID = os.getenv("MACHINE_ID", "product-01")
OTEL_ENDPOINT = os.getenv("OTEL_ENDPOINT", "otel-collector:4317")

# Initialize FastAPI
app = FastAPI(title=f"Product Application - {MACHINE_ID}")

# ============================================
# PROMETHEUS METRICS
# ============================================

# Process health
app_process_up = Gauge('app_process_up', 'Application process status', ['machine'])
app_process_up.labels(machine=MACHINE_ID).set(1)

# Worker metrics
app_worker_total = Gauge('app_worker_total', 'Total workers', ['machine'])
app_worker_busy = Gauge('app_worker_busy', 'Busy workers', ['machine'])
app_worker_restarts_total = Counter('app_worker_restarts_total', 'Worker restarts', ['machine'])

# Exception tracking
app_exceptions_total = Counter('app_exceptions_total', 'Application exceptions', ['machine', 'exception_type'])

# API metrics
api_requests_total = Counter('api_requests_total', 'Total API requests', ['machine', 'method', 'endpoint', 'status'])
api_request_duration_seconds = Histogram('api_request_duration_seconds', 'API request duration', ['machine', 'method', 'endpoint'])
api_requests_in_progress = Gauge('api_requests_in_progress', 'Requests in progress', ['machine', 'endpoint'])
api_http_5xx_total = Counter('api_http_5xx_total', 'Total 5xx errors', ['machine', 'endpoint', 'status'])
api_http_4xx_total = Counter('api_http_4xx_total', 'Total 4xx errors', ['machine', 'endpoint', 'status'])
api_timeout_total = Counter('api_timeout_total', 'API timeouts', ['machine', 'endpoint'])

# Initialize worker metrics
app_worker_total.labels(machine=MACHINE_ID).set(8)
app_worker_busy.labels(machine=MACHINE_ID).set(0)

# ============================================
# OPENTELEMETRY SETUP
# ============================================

resource = Resource(attributes={
    "service.name": f"product-app-{MACHINE_ID}",
    "service.instance.id": MACHINE_ID,
    "environment": "production"
})

trace.set_tracer_provider(TracerProvider(resource=resource))
tracer = trace.get_tracer(__name__)

# Configure OTLP exporter
otlp_exporter = OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True)
span_processor = BatchSpanProcessor(otlp_exporter)
trace.get_tracer_provider().add_span_processor(span_processor)

# Auto-instrument FastAPI
FastAPIInstrumentor.instrument_app(app)

# ============================================
# MIDDLEWARE
# ============================================

@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    endpoint = request.url.path
    method = request.method
    
    # Track in-progress requests
    api_requests_in_progress.labels(machine=MACHINE_ID, endpoint=endpoint).inc()
    
    # Simulate worker usage
    current_busy = random.randint(2, 7)
    app_worker_busy.labels(machine=MACHINE_ID).set(current_busy)
    
    start_time = time.time()
    
    try:
        response = await call_next(request)
        status_code = response.status_code
        
        # Record metrics
        duration = time.time() - start_time
        api_request_duration_seconds.labels(machine=MACHINE_ID, method=method, endpoint=endpoint).observe(duration)
        api_requests_total.labels(machine=MACHINE_ID, method=method, endpoint=endpoint, status=status_code).inc()
        
        # Track errors
        if 400 <= status_code < 500:
            api_http_4xx_total.labels(machine=MACHINE_ID, endpoint=endpoint, status=status_code).inc()
        elif status_code >= 500:
            api_http_5xx_total.labels(machine=MACHINE_ID, endpoint=endpoint, status=status_code).inc()
        
        return response
    
    except Exception as e:
        logger.error(f"Request failed: {str(e)}", exc_info=True)
        app_exceptions_total.labels(machine=MACHINE_ID, exception_type=type(e).__name__).inc()
        raise
    
    finally:
        api_requests_in_progress.labels(machine=MACHINE_ID, endpoint=endpoint).dec()

# ============================================
# API ENDPOINTS
# ============================================

@app.get("/")
async def root():
    logger.info("Root endpoint accessed")
    return {"message": f"Product Application - {MACHINE_ID}", "status": "running"}

@app.get("/health")
async def health():
    return {"status": "healthy", "machine": MACHINE_ID}

@app.get("/api/v1/users")
async def get_users():
    with tracer.start_as_current_span("get_users") as span:
        span.set_attribute("machine", MACHINE_ID)
        
        # Simulate database query
        time.sleep(random.uniform(0.01, 0.05))
        
        users = [
            {"id": 1, "name": "User 1"},
            {"id": 2, "name": "User 2"},
            {"id": 3, "name": "User 3"}
        ]
        
        logger.info(f"Retrieved {len(users)} users")
        return {"users": users, "count": len(users)}

@app.get("/api/v1/reports")
async def get_reports():
    with tracer.start_as_current_span("get_reports") as span:
        span.set_attribute("machine", MACHINE_ID)
        
        # Simulate slow query (for testing)
        delay = random.uniform(0.5, 1.5)
        span.set_attribute("query_duration", delay)
        time.sleep(delay)
        
        logger.info("Generated reports")
        return {"reports": [], "generated_at": time.time()}

@app.get("/api/v1/data")
async def get_data():
    with tracer.start_as_current_span("get_data") as span:
        span.set_attribute("machine", MACHINE_ID)
        
        # Occasionally raise an exception
        if random.random() < 0.05:
            logger.error("Database connection error")
            app_exceptions_total.labels(machine=MACHINE_ID, exception_type="DatabaseError").inc()
            raise HTTPException(status_code=500, detail="Database connection failed")
        
        time.sleep(random.uniform(0.02, 0.1))
        return {"data": {"value": random.randint(1, 100)}}

@app.post("/api/v1/process")
async def process_data(request: Request):
    with tracer.start_as_current_span("process_data") as span:
        span.set_attribute("machine", MACHINE_ID)
        
        data = await request.json()
        
        # Simulate processing
        time.sleep(random.uniform(0.1, 0.3))
        
        logger.info(f"Processed data: {data}")
        return {"status": "processed", "machine": MACHINE_ID}

# ============================================
# METRICS ENDPOINT
# ============================================

@app.get("/metrics")
async def metrics():
    return JSONResponse(
        content=generate_latest().decode('utf-8'),
        media_type=CONTENT_TYPE_LATEST
    )

# ============================================
# STARTUP/SHUTDOWN
# ============================================

@app.on_event("startup")
async def startup_event():
    logger.info(f"Application starting on {MACHINE_ID}")
    app_process_up.labels(machine=MACHINE_ID).set(1)

@app.on_event("shutdown")
async def shutdown_event():
    logger.info(f"Application shutting down on {MACHINE_ID}")
    app_process_up.labels(machine=MACHINE_ID).set(0)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
