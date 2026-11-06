@echo off
REM Azure AI Agent - Startup Script for Windows

echo =========================================
echo    Azure AI Agent - Container Deployment
echo =========================================
echo.

REM Check if Docker is running
docker info >nul 2>&1
if errorlevel 1 (
    echo X Error: Docker is not running
    echo Please start Docker Desktop and try again
    pause
    exit /b 1
)

echo [OK] Docker is running

REM Check if .env file exists
if not exist .env (
    echo.
    echo [!] .env file not found
    echo Creating .env from template...
    copy env-template.txt .env
    echo.
    echo [!] IMPORTANT: Edit .env file with your Azure OpenAI credentials
    echo    AZURE_OPENAI_API_KEY=your-key
    echo    AZURE_OPENAI_ENDPOINT=your-endpoint
    echo.
    pause
)

echo [OK] Environment file found

REM Check if monitoring network exists
docker network inspect deployment_monitoring >nul 2>&1
if errorlevel 1 (
    echo.
    echo Creating monitoring network...
    docker network create deployment_monitoring
    echo [OK] Network created
) else (
    echo [OK] Monitoring network exists
)

REM Build and start the agent
echo.
echo Building and starting Azure AI Agent...
echo.
docker-compose up -d --build

echo.
echo Waiting for agent to be ready...
timeout /t 10 /nobreak >nul

REM Health check
echo.
echo Checking agent health...
curl -f http://localhost:8085/health >nul 2>&1
if errorlevel 1 (
    echo.
    echo [!] Agent started but health check failed
    echo Check logs: docker-compose logs
    echo.
    echo Common issues:
    echo   - Missing Azure OpenAI credentials in .env
    echo   - Invalid API key or endpoint
    echo.
) else (
    echo.
    echo =========================================
    echo    [OK] Azure AI Agent is running!
    echo =========================================
    echo.
    echo Agent URL: http://localhost:8085
    echo Health: http://localhost:8085/health
    echo.
    echo Useful commands:
    echo   View logs:    docker-compose logs -f
    echo   Stop agent:   docker-compose down
    echo   Restart:      docker-compose restart
    echo.
)

pause
