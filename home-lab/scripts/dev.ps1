param(
  [Parameter(Mandatory = $true, Position = 0)]
  [ValidateSet("up", "down", "ps", "logs", "restart", "health", "backup", "smoke")]
  [string]$Command,

  [Parameter(Position = 1)]
  [string]$Service
)

$ErrorActionPreference = "Stop"
$composeDir = Join-Path $PSScriptRoot "..\\docker"

function Get-HttpStatusCode {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Uri
  )

  try {
    $code = curl.exe -s -o NUL -w "%{http_code}" --max-time 8 $Uri
    return [int]$code
  } catch {
    return 0
  }
}

Push-Location $composeDir
try {
  switch ($Command) {
    "up" {
      docker compose up -d --build
      break
    }
    "down" {
      docker compose down
      break
    }
    "ps" {
      docker compose ps
      break
    }
    "logs" {
      if ($Service) {
        docker compose logs -f --tail=200 $Service
      } else {
        docker compose logs -f --tail=200
      }
      break
    }
    "restart" {
      if ($Service) {
        docker compose restart $Service
      } else {
        docker compose restart
      }
      break
    }
    "health" {
      docker compose ps
      Write-Host ""

      $checks = @(
        @{ Name = "Home Assistant"; Uri = "http://localhost:8123" },
        @{ Name = "Node-RED"; Uri = "http://localhost:1880" },
        @{ Name = "InfluxDB 3"; Uri = "http://localhost:8181/health" },
        @{ Name = "Grafana"; Uri = "http://localhost:3000/api/health" },
        @{ Name = "Ollama"; Uri = "http://localhost:11434/api/tags" }
      )

      foreach ($check in $checks) {
        $status = Get-HttpStatusCode -Uri $check.Uri
        if ($status -eq 0) {
          Write-Host ("{0,-15} FAIL  {1}" -f $check.Name, $check.Uri)
        } elseif ($status -eq 200 -or $status -eq 401) {
          Write-Host ("{0,-15} OK    {1} ({2})" -f $check.Name, $check.Uri, $status)
        } else {
          Write-Host ("{0,-15} WARN  {1} ({2})" -f $check.Name, $check.Uri, $status)
        }
      }
      break
    }
    "backup" {
      $backupScript = Join-Path $PSScriptRoot "backup.ps1"
      powershell -NoProfile -ExecutionPolicy Bypass -File $backupScript
      break
    }
    "smoke" {
      $smokeScript = Join-Path $PSScriptRoot "smoke.ps1"
      powershell -NoProfile -ExecutionPolicy Bypass -File $smokeScript -InjectTests
      break
    }
  }
} finally {
  Pop-Location
}
