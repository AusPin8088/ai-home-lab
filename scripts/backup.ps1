param(
  [string]$OutputDir = ".\backups",
  [switch]$Online,
  [switch]$IncludeOllama,
  [switch]$Zip
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$composeDir = Join-Path $repoRoot "docker"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$resolvedOutputDir = if ([System.IO.Path]::IsPathRooted($OutputDir)) {
  $OutputDir
} else {
  Join-Path $repoRoot $OutputDir
}
$backupRoot = (Resolve-Path $resolvedOutputDir -ErrorAction SilentlyContinue)
if (-not $backupRoot) {
  New-Item -Path $resolvedOutputDir -ItemType Directory | Out-Null
  $backupRoot = Resolve-Path $resolvedOutputDir
}
$targetDir = Join-Path $backupRoot.Path ("home-lab-backup-" + $timestamp)

function Copy-Tree {
  param(
    [Parameter(Mandatory = $true)] [string]$Source,
    [Parameter(Mandatory = $true)] [string]$Destination
  )

  if (-not (Test-Path $Source)) {
    return
  }

  New-Item -ItemType Directory -Path $Destination -Force | Out-Null
  $null = robocopy $Source $Destination /MIR /R:2 /W:2 /NFL /NDL /NJH /NJS /NP
  if ($LASTEXITCODE -gt 7) {
    throw "Backup copy failed for '$Source' (robocopy exit code: $LASTEXITCODE)."
  }
}

$stoppedForBackup = $false
$services = @("homeassistant", "mosquitto", "nodered", "influxdb", "grafana", "agent")
if ($IncludeOllama) {
  $services += "ollama"
}

try {
  New-Item -ItemType Directory -Path $targetDir -Force | Out-Null

  if (-not $Online) {
    Push-Location $composeDir
    try {
      docker compose stop @services | Out-Host
      $stoppedForBackup = $true
    } finally {
      Pop-Location
    }
  }

  Copy-Tree -Source (Join-Path $repoRoot "ha\config") -Destination (Join-Path $targetDir "ha\config")
  Copy-Tree -Source (Join-Path $repoRoot "mosquitto\config") -Destination (Join-Path $targetDir "mosquitto\config")
  Copy-Tree -Source (Join-Path $repoRoot "runtime\mosquitto\data") -Destination (Join-Path $targetDir "runtime\mosquitto\data")
  Copy-Tree -Source (Join-Path $repoRoot "runtime\mosquitto\log") -Destination (Join-Path $targetDir "runtime\mosquitto\log")
  Copy-Tree -Source (Join-Path $repoRoot "runtime\nodered\data") -Destination (Join-Path $targetDir "runtime\nodered\data")
  Copy-Tree -Source (Join-Path $repoRoot "runtime\grafana\data") -Destination (Join-Path $targetDir "runtime\grafana\data")
  Copy-Tree -Source (Join-Path $repoRoot "runtime\influxdb3\data") -Destination (Join-Path $targetDir "runtime\influxdb3\data")
  Copy-Tree -Source (Join-Path $repoRoot "runtime\influxdb3\secrets") -Destination (Join-Path $targetDir "runtime\influxdb3\secrets")
  if ($IncludeOllama) {
    Copy-Tree -Source (Join-Path $repoRoot "runtime\ollama\data") -Destination (Join-Path $targetDir "runtime\ollama\data")
  }

  Copy-Item -Path (Join-Path $repoRoot "docker\.env") -Destination (Join-Path $targetDir "docker.env") -Force
  Copy-Item -Path (Join-Path $repoRoot "docker\compose.yaml") -Destination (Join-Path $targetDir "compose.yaml") -Force
  Copy-Item -Path (Join-Path $repoRoot "docs\runbook.md") -Destination (Join-Path $targetDir "runbook.md") -Force

  $manifest = @{
    created_at = (Get-Date).ToString("o")
    online_backup = [bool]$Online
    include_ollama = [bool]$IncludeOllama
    source_repo = $repoRoot
    services_stopped = if ($Online) { @() } else { $services }
  } | ConvertTo-Json -Depth 3
  Set-Content -Path (Join-Path $targetDir "manifest.json") -Value $manifest -Encoding ascii

  if ($Zip) {
    $zipPath = $targetDir + ".zip"
    if (Test-Path $zipPath) {
      Remove-Item -Path $zipPath -Force
    }
    Compress-Archive -Path (Join-Path $targetDir "*") -DestinationPath $zipPath
    Write-Host "Backup complete: $zipPath"
  } else {
    Write-Host "Backup complete: $targetDir"
  }
} finally {
  if ($stoppedForBackup) {
    Push-Location $composeDir
    try {
      # Use `up -d` so service definitions are reconciled after backup
      # (safe when mounts/paths changed over time).
      docker compose up -d | Out-Host
    } finally {
      Pop-Location
    }
  }
}
