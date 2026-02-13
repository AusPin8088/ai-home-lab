param(
  [switch]$NoTts
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$envPath = Join-Path $repoRoot "docker\.env"

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

function Publish-Mqtt {
  param(
    [Parameter(Mandatory = $true)] [string]$User,
    [Parameter(Mandatory = $true)] [string]$Password,
    [Parameter(Mandatory = $true)] [string]$Topic,
    [Parameter(Mandatory = $true)] [string]$Payload
  )
  $Payload | docker exec -i mosquitto mosquitto_pub -h localhost -u $User -P $Password -t $Topic -s | Out-Null
}

function Receive-MqttOnce {
  param(
    [Parameter(Mandatory = $true)] [string]$User,
    [Parameter(Mandatory = $true)] [string]$Password,
    [Parameter(Mandatory = $true)] [string]$Topic
  )
  return docker exec mosquitto mosquitto_sub -h localhost -u $User -P $Password -t $Topic -C 1 -W 8
}

$mqttUser = Get-EnvVar -Name "MQTT_USER"
$mqttPassword = Get-EnvVar -Name "MQTT_PASSWORD"

$speechAvailable = $true
try {
  Add-Type -AssemblyName System.Speech
}
catch {
  $speechAvailable = $false
}

$recognizer = $null
$speaker = $null
if ($speechAvailable) {
  try {
    $recognizer = New-Object System.Speech.Recognition.SpeechRecognitionEngine
    $recognizer.SetInputToDefaultAudioDevice()
    $recognizer.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
    if (-not $NoTts) {
      $speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
    }
  }
  catch {
    $speechAvailable = $false
  }
}

Write-Host "Voice bridge ready."
Write-Host "Press Enter to speak, type command text directly, or type 'q' to quit."

while ($true) {
  $raw = Read-Host "Input"
  if ($raw -eq "q") {
    break
  }

  $command = ""
  $confirm = $false

  if ([string]::IsNullOrWhiteSpace($raw)) {
    if ($speechAvailable -and $recognizer) {
      Write-Host "Listening for up to 5 seconds..."
      $result = $recognizer.Recognize([TimeSpan]::FromSeconds(5))
      if ($result -and $result.Text) {
        $command = $result.Text.Trim()
        Write-Host ("Heard: {0}" -f $command)
      }
      else {
        Write-Host "No speech recognized. Type command manually."
        continue
      }
    }
    else {
      Write-Host "Speech engine unavailable. Type command manually."
      continue
    }
  }
  else {
    $command = $raw.Trim()
  }

  if ($command.ToLower().StartsWith("confirm ")) {
    $confirm = $true
  }

  $payload = @{
    command = $command
    source  = "voice"
    confirm = $confirm
  } | ConvertTo-Json -Compress

  Publish-Mqtt -User $mqttUser -Password $mqttPassword -Topic "home/ai/command" -Payload $payload

  $resultRaw = Receive-MqttOnce -User $mqttUser -Password $mqttPassword -Topic "home/ai/action_result"
  if ([string]::IsNullOrWhiteSpace($resultRaw)) {
    Write-Host "No action_result received within timeout."
    continue
  }

  try {
    $result = $resultRaw | ConvertFrom-Json
    $line = "Status=$($result.status), Mode=$($result.mode), Detail=$($result.detail)"
    Write-Host $line
    if ($speaker) {
      $speaker.SpeakAsync("Status $($result.status). $($result.detail)") | Out-Null
    }
  }
  catch {
    Write-Host ("Raw result: {0}" -f $resultRaw)
  }
}

Write-Host "Voice bridge stopped."
