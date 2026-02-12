param(
  [switch]$InjectTests
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$composeDir = Join-Path $repoRoot "docker"
$envPath = Join-Path $composeDir ".env"

function Get-EnvVar {
  param(
    [Parameter(Mandatory = $true)] [string]$Name
  )
  $line = Get-Content -Path $envPath | Where-Object { $_ -match "^$Name=" } | Select-Object -First 1
  if (-not $line) {
    throw "Missing '$Name' in $envPath"
  }
  return $line.Split("=", 2)[1]
}

function Get-InfluxCount {
  param(
    [Parameter(Mandatory = $true)] [string]$Token,
    [Parameter(Mandatory = $true)] [string]$Database,
    [Parameter(Mandatory = $true)] [string]$Query
  )
  $json = docker exec influxdb influxdb3 query --host http://127.0.0.1:8181 --token $Token --database $Database --format json "$Query"
  $rows = $json | ConvertFrom-Json
  if ($null -eq $rows -or $rows.Count -eq 0) {
    return 0
  }
  return [int]$rows[0].c
}

Push-Location $composeDir
try {
  $requiredServices = @("homeassistant", "mosquitto", "influxdb", "grafana", "agent")
  $running = docker compose ps --services --status running
  $missing = @()
  foreach ($svc in $requiredServices) {
    if ($running -notcontains $svc) {
      $missing += $svc
    }
  }
  if ($missing.Count -gt 0) {
    throw "Smoke test failed: required services not running -> $($missing -join ', ')"
  }

  $mqttUser = Get-EnvVar -Name "MQTT_USER"
  $mqttPassword = Get-EnvVar -Name "MQTT_PASSWORD"
  $influxToken = Get-EnvVar -Name "INFLUXDB_TOKEN"
  $influxDb = Get-EnvVar -Name "INFLUXDB_DATABASE"
  $stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

  if ($InjectTests) {
    docker exec mosquitto sh -lc "mosquitto_pub -h localhost -u '$mqttUser' -P '$mqttPassword' -t 'home/ha/switch/p304m_tapo_p304m_2/state' -m 'on'" | Out-Null
    docker exec mosquitto sh -lc "mosquitto_pub -h localhost -u '$mqttUser' -P '$mqttPassword' -t 'home/ha/binary_sensor/p304m_tapo_p304m_2_overheated/state' -m 'on'" | Out-Null
  }

  Start-Sleep -Seconds 4

  $checks = @(
    @{
      Name = "switch_state_rows"
      Query = "SELECT COUNT(*) AS c FROM mqtt_event WHERE topic = 'home/ha/switch/p304m_tapo_p304m_2/state'"
      Min = 1
    },
    @{
      Name = "switch_audit_rows"
      Query = "SELECT COUNT(*) AS c FROM mqtt_event WHERE topic = 'home/automation/p304m/switch_event'"
      Min = 1
    },
    @{
      Name = "safety_alert_rows"
      Query = "SELECT COUNT(*) AS c FROM mqtt_event WHERE topic = 'home/automation/p304m/safety_alert'"
      Min = 1
    }
  )

  $failed = @()
  foreach ($check in $checks) {
    $count = Get-InfluxCount -Token $influxToken -Database $influxDb -Query $check.Query
    if ($count -lt $check.Min) {
      $failed += "$($check.Name): $count (expected >= $($check.Min))"
    } else {
      Write-Host ("PASS {0,-20} count={1}" -f $check.Name, $count)
    }
  }

  if ($failed.Count -gt 0) {
    throw ("Smoke test failed at {0}. {1}" -f $stamp, ($failed -join " | "))
  }

  Write-Host "Smoke test passed at $stamp"
} finally {
  Pop-Location
}
