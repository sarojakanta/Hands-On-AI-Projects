FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Expose port
EXPOSE 8080

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV CONFIG_PATH=/app/config/settings.yaml

# Run application (use simple_main_db for GUI + Database)
CMD ["python", "-m", "uvicorn", "api.simple_main_db:app", "--host", "0.0.0.0", "--port", "8080"]
