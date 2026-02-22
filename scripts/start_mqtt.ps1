# MQTT Broker Startup Script for Digital Twin Microgrid
# This script attempts to start a local Mosquitto broker if installed.

$ServiceName = "mosquitto"
$Service = Get-Service $ServiceName -ErrorAction SilentlyContinue

if ($Service) {
    if ($Service.Status -eq "Running") {
        Write-Host "MQTT: Mosquitto service is already running." -ForegroundColor Green
        exit 0
    }
    Write-Host "MQTT: Found Mosquitto service. Attempting to start..." -ForegroundColor Cyan
    try {
        Start-Service $ServiceName -ErrorAction Stop
        Write-Host "MQTT: Mosquitto service started successfully." -ForegroundColor Green
        exit 0
    } catch {
        Write-Host "MQTT: Failed to start service directly (Check admin permissions). Falling back to executable detection..." -ForegroundColor Yellow
    }
}

# Fallback: Find executable
$Paths = @(
    "D:\SET and IEMS project\mosquitto\mosquitto.exe",
    "D:\Digital-twin-microgrid\mosquitto\mosquitto.exe",
    "C:\Program Files\mosquitto\mosquitto.exe",
    "C:\Program Files (x86)\mosquitto\mosquitto.exe"
)

# If service exists, try to get its path
if ($Service) {
    # Extract path: handle quotes and args cleanly
    $RawPath = (Get-WmiObject win32_service | Where-Object {$_.Name -eq $ServiceName} | Select-Object -ExpandProperty PathName) -replace '"', ''
    if ($RawPath -match "(.*\.exe)") {
        $ServicePath = $Matches[1]
        if ($ServicePath -and (Test-Path $ServicePath)) {
            $Paths += $ServicePath
        }
    }
}

$MosquittoPath = $null
foreach ($path in ($Paths | Select-Object -Unique)) {
    if (Test-Path $path) {
        $MosquittoPath = $path
        break
    }
}

if (-not $MosquittoPath) {
    $MosquittoPath = (Get-Command mosquitto.exe -ErrorAction SilentlyContinue).Source
}

if ($MosquittoPath) {
    Write-Host "MQTT: Starting Mosquitto Broker from $MosquittoPath..." -ForegroundColor Cyan
    Start-Process $MosquittoPath -ArgumentList "-v"
} else {
    Write-Host "MQTT: Mosquitto broker not found. Please ensure it is installed and in your PATH." -ForegroundColor Red
    Write-Host "Download from: https://mosquitto.org/download/" -ForegroundColor White
}
