param(
  [switch]$NoTts,
  [switch]$VerboseOutput
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$envPath = Join-Path $repoRoot "docker\.env"
$voiceSttScript = Join-Path $PSScriptRoot "voice-stt.py"

function Get-EnvVar {
  param(
    [Parameter(Mandatory = $true)] [string]$Name,
    [string]$Default = ""
  )
  if (-not (Test-Path $envPath)) {
    if ($PSBoundParameters.ContainsKey("Default")) {
      return $Default
    }
    throw "Missing env file at $envPath"
  }
  $line = Get-Content -Path $envPath | Where-Object { $_ -match "^$Name=" } | Select-Object -First 1
  if (-not $line) {
    if ($PSBoundParameters.ContainsKey("Default")) {
      return $Default
    }
    throw "Missing '$Name' in $envPath"
  }
  return $line.Split("=", 2)[1]
}

function Get-FirstAvailableCommand {
  param(
    [Parameter(Mandatory = $true)] [string[]]$Candidates
  )
  foreach ($candidate in $Candidates) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) {
      return $candidate
    }
  }
  return ""
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
    [Parameter(Mandatory = $true)] [string]$Topic,
    [int]$TimeoutSeconds = 8
  )
  return docker exec mosquitto mosquitto_sub -h localhost -u $User -P $Password -t $Topic -C 1 -W $TimeoutSeconds
}

function Receive-MqttAfterPublish {
  param(
    [Parameter(Mandatory = $true)] [string]$User,
    [Parameter(Mandatory = $true)] [string]$Password,
    [Parameter(Mandatory = $true)] [string]$Topic,
    [Parameter(Mandatory = $true)] [int]$TimeoutSeconds,
    [Parameter(Mandatory = $true)] [scriptblock]$PublishAction,
    [int]$PublishDelayMs = 150
  )

  $job = Start-Job -ScriptBlock {
    param($u, $p, $t, $w)
    docker exec mosquitto mosquitto_sub -h localhost -u $u -P $p -t $t -C 1 -W $w
  } -ArgumentList $User, $Password, $Topic, $TimeoutSeconds

  try {
    Start-Sleep -Milliseconds $PublishDelayMs
    & $PublishAction
    $finished = Wait-Job -Job $job -Timeout ($TimeoutSeconds + 2)
    if (-not $finished) {
      return ""
    }
    $raw = Receive-Job -Job $job -ErrorAction SilentlyContinue
    if (-not $raw) {
      return ""
    }
    return [string]($raw | Select-Object -Last 1)
  }
  finally {
    if ($job) {
      if ($job.State -eq "Running") {
        Stop-Job -Job $job -Force -ErrorAction SilentlyContinue | Out-Null
      }
      Remove-Job -Job $job -Force -ErrorAction SilentlyContinue | Out-Null
    }
  }
}

function Get-CurrentMode {
  param(
    [Parameter(Mandatory = $true)] [string]$User,
    [Parameter(Mandatory = $true)] [string]$Password
  )
  $raw = Receive-MqttOnce -User $User -Password $Password -Topic "home/ai/mode" -TimeoutSeconds 3
  if ([string]::IsNullOrWhiteSpace($raw)) {
    return "auto"
  }
  try {
    $obj = $raw | ConvertFrom-Json
    $mode = [string]$obj.mode
    if ($mode -in @("suggest", "ask", "auto")) {
      return $mode
    }
  }
  catch {
    return "auto"
  }
  return "auto"
}

function Replace-ChineseDigits {
  param(
    [Parameter(Mandatory = $true)] [string]$Text
  )
  $converted = [regex]::Replace($Text, "\u4e00", "1")
  $converted = [regex]::Replace($converted, "\u4e8c", "2")
  $converted = [regex]::Replace($converted, "\u4e24", "2")
  $converted = [regex]::Replace($converted, "\u4e09", "3")
  $converted = [regex]::Replace($converted, "\u56db", "4")
  return $converted
}

function Normalize-VoiceCommand {
  param(
    [Parameter(Mandatory = $true)] [string]$RawCommand,
    [Parameter(Mandatory = $true)] [string]$Lang
  )
  $text = $RawCommand.Trim()
  $langNorm = $Lang.ToLower()
  $normalized = $text.ToLower()
  $normalized = Replace-ChineseDigits -Text $normalized
  $normalized = [regex]::Replace($normalized, "[^\w\s\u4e00-\u9fff]", " ")
  $normalized = [regex]::Replace($normalized, "\s+", " ").Trim()

  # Common Cantonese/Whisper variants for "plug/socket".
  $normalized = [regex]::Replace($normalized, "\u53c9\u5934|\u53c9\u982d", "\u63d2\u5934")
  $normalized = [regex]::Replace($normalized, "\u63d2\u82cf|\u63d2\u8607|\u63d2\u8607", "\u63d2\u5ea7")
  $normalized = [regex]::Replace($normalized, "x\u505a|xzuo", "\u63d2\u5ea7")

  if ([string]::IsNullOrWhiteSpace($normalized)) {
    return @{
      command = ""
      lang    = $langNorm
    }
  }

  if (
    $normalized -match "what devices do you control" -or
    $normalized -match "what can you control" -or
    $normalized -match "device list" -or
    $normalized -match "senarai peranti" -or
    $normalized -match "peranti apa" -or
    $normalized -match "\u4f60\u53ef\u4ee5\u63a7\u5236\u4ec0\u4e48" -or
    $normalized -match "\u4f60\u80fd\u63a7\u5236\u4ec0\u4e48" -or
    $normalized -match "\u8bbe\u5907\u5217\u8868" -or
    $normalized -match "\u88dd\u7f6e\u5217\u8868"
  ) {
    return @{
      command = "what devices do you control"
      lang    = $langNorm
    }
  }

  if (
    $normalized -match "what can xiaomi fan do" -or
    $normalized -match "what can the fan do" -or
    $normalized -match "apa kipas xiaomi boleh" -or
    $normalized -match "kipas xiaomi boleh buat apa" -or
    $normalized -match "\u5c0f\u7c73\u98ce\u6247.*\u80fd.*\u505a\u4ec0\u4e48" -or
    $normalized -match "\u5c0f\u7c73\u98a8\u6247.*\u80fd.*\u505a\u4ec0\u9ebc" -or
    $normalized -match "\u98ce\u6247.*\u53ef\u4ee5.*\u505a\u4ec0\u4e48" -or
    $normalized -match "\u98a8\u6247.*\u53ef\u4ee5.*\u505a\u4ec0\u9ebc"
  ) {
    return @{
      command = "what can xiaomi fan do"
      lang    = $langNorm
    }
  }

  if (
    $normalized -match "xiao mi fan" -or
    $normalized -match "kipas xiaomi" -or
    $normalized -match "\u5c0f\u7c73\u98ce\u6247" -or
    $normalized -match "\u5c0f\u7c73\u98a8\u6247"
  ) {
    $normalized = [regex]::Replace($normalized, "xiao mi fan", "xiaomi fan")
    $normalized = [regex]::Replace($normalized, "kipas xiaomi", "xiaomi fan")
    $normalized = [regex]::Replace($normalized, "\u5c0f\u7c73\u98ce\u6247|\u5c0f\u7c73\u98a8\u6247", "xiaomi fan")
  }

  # Bare fan mention is treated as a capability query to avoid accidental actions.
  if ($normalized -eq "xiaomi fan" -or $normalized -eq "\u5c0f\u7c73\u98ce\u6247" -or $normalized -eq "\u5c0f\u7c73\u98a8\u6247") {
    return @{
      command = "what can xiaomi fan do"
      lang    = $langNorm
    }
  }

  $plugOnRegexes = @(
    "\b(turn on|switch on|power on)\b.*\b(plug|outlet)\s*([1-4])\b",
    "\b(on|hidupkan|buka)\b.*\b(plug|soket|outlet)\s*([1-4])\b",
    "(\u6253\u5f00|\u6253\u958b|\u958b\u555f|\u5f00\u542f|\u958b|\u5f00)\s*(\u63d2\u5ea7|\u63d2\u5934|\u63d2\u982d|\u63d2\u8607)\s*([1-4])"
  )
  foreach ($rx in $plugOnRegexes) {
    $m = [regex]::Match($normalized, $rx)
    if ($m.Success) {
      $num = $m.Groups[$m.Groups.Count - 1].Value
      return @{
        command = "turn on plug $num"
        lang    = $langNorm
      }
    }
  }

  $plugOffRegexes = @(
    "\b(turn off|switch off|power off|shut off)\b.*\b(plug|outlet)\s*([1-4])\b",
    "\b(off|tutup|padam|matikan)\b.*\b(plug|soket|outlet)\s*([1-4])\b",
    "(\u5173\u95ed|\u95dc\u9589|\u5173|\u95dc)\s*(\u63d2\u5ea7|\u63d2\u5934|\u63d2\u982d|\u63d2\u8607)\s*([1-4])"
  )
  foreach ($rx in $plugOffRegexes) {
    $m = [regex]::Match($normalized, $rx)
    if ($m.Success) {
      $num = $m.Groups[$m.Groups.Count - 1].Value
      return @{
        command = "turn off plug $num"
        lang    = $langNorm
      }
    }
  }

  if (
    $normalized -match "(turn on|switch on|power on|hidupkan|buka|\u6253\u5f00|\u6253\u958b|\u958b\u555f|\u5f00\u542f|\u958b|\u5f00).*(fan|xiaomi fan|kipas|\u98ce\u6247|\u98a8\u6247)" -or
    $normalized -match "(fan|xiaomi fan|kipas|\u98ce\u6247|\u98a8\u6247).*(turn on|switch on|power on|hidupkan|buka|\u6253\u5f00|\u6253\u958b|\u958b\u555f|\u5f00\u542f|\u958b|\u5f00)"
  ) {
    return @{
      command = "turn on xiaomi fan"
      lang    = $langNorm
    }
  }
  if (
    $normalized -match "(turn off|switch off|power off|shut off|tutup|padam|matikan|\u5173\u95ed|\u95dc\u9589|\u95dc\u9589|\u5173|\u95dc|\u95dc\u6389|\u5173\u6389).*(fan|xiaomi fan|kipas|\u98ce\u6247|\u98a8\u6247)" -or
    $normalized -match "(fan|xiaomi fan|kipas|\u98ce\u6247|\u98a8\u6247).*(turn off|switch off|power off|shut off|tutup|padam|matikan|\u5173\u95ed|\u95dc\u9589|\u95dc\u9589|\u5173|\u95dc|\u95dc\u6389|\u5173\u6389)"
  ) {
    return @{
      command = "turn off xiaomi fan"
      lang    = $langNorm
    }
  }

  if (
    $normalized -match "(increase|faster|up|kuatkan|naikkan|tambah laju|\u52a0\u5feb|\u8c03\u9ad8|\u8abf\u9ad8).*(fan|xiaomi fan|kipas|\u98ce\u6247|\u98a8\u6247)" -or
    $normalized -match "(fan|xiaomi fan|kipas|\u98ce\u6247|\u98a8\u6247).*(increase|faster|up|kuatkan|naikkan|tambah laju|\u52a0\u5feb|\u8c03\u9ad8|\u8abf\u9ad8)"
  ) {
    return @{
      command = "increase xiaomi fan speed"
      lang    = $langNorm
    }
  }
  if (
    $normalized -match "(decrease|slower|down|perlahan|kurangkan|turunkan|\u51cf\u901f|\u6e1b\u901f|\u8c03\u4f4e|\u8abf\u4f4e).*(fan|xiaomi fan|kipas|\u98ce\u6247|\u98a8\u6247)" -or
    $normalized -match "(fan|xiaomi fan|kipas|\u98ce\u6247|\u98a8\u6247).*(decrease|slower|down|perlahan|kurangkan|turunkan|\u51cf\u901f|\u6e1b\u901f|\u8c03\u4f4e|\u8abf\u4f4e)"
  ) {
    return @{
      command = "decrease xiaomi fan speed"
      lang    = $langNorm
    }
  }

  if (
    $normalized -match "(oscillat|swing|left right|kiri kanan|ayun|\u6447\u5934|\u6416\u982d).*(off|stop|disable|tutup|henti|\u5173\u95ed|\u95dc\u9589|\u5173|\u95dc)" -or
    $normalized -match "(off|stop|disable|tutup|henti|\u5173\u95ed|\u95dc\u9589|\u5173|\u95dc).*(oscillat|swing|left right|kiri kanan|ayun|\u6447\u5934|\u6416\u982d)"
  ) {
    return @{
      command = "turn off xiaomi fan oscillation"
      lang    = $langNorm
    }
  }
  if (
    $normalized -match "(oscillat|swing|left right|kiri kanan|ayun|\u6447\u5934|\u6416\u982d).*(on|start|enable|hidupkan|buka|\u6253\u5f00|\u958b\u555f|\u5f00\u542f|\u5f00)" -or
    $normalized -match "(on|start|enable|hidupkan|buka|\u6253\u5f00|\u958b\u555f|\u5f00\u542f|\u5f00).*(oscillat|swing|left right|kiri kanan|ayun|\u6447\u5934|\u6416\u982d)"
  ) {
    return @{
      command = "turn on xiaomi fan oscillation"
      lang    = $langNorm
    }
  }

  if (
    $normalized -match "(turn|set|make).*(fan|kipas|\u98ce\u6247|\u98a8\u6247).*(a bit|a little|sedikit|sikit|\u4e00\u70b9|\u4e00\u9ede)"
  ) {
    return @{
      command = "turn the fan a bit"
      lang    = $langNorm
    }
  }

  return @{
    command = $normalized
    lang    = $langNorm
  }
}

function Get-VoiceCapture {
  param(
    [Parameter(Mandatory = $true)] [string]$SttEngine,
    [Parameter(Mandatory = $true)] [int]$TimeoutSeconds,
    [Parameter(Mandatory = $true)] [string]$Model,
    [Parameter(Mandatory = $true)] [string]$Device,
    [Parameter(Mandatory = $true)] [string]$ComputeType,
    [Parameter(Mandatory = $true)] [string]$Languages,
    [Parameter(Mandatory = $true)] [bool]$WindowsSpeechAvailable,
    $WindowsRecognizer,
    [Parameter(Mandatory = $true)] [string]$PythonCommand
  )
  if ($SttEngine -eq "whisper") {
    if (-not $PythonCommand) {
      Write-Warning "Whisper STT requested but Python is unavailable. Falling back to Windows speech."
    }
    elseif (-not (Test-Path $voiceSttScript)) {
      Write-Warning "Whisper STT helper not found at $voiceSttScript. Falling back to Windows speech."
    }
    else {
      Write-Host ("Listening (Whisper) for up to {0}s..." -f $TimeoutSeconds)
      $args = @(
        $voiceSttScript,
        "--timeout-seconds", $TimeoutSeconds,
        "--model", $Model,
        "--device", $Device,
        "--compute-type", $ComputeType,
        "--languages", $Languages
      )
      $nativePrefSet = $false
      $nativePrefOld = $null
      $errorPrefOld = $ErrorActionPreference
      if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -Scope Global -ErrorAction SilentlyContinue) {
        $nativePrefSet = $true
        $nativePrefOld = $global:PSNativeCommandUseErrorActionPreference
        $global:PSNativeCommandUseErrorActionPreference = $false
      }

      try {
        # Windows PowerShell can treat native stderr as errors when ErrorActionPreference=Stop.
        # Downgrade only for this native call so traceback text can be parsed and handled below.
        $ErrorActionPreference = "Continue"
        $output = @(& $PythonCommand @args 2>&1)
        $exitCode = $LASTEXITCODE
      }
      finally {
        $ErrorActionPreference = $errorPrefOld
        if ($nativePrefSet) {
          $global:PSNativeCommandUseErrorActionPreference = $nativePrefOld
        }
      }

      if ($exitCode -eq 0 -and $output) {
        $line = [string]($output | Select-Object -Last 1)
        try {
          $obj = $line | ConvertFrom-Json
          if ($obj.ok -and -not [string]::IsNullOrWhiteSpace([string]$obj.text)) {
            return @{
              ok   = $true
              text = [string]$obj.text
              lang = [string]$obj.lang
              stt  = "whisper"
            }
          }
          if ($obj.error) {
            Write-Warning ("Whisper STT failed: {0}" -f $obj.error)
          }
        }
        catch {
          Write-Warning ("Whisper STT returned non-JSON output: {0}" -f $line)
        }
      }
      else {
        $err = [string]($output | Out-String)
        if (-not [string]::IsNullOrWhiteSpace($err)) {
          Write-Warning ("Whisper STT failed (exit {0}): {1}" -f $exitCode, $err.Trim())
        }
      }
    }
  }

  if ($WindowsSpeechAvailable -and $WindowsRecognizer) {
    Write-Host ("Listening (Windows Speech) for up to {0}s..." -f $TimeoutSeconds)
    $result = $WindowsRecognizer.Recognize([TimeSpan]::FromSeconds($TimeoutSeconds))
    if ($result -and $result.Text) {
      return @{
        ok   = $true
        text = $result.Text.Trim()
        lang = "en"
        stt  = "windows"
      }
    }
    return @{
      ok    = $false
      error = "No speech recognized."
    }
  }

  return @{
    ok    = $false
    error = "No STT backend available. Use typed command input."
  }
}

function Parse-ConfirmIntent {
  param(
    [Parameter(Mandatory = $true)] [string]$Text
  )
  $normalized = [regex]::Replace($Text.ToLower(), "\s+", " ").Trim()
  $confirmPhrases = @(
    "confirm",
    "yes execute",
    "go ahead",
    "proceed",
    "do it",
    "sahkan",
    "ya jalankan",
    "teruskan",
    "\u786e\u8ba4",
    "\u78ba\u8a8d",
    "\u8bf7\u6267\u884c",
    "\u8acb\u57f7\u884c"
  )
  foreach ($phrase in $confirmPhrases) {
    if ($normalized.StartsWith($phrase)) {
      $remaining = $normalized.Substring($phrase.Length).Trim()
      return @{
        confirm = $true
        text    = $remaining
      }
    }
  }
  return @{
    confirm = $false
    text    = $normalized
  }
}

function Get-ResultSummary {
  param(
    $ResultObject
  )
  if (-not $ResultObject) {
    return @{
      line = "No result object returned."
      tts  = "No result returned."
    }
  }
  $status = [string]$ResultObject.status
  $mode = [string]$ResultObject.mode
  $detail = [string]$ResultObject.detail
  $action = [string]$ResultObject.action
  $command = [string]$ResultObject.command

  if ($status -eq "executed") {
    return @{
      line = ("Done: action={0}, mode={1}, command='{2}'" -f $action, $mode, $command)
      tts  = "Done. Command executed."
    }
  }
  if ($status -eq "rejected") {
    return @{
      line = ("Rejected: {0}" -f $detail)
      tts  = "Rejected. " + $detail
    }
  }
  if ($status -eq "failed") {
    return @{
      line = ("Failed: {0}" -f $detail)
      tts  = "Failed. " + $detail
    }
  }

  return @{
    line = ("Status={0}, Mode={1}, Detail={2}" -f $status, $mode, $detail)
    tts  = ("Status " + $status)
  }
}

$mqttUser = Get-EnvVar -Name "MQTT_USER"
$mqttPassword = Get-EnvVar -Name "MQTT_PASSWORD"
$sttEngine = (Get-EnvVar -Name "VOICE_STT_ENGINE" -Default "whisper").ToLower()
$whisperModel = Get-EnvVar -Name "VOICE_WHISPER_MODEL" -Default "small"
$whisperDevice = Get-EnvVar -Name "VOICE_WHISPER_DEVICE" -Default "cpu"
$whisperComputeType = Get-EnvVar -Name "VOICE_WHISPER_COMPUTE_TYPE" -Default "int8"
$voiceLanguages = Get-EnvVar -Name "VOICE_LANGUAGES" -Default "en,ms,zh"
$timeoutRaw = Get-EnvVar -Name "VOICE_PUSH_TO_TALK_TIMEOUT_SECONDS" -Default "5"
$resultTimeoutRaw = Get-EnvVar -Name "VOICE_RESULT_TIMEOUT_SECONDS" -Default "20"
$voiceTtsEnabledRaw = Get-EnvVar -Name "VOICE_TTS_ENABLED" -Default "true"
$voiceTtsEnabled = $voiceTtsEnabledRaw.ToLower() -in @("1", "true", "yes", "on")

$timeoutSeconds = 5
if (-not [int]::TryParse($timeoutRaw, [ref]$timeoutSeconds)) {
  $timeoutSeconds = 5
}
$timeoutSeconds = [Math]::Max(2, [Math]::Min(15, $timeoutSeconds))

$resultTimeoutSeconds = 20
if (-not [int]::TryParse($resultTimeoutRaw, [ref]$resultTimeoutSeconds)) {
  $resultTimeoutSeconds = 20
}
$resultTimeoutSeconds = [Math]::Max(8, [Math]::Min(60, $resultTimeoutSeconds))

$pythonCommand = Get-FirstAvailableCommand -Candidates @("python", "py")

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
    if (-not $NoTts -and $voiceTtsEnabled) {
      $speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
    }
  }
  catch {
    $speechAvailable = $false
  }
}

Write-Host "Voice bridge ready."
Write-Host ("STT engine priority: {0}" -f $sttEngine)
if ($sttEngine -eq "whisper") {
  if ($pythonCommand) {
    Write-Host ("Whisper backend available via '{0}'. Model={1}, Device={2}, Compute={3}" -f $pythonCommand, $whisperModel, $whisperDevice, $whisperComputeType)
    if ($whisperDevice -eq "cpu" -and $whisperModel -match "large") {
      Write-Warning "Large Whisper model on CPU can take a long time to transcribe per command. Use small/medium for lower latency."
    }
  }
  else {
    Write-Warning "Python not found. Whisper will be unavailable; Windows speech fallback will be used if available."
  }
}
Write-Host "Press Enter to speak, type command text directly, or type 'q' to quit."

while ($true) {
  $raw = Read-Host "Input"
  if ($raw -eq "q") {
    break
  }

  $rawCommand = ""
  $detectedLang = "en"
  $sttUsed = "typed"

  if ([string]::IsNullOrWhiteSpace($raw)) {
    $capture = Get-VoiceCapture `
      -SttEngine $sttEngine `
      -TimeoutSeconds $timeoutSeconds `
      -Model $whisperModel `
      -Device $whisperDevice `
      -ComputeType $whisperComputeType `
      -Languages $voiceLanguages `
      -WindowsSpeechAvailable $speechAvailable `
      -WindowsRecognizer $recognizer `
      -PythonCommand $pythonCommand

    if (-not $capture.ok) {
      Write-Host ([string]$capture.error)
      continue
    }

    $rawCommand = [string]$capture.text
    $detectedLang = [string]$capture.lang
    $sttUsed = [string]$capture.stt
    Write-Host ("Heard ({0}, {1}): {2}" -f $sttUsed, $detectedLang, $rawCommand)
  }
  else {
    $rawCommand = $raw.Trim()
  }

  if ([string]::IsNullOrWhiteSpace($rawCommand)) {
    Write-Host "Empty command, skipped."
    continue
  }

  $confirmParse = Parse-ConfirmIntent -Text $rawCommand
  $explicitConfirm = [bool]$confirmParse.confirm
  $commandWithoutConfirmWord = [string]$confirmParse.text
  if ([string]::IsNullOrWhiteSpace($commandWithoutConfirmWord)) {
    $commandWithoutConfirmWord = $rawCommand.Trim()
  }

  $normalizedInfo = Normalize-VoiceCommand -RawCommand $commandWithoutConfirmWord -Lang $detectedLang
  $normalizedCommand = [string]$normalizedInfo.command
  $langTag = [string]$normalizedInfo.lang
  if ([string]::IsNullOrWhiteSpace($normalizedCommand)) {
    Write-Host "Could not normalize command, skipped."
    continue
  }

  $mode = Get-CurrentMode -User $mqttUser -Password $mqttPassword
  $confirm = $false
  if ($mode -eq "ask" -and $explicitConfirm) {
    $confirm = $true
  }

  if ($VerboseOutput) {
    Write-Host ("Mode={0}, explicitConfirm={1}, confirmSent={2}, lang={3}" -f $mode, $explicitConfirm, $confirm, $langTag)
    Write-Host ("Normalized command={0}" -f $normalizedCommand)
  }

  $payloadObj = @{
    command     = $normalizedCommand
    source      = "voice"
    confirm     = $confirm
    raw_command = $rawCommand
    lang        = $langTag
  }
  $payload = $payloadObj | ConvertTo-Json -Compress

  $resultRaw = Receive-MqttAfterPublish `
    -User $mqttUser `
    -Password $mqttPassword `
    -Topic "home/ai/action_result" `
    -TimeoutSeconds $resultTimeoutSeconds `
    -PublishAction { Publish-Mqtt -User $mqttUser -Password $mqttPassword -Topic "home/ai/command" -Payload $payload }
  if ([string]::IsNullOrWhiteSpace($resultRaw)) {
    Write-Host "No action_result received within timeout."
    if ($speaker) {
      $speaker.SpeakAsync("No response received.") | Out-Null
    }
    continue
  }

  try {
    $result = $resultRaw | ConvertFrom-Json
    $summary = Get-ResultSummary -ResultObject $result
    Write-Host $summary.line
    if ($VerboseOutput) {
      Write-Host ("Raw result JSON: {0}" -f ($result | ConvertTo-Json -Depth 8 -Compress))
    }
    if ($speaker) {
      $speaker.SpeakAsync($summary.tts) | Out-Null
    }
  }
  catch {
    Write-Host ("Raw result (non-JSON): {0}" -f $resultRaw)
  }
}

Write-Host "Voice bridge stopped."
