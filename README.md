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
- audit rows are stored in Influx measurement `agent_action`
- action mode topics:
  - current mode: `home/ai/mode`
  - set mode: `home/ai/mode/set`

Required in `docker/.env`:

- `HA_TOKEN=<home-assistant-long-lived-token>`

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

Node-RED control console:

- import `nodered/flows/ai-control-console.json`
- open `http://localhost:1880/ai-console`

Multi-action command examples:

- `turn on plug 3 and 4 and turn off plug 2`
- `turn off plug 1, then turn on plug 2`

Optional non-plug aliases:

- set `ACTION_EXTRA_ENTITY_MAP_JSON` in `docker/.env`, for example
  `{"desk lamp":"light.desk_lamp","bedroom fan":"fan.bedroom_fan"}`

Voice bridge (PC push-to-talk/typed fallback):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\voice-bridge.ps1
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
