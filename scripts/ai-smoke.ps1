param()

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

function Get-InfluxRows {
  param(
    [Parameter(Mandatory = $true)] [string]$Token,
    [Parameter(Mandatory = $true)] [string]$Database,
    [Parameter(Mandatory = $true)] [string]$Query
  )
  $jsonLines = docker exec influxdb influxdb3 query --host http://127.0.0.1:8181 --token $Token --database $Database --format jsonl "$Query"
  if ($null -eq $jsonLines -or $jsonLines.Count -eq 0) {
    return @()
  }
  $rows = @()
  foreach ($line in $jsonLines) {
    if (-not [string]::IsNullOrWhiteSpace($line)) {
      $rows += ($line | ConvertFrom-Json)
    }
  }
  return $rows
}

function Publish-Mqtt {
  param(
    [Parameter(Mandatory = $true)] [string]$User,
    [Parameter(Mandatory = $true)] [string]$Password,
    [Parameter(Mandatory = $true)] [string]$Topic,
    [Parameter(Mandatory = $true)] [string]$Payload
  )
  $Payload | docker exec -i mosquitto mosquitto_pub -h localhost -u $User -P $Password -t $Topic -s | Out-Null
}

Push-Location $composeDir
try {
  $requiredServices = @("homeassistant", "mosquitto", "influxdb", "agent")
  $running = docker compose ps --services --status running
  $missing = @()
  foreach ($svc in $requiredServices) {
    if ($running -notcontains $svc) {
      $missing += $svc
    }
  }
  if ($missing.Count -gt 0) {
    throw "AI smoke failed: required services not running -> $($missing -join ', ')"
  }

  $mqttUser = Get-EnvVar -Name "MQTT_USER"
  $mqttPassword = Get-EnvVar -Name "MQTT_PASSWORD"
  $influxToken = Get-EnvVar -Name "INFLUXDB_TOKEN"
  $influxDb = Get-EnvVar -Name "INFLUXDB_DATABASE"
  $stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
  $runId = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds().ToString()

  $cmdOff = "turn off plug 2 run$runId"
  $cmdOn = "turn on plug 2 run$runId"
  $cmdAmbiguous = "turn on then turn off plug 2 run$runId"
  $jsonCmd = @{
    command = $cmdOff
    source  = "node_red"
    confirm = $true
  } | ConvertTo-Json -Compress

  # Sequence to generate expected audit patterns
  Publish-Mqtt -User $mqttUser -Password $mqttPassword -Topic "home/ai/mode/set" -Payload "auto"
  Start-Sleep -Seconds 2
  Publish-Mqtt -User $mqttUser -Password $mqttPassword -Topic "home/ai/command" -Payload $cmdOff
  Start-Sleep -Seconds 3
  Publish-Mqtt -User $mqttUser -Password $mqttPassword -Topic "home/ai/command" -Payload $cmdOn
  Start-Sleep -Seconds 3
  Start-Sleep -Seconds 3
  Publish-Mqtt -User $mqttUser -Password $mqttPassword -Topic "home/ai/command" -Payload $cmdAmbiguous
  Start-Sleep -Seconds 2

  Publish-Mqtt -User $mqttUser -Password $mqttPassword -Topic "home/ai/mode/set" -Payload "ask"
  Start-Sleep -Seconds 2
  Publish-Mqtt -User $mqttUser -Password $mqttPassword -Topic "home/ai/command" -Payload $cmdOff
  Start-Sleep -Seconds 2
  Publish-Mqtt -User $mqttUser -Password $mqttPassword -Topic "home/ai/command" -Payload $jsonCmd
  Start-Sleep -Seconds 3

  Publish-Mqtt -User $mqttUser -Password $mqttPassword -Topic "home/ai/mode/set" -Payload "auto"
  Start-Sleep -Seconds 2
  Publish-Mqtt -User $mqttUser -Password $mqttPassword -Topic "home/ai/command" -Payload $cmdOn
  Start-Sleep -Milliseconds 200
  Publish-Mqtt -User $mqttUser -Password $mqttPassword -Topic "home/ai/command" -Payload $cmdOn
  Start-Sleep -Seconds 3
  Publish-Mqtt -User $mqttUser -Password $mqttPassword -Topic "home/ai/command" -Payload $cmdOff
  Start-Sleep -Milliseconds 2500
  Publish-Mqtt -User $mqttUser -Password $mqttPassword -Topic "home/ai/command" -Payload $cmdOn
  Start-Sleep -Seconds 4

  $rows = @()
  for ($attempt = 0; $attempt -lt 40; $attempt++) {
    $rows = Get-InfluxRows -Token $influxToken -Database $influxDb -Query "SELECT status, source, mode, action, command, detail FROM iox.agent_action WHERE command LIKE '%run$runId%' ORDER BY time DESC LIMIT 200"
    if ($rows.Count -ge 8) {
      break
    }
    Start-Sleep -Seconds 2
  }
  if ($rows.Count -eq 0) {
    throw "AI smoke failed: no action rows captured for run id $runId"
  }

  $checks = @(
    @{
      Name = "executed_off_or_on"
      Count = @($rows | Where-Object { $_.status -eq "executed" -and $_.action -in @("turn_on", "turn_off") }).Count
      Min = 2
    },
    @{
      Name = "multi_command_processed"
      Count = @($rows | Where-Object { $_.command -like "*$cmdAmbiguous*" }).Count
      Min = 1
    },
    @{
      Name = "ask_requires_confirm"
      Count = @($rows | Where-Object { $_.status -eq "rejected" -and $_.detail -like "mode=ask*" }).Count
      Min = 1
    },
    @{
      Name = "source_tagging_node_red"
      Count = @($rows | Where-Object { $_.status -eq "executed" -and $_.source -eq "node_red" }).Count
      Min = 1
    },
    @{
      Name = "rate_limited"
      Count = @($rows | Where-Object { $_.status -eq "rejected" -and $_.detail -like "rate limited:*" }).Count
      Min = 1
    },
    @{
      Name = "flip_cooldown"
      Count = 0
      Min = 1
    }
  )

  $cooldownRows = Get-InfluxRows -Token $influxToken -Database $influxDb -Query "SELECT status, detail FROM iox.agent_action WHERE status = 'rejected' AND detail LIKE 'cooldown active%' ORDER BY time DESC LIMIT 20"
  foreach ($check in $checks) {
    if ($check.Name -eq "flip_cooldown") {
      $check.Count = $cooldownRows.Count
    }
  }

  $failed = @()
  foreach ($check in $checks) {
    if ($check.Count -lt $check.Min) {
      $failed += "$($check.Name): $($check.Count) (expected >= $($check.Min))"
    } else {
      Write-Host ("PASS {0,-24} count={1}" -f $check.Name, $check.Count)
    }
  }

  if ($failed.Count -gt 0) {
    throw ("AI smoke failed at {0}. {1}" -f $stamp, ($failed -join " | "))
  }

  Write-Host "AI smoke passed at $stamp (run$runId)"
}
finally {
  Pop-Location
}
