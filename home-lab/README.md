# AI Home Lab

Docker-based home lab stack:

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

- `docker/compose.yaml`: Main orchestration file.
- `docker/.env`: Local secrets/config used by compose.
- `docker/.env.example`: Template for new environments.
- `docker/scripts/influxdb-init.sh`: Idempotent Influx database bootstrap.
- `services/agent/`: MQTT -> Influx ingest and suggestion service.
- `scripts/dev.ps1`: Day-to-day helper commands.
- `scripts/backup.ps1`: Backup helper (cold backup by default).
- `scripts/smoke.ps1`: End-to-end smoke test for MQTT -> HA automation -> Influx.
- `docs/runtime-lock.md`: Pinned image references.
- `docs/restore-checklist.md`: Restore workflow.

## Start / Stop

From repo root (`home-lab/`):

```powershell
.\scripts\dev.ps1 up
.\scripts\dev.ps1 ps
.\scripts\dev.ps1 health
.\scripts\dev.ps1 smoke
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

## Grafana Datasource

Grafana auto-provisions `InfluxDB3` datasource from:

- `grafana/provisioning/datasources/influxdb3.yaml`

Settings used:

- Query language: `SQL`
- URL: `http://influxdb:8181`
- Database: `${INFLUXDB_DATABASE}`
- Token: `${INFLUXDB_TOKEN}`

## Influx Auth Model

- InfluxDB 3 runs with auth enabled.
- Admin token file is mounted at:
  - `influxdb3/secrets/admin-token.json`
- Compose env token:
  - `INFLUXDB_TOKEN` in `docker/.env`

The init job (`influxdb-init`) creates database `${INFLUXDB_DATABASE}` if missing.

## Common Issues

- `Unauthorized` from Influx:
  - Check `INFLUXDB_TOKEN` in `docker/.env`.
  - Ensure token matches `influxdb3/secrets/admin-token.json`.
- Grafana datasource missing:
  - Restart Grafana after env/provisioning changes: `.\scripts\dev.ps1 restart grafana`
- MQTT auth failures:
  - Ensure `MQTT_USER` / `MQTT_PASSWORD` in `docker/.env` matches Mosquitto password file.

## Phase 2 Operations

- Smoke test (includes test event injection):
  - `.\scripts\dev.ps1 smoke`
- Backup (cold backup):
  - `.\scripts\dev.ps1 backup`
- Backup options:
  - `.\scripts\backup.ps1 -Online` (hot backup, less consistent)
  - `.\scripts\backup.ps1 -IncludeOllama` (includes local models, large)
  - `.\scripts\backup.ps1 -Zip` (creates zip archive)
- Restore:
  - Follow `docs/restore-checklist.md`

## Alerting

- Safety alerts are published to:
  - `home/automation/p304m/safety_alert`
- Switch audit events are published to:
  - `home/automation/p304m/switch_event`
- Home Assistant also creates:
  - persistent notification for safety alerts
  - best-effort `notify.notify` push (if notify service is configured)

## Codex Workflow (VS Code / CLI)

1. Open repo root: `home-lab/`
2. Ask Codex for a plan first.
3. Apply code/config changes.
4. Run checks:
   - `.\scripts\dev.ps1 ps`
   - `.\scripts\dev.ps1 health`
   - targeted `docker compose logs <service>`
