# Complete RCA Workflow Test Script (PowerShell)
# This script guides you through the entire RCA testing process

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  RCA WORKFLOW COMPLETE TEST" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

# Step 1: Check prerequisites
Write-Host "Step 1: Checking Prerequisites...`n" -ForegroundColor Yellow

Write-Host "  Checking Prometheus... " -NoNewline
try {
    $null = Invoke-WebRequest -Uri "http://localhost:9090/-/healthy" -UseBasicParsing -TimeoutSec 5
    Write-Host "✓" -ForegroundColor Green
} catch {
    Write-Host "✗ Prometheus not available" -ForegroundColor Red
    exit 1
}

Write-Host "  Checking Grafana... " -NoNewline
try {
    $null = Invoke-WebRequest -Uri "http://localhost:3000/api/health" -UseBasicParsing -TimeoutSec 5
    Write-Host "✓" -ForegroundColor Green
} catch {
    Write-Host "✗ Grafana not available" -ForegroundColor Red
    exit 1
}

Write-Host "  Checking Product-01 App... " -NoNewline
try {
    $null = Invoke-WebRequest -Uri "http://localhost:8001/health" -UseBasicParsing -TimeoutSec 5
    Write-Host "✓" -ForegroundColor Green
} catch {
    Write-Host "✗ Product-01 not available" -ForegroundColor Red
    exit 1
}

Write-Host "`nAll prerequisites met!`n" -ForegroundColor Green

# Step 2: Establish baseline
Write-Host "Step 2: Establishing Baseline...`n" -ForegroundColor Yellow
Write-Host "  Current metrics:"

try {
    $dbConnResp = Invoke-WebRequest -Uri "http://localhost:9090/api/v1/query?query=pg_stat_database_numbackends" -UseBasicParsing
    $dbConn = ($dbConnResp.Content | ConvertFrom-Json).data.result[0].value[1]
    Write-Host "  - Database Connections: $dbConn" -ForegroundColor Green
    
    $maxConnResp = Invoke-WebRequest -Uri "http://localhost:9090/api/v1/query?query=pg_settings_max_connections" -UseBasicParsing
    $maxConn = ($maxConnResp.Content | ConvertFrom-Json).data.result[0].value[1]
    Write-Host "  - Max Connections: $maxConn" -ForegroundColor Green
    
    $connPct = [math]::Round(($dbConn / $maxConn) * 100, 1)
    Write-Host "  - Connection Utilization: $connPct%" -ForegroundColor Green
} catch {
    Write-Host "  Could not retrieve metrics" -ForegroundColor Yellow
}

Write-Host "`nBaseline established. Ready to inject failure.`n" -ForegroundColor Cyan

# Step 3: User confirmation
Write-Host "Press ENTER to start database load injection (10 minutes)..." -ForegroundColor Yellow
$null = Read-Host

Write-Host "`nStep 3: Injecting Database Load...`n" -ForegroundColor Yellow
Write-Host "This will run for 10 minutes. Watch these URLs:" -ForegroundColor Cyan
Write-Host "  - Grafana Dashboard: " -NoNewline
Write-Host "http://localhost:3000" -ForegroundColor Green
Write-Host "  - Prometheus Alerts: " -NoNewline
Write-Host "http://localhost:9090/alerts" -ForegroundColor Green
Write-Host "  - Alertmanager: " -NoNewline
Write-Host "http://localhost:9093`n" -ForegroundColor Green

Write-Host "Expected Timeline:" -ForegroundColor Yellow
Write-Host "  T+1min: DB connections increase (30→60)"
Write-Host "  T+2min: API latency starts increasing"
Write-Host "  T+3min: DB connections reach 80-90"
Write-Host "  T+4min: Alerts start firing"
Write-Host "  T+5min: Critical alerts active`n"

# Run injection
Write-Host "Starting failure injection..." -ForegroundColor Green
$injectionJob = Start-Job -ScriptBlock {
    param($path)
    Set-Location $path
    python inject_failures.py --machine product-01 --failure-type database-load --duration 600
} -ArgumentList (Get-Location).Path

Write-Host "Injection started! (Job ID: $($injectionJob.Id))`n" -ForegroundColor Green
Write-Host "Monitoring for alerts..." -ForegroundColor Cyan
Write-Host "  (Press Ctrl+C to stop early)`n"

# Monitor alerts
for ($i = 1; $i -le 20; $i++) {
    Start-Sleep -Seconds 30
    
    try {
        $alertsResp = Invoke-WebRequest -Uri "http://localhost:9090/api/v1/alerts" -UseBasicParsing
        $alertsData = ($alertsResp.Content | ConvertFrom-Json).data.alerts
        $firingAlerts = $alertsData | Where-Object { $_.state -eq "firing" }
        
        if ($firingAlerts.Count -gt 0) {
            $minutes = [math]::Floor($i / 2)
            Write-Host "[T+${minutes}min] $($firingAlerts.Count) alerts firing!" -ForegroundColor Red
            foreach ($alert in $firingAlerts) {
                Write-Host "  - $($alert.labels.alertname) ($($alert.labels.severity))" -ForegroundColor Yellow
            }
        } else {
            $minutes = [math]::Floor($i / 2)
            Write-Host "[T+${minutes}min] No alerts yet..." -ForegroundColor Yellow
        }
    } catch {
        Write-Host "  Could not retrieve alerts" -ForegroundColor DarkGray
    }
}

# Cleanup
Stop-Job -Job $injectionJob
Remove-Job -Job $injectionJob

Write-Host "`nTest complete!`n" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  NEXT STEPS" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan
Write-Host "1. Review Grafana Dashboard: http://localhost:3000"
Write-Host "2. Check fired alerts: http://localhost:9090/alerts"
Write-Host "3. View Alertmanager: http://localhost:9093"
Write-Host "4. If AI Platform deployed, check incident reports`n"
Write-Host "RCA Workflow Test Complete! 🎉`n" -ForegroundColor Green
