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
- `docs/architecture.md`: system architecture and runtime layout
- `docs/runbook.md`: operations and troubleshooting
- `docs/backup-restore.md`: backup and restore workflow
- `runtime/`: container runtime/state volumes (gitignored)

## Start / Stop

From repo root:

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
