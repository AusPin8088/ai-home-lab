param()

$ErrorActionPreference = "Stop"

function Get-HttpStatusCode {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Uri
  )

  try {
    $code = curl.exe -s -o NUL -w "%{http_code}" --max-time 8 $Uri
    return [int]$code
  }
  catch {
    return 0
  }
}

$checks = @(
  @{ Name = "Home Assistant"; Uri = "http://localhost:8123"; Accept = @(200) },
  @{ Name = "Node-RED"; Uri = "http://localhost:1880"; Accept = @(200) },
  @{ Name = "InfluxDB 3"; Uri = "http://localhost:8181/health"; Accept = @(200, 401) },
  @{ Name = "Grafana"; Uri = "http://localhost:3000/api/health"; Accept = @(200) },
  @{ Name = "Ollama"; Uri = "http://localhost:11434/api/tags"; Accept = @(200) }
)

$failed = @()
foreach ($check in $checks) {
  $status = Get-HttpStatusCode -Uri $check.Uri
  if ($check.Accept -contains $status) {
    Write-Host ("OK   {0,-15} {1} ({2})" -f $check.Name, $check.Uri, $status)
  }
  else {
    Write-Host ("FAIL {0,-15} {1} ({2})" -f $check.Name, $check.Uri, $status)
    $failed += $check.Name
  }
}

if ($failed.Count -gt 0) {
  throw "Uptime check failed: $($failed -join ', ')"
}

Write-Host "Uptime check passed."
