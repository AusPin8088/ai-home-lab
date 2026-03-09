# AI Home Lab

Docker-based local automation stack:

- Home Assistant
- Mosquitto
- Node-RED
- InfluxDB 3 Core
- Grafana
- Ollama
- Python agent (`services/agent`)

## Prerequisites

- Windows 11
- Docker Desktop (WSL2 backend)

## Project Layout

- `docker/compose.yaml`: main orchestration file
- `docker/.env`: local secrets/config used by compose
- `docker/.env.example`: template for new environments
- `docker/scripts/influxdb-init.sh`: Influx database bootstrap
- `services/agent/`: MQTT -> Influx ingest and suggestion service
- `scripts/dev.ps1`: day-to-day helper commands
- `scripts/backup.ps1`: backup helper
- `scripts/smoke.ps1`: end-to-end smoke test
- `scripts/ai-smoke.ps1`: AI action guardrail smoke test
- `scripts/voice-bridge.ps1`: PC voice/typed command bridge
- `scripts/voice-stt.py`: local Whisper STT helper
- `scripts/requirements-voice.txt`: Python dependencies for voice STT
- `docs/architecture.md`: system architecture and runtime layout
- `docs/runbook.md`: operations and troubleshooting
- `docs/backup-restore.md`: backup and restore workflow
- `docs/current-state-repro.md`: current state and exact reproducible commands
- `nodered/flows/ai-control-console.json`: importable Node-RED control console flow
- `grafana/dashboards/ai-action-guardrails.json`: guardrail dashboard
- `runtime/`: container runtime/state volumes (gitignored)

## Start / Stop

From repo root:

```powershell
.\scripts\dev.ps1 up
.\scripts\dev.ps1 ps
.\scripts\dev.ps1 health
.\scripts\dev.ps1 smoke
.\scripts\dev.ps1 ai-smoke
```

Stop:

```powershell
.\scripts\dev.ps1 down
```

Logs:

```powershell
.\scripts\dev.ps1 logs
.\scripts\dev.ps1 logs influxdb
```

Backup:

```powershell
.\scripts\dev.ps1 backup
```

Restart:

```powershell
.\scripts\dev.ps1 restart
.\scripts\dev.ps1 restart agent
```

## URLs

- Home Assistant: `http://localhost:8123`
- Node-RED: `http://localhost:1880`
- InfluxDB 3 Core: `http://localhost:8181`
- Grafana: `http://localhost:3000`
- Ollama API: `http://localhost:11434`

## Regional Settings (Celsius + Xiaomi OAuth)

Home Assistant units are configured in `ha/config/configuration.yaml`:

```yaml
homeassistant:
  unit_system: metric
  temperature_unit: C
```

Apply changes:

```powershell
docker restart homeassistant
```

For Xiaomi Home OAuth on Windows, `homeassistant.local` must resolve locally.
Run PowerShell as Administrator once:

```powershell
Add-Content -Path "C:\Windows\System32\drivers\etc\hosts" -Value "`n127.0.0.1 homeassistant.local"
ipconfig /flushdns
```

Then open:

- `http://homeassistant.local:8123`

## AI Action Bridge

The agent now supports guarded action execution for plugs 1..4:

- publish command text to MQTT topic `home/ai/command`
- agent parses command and executes HA `switch.turn_on`/`switch.turn_off`
- result is published to `home/ai/action_result`
- newly discovered controllable devices are suggested on `home/ai/device_suggestion`
- audit rows are stored in Influx measurement `agent_action`
- action mode topics:
  - current mode: `home/ai/mode`
  - set mode: `home/ai/mode/set`

Required in `docker/.env`:

- `HA_TOKEN=<home-assistant-long-lived-token>`
- `OLLAMA_MODEL=<model-name>` (default `llama3.1:8b`)

Optional model split:

- `ACTION_PARSE_OLLAMA_MODEL=<model-name>` (command/action parsing)
- `SUGGESTION_OLLAMA_MODEL=<model-name>` (event suggestions)
- if unset, both use `OLLAMA_MODEL`

Action modes:

- `suggest`: never execute, only reject with reason.
- `ask`: requires explicit confirmation (`confirm ...` or JSON `confirm:true`).
- `auto`: executes immediately when command passes guardrails.

Default: `ACTION_MODE_DEFAULT=auto` in this repo.

Guardrails:

- strict allowlist of plug entities (`plug 1..4`)
- rate limit (`ACTION_RATE_LIMIT_SECONDS`, default 2s)
- flip cooldown for rapid on/off (`ACTION_FLIP_COOLDOWN_SECONDS`, default 3s)
- source tagging (`manual|node_red|voice|api`) in action audit rows

Auto-discovery (safe):

- agent watches HA MQTT state topics for new `switch|light|fan|input_boolean` entities
- unknown entities are not auto-enabled for control
- agent publishes a suggestion event with approval command example
- approve/reject via command topic:
  - `approve device fan.some_entity as living room fan`
  - `reject device fan.some_entity`
- approved aliases are persisted to `runtime/agent/dynamic_aliases.json`

Node-RED control console:

- import `nodered/flows/ai-control-console.json`
- open `http://localhost:1880/ai-console`
- new-device suggestions appear as a popup modal with `Approve` / `Reject` buttons
- simplified chat-first layout with optional advanced controls
- press `Enter` in the command input to send

Multi-action command examples:

- `turn on plug 3 and 4 and turn off plug 2`
- `turn off plug 1, then turn on plug 2`
- `what devices do you control`
- `what can xiaomi fan do`

Fan command examples (after fan alias approval):

- `turn on xiaomi fan`
- `turn off xiaomi fan`
- `set xiaomi fan speed to 66`
- `turn the fan up a bit`
- `turn the fan down a bit`
- `turn on xiaomi fan oscillation`
- `turn off xiaomi fan oscillation`
- `set xiaomi fan to sleeping mode`
- `set xiaomi fan to direct breeze`

Natural phrase behavior:

- `turn the fan a bit` = oscillate briefly, then auto-stop (default 5s)
- `turn the fan a bit for 10 seconds` = oscillate for 10s, then auto-stop

Optional non-plug aliases:

- set `ACTION_EXTRA_ENTITY_MAP_JSON` in `docker/.env`, for example
  `{"desk lamp":"light.desk_lamp","bedroom fan":"fan.bedroom_fan"}`

Discovery noise filtering:

- helper/config entities are ignored by default in new-device suggestions
- default ignore covers patterns such as:
  `physical_controls_locked`, `brightness`, `indicator`, `auto_off_enabled`, `auto_update_enabled`, `led`
- configurable via:
  `ACTION_DEVICE_DISCOVERY_IGNORE_OBJECTID_REGEX` in `docker/.env`

Voice bridge (PC push-to-talk/typed fallback):

Use Python 3.12+ (3.14 is supported with the current requirements).

Install once on host Python:

```powershell
python -m venv .venv-voice
.\.venv-voice\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r .\scripts\requirements-voice.txt
```

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\voice-bridge.ps1
```

Optional flags:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\voice-bridge.ps1 -VerboseOutput
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\voice-bridge.ps1 -NoTts
```

Voice behavior (Phase 4):

- push-to-talk starts when you press Enter on empty input
- Whisper STT is primary; Windows speech is fallback if Whisper/Python unavailable
- language scope: English + Malay + Chinese
- typed input is always available
- command payload includes `raw_command` and `lang` metadata with `source=voice`
- concise result summary is shown and optionally spoken (TTS)

Recommended model upgrade (multilingual):

```powershell
docker exec -it ollama ollama pull qwen2.5:7b-instruct
```

Then set in `docker/.env`:

```env
OLLAMA_MODEL=qwen2.5:7b-instruct
VOICE_WHISPER_MODEL=large-v3-turbo
```

Apply:

```powershell
.\scripts\dev.ps1 restart agent
```

## Grafana Datasource

Grafana auto-provisions `InfluxDB3` datasource from:

- `grafana/provisioning/datasources/influxdb3.yaml`

## Influx Auth Model

- InfluxDB 3 runs with auth enabled.
- Admin token file is mounted at:
  - `runtime/influxdb3/secrets/admin-token.json`
- Compose env token:
  - `INFLUXDB_TOKEN` in `docker/.env`

The init job (`influxdb-init`) creates database `${INFLUXDB_DATABASE}` if missing.

## Documentation

- Architecture: `docs/architecture.md`
- Runbook: `docs/runbook.md`
- Backup/Restore: `docs/backup-restore.md`
- Interactive Assistant: `docs/interactive-assistant.md`
