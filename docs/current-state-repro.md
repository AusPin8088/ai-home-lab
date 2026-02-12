# Current State and Repro Guide

This file is the source of truth for what is currently running in this repo and how to reproduce/operate it.

## 1) Current stack (what exists now)

- Home Assistant (`homeassistant`)
- Mosquitto (`mosquitto`)
- Node-RED (`nodered`)
- InfluxDB 3 Core (`influxdb`)
- Grafana (`grafana`)
- Ollama (`ollama`)
- Python agent (`homelab-agent`)

Compose file:

- `docker/compose.yaml`

Environment/secrets:

- `docker/.env`
- `runtime/influxdb3/secrets/admin-token.json`
- `mosquitto/config/passwordfile`

Runtime data (persistent):

- `runtime/influxdb3/data`
- `runtime/grafana/data`
- `runtime/mosquitto/data`
- `runtime/mosquitto/log`
- `runtime/nodered/data`
- `runtime/ollama/data`

## 2) Key customizations already implemented

### Home Assistant

File: `ha/config/configuration.yaml`

- `mqtt_statestream` enabled
- `base_topic: home/ha`
- `publish_attributes: false`
- `publish_timestamps: false`
- includes `sensor`, `binary_sensor`, `switch`, `light`, `climate`
- includes switch glob `switch.*p304m*`

File: `ha/config/automations.yaml`

- `p304m_switch_audit_to_mqtt`
  - reads `home/ha/switch/+/state`
  - writes audit event to `home/automation/p304m/switch_event`
- `p304m_overheat_overload_alert`
  - reads `home/ha/binary_sensor/+/state`
  - writes safety event to `home/automation/p304m/safety_alert`
  - sends `notify.notify` (best effort)
  - creates HA persistent notification

### Agent

Files:

- `services/agent/Dockerfile`
- `services/agent/src/agent/main.py`

Behavior:

- subscribes to `home/#`
- writes MQTT events to Influx table `mqtt_event`
- generates suggestion rows in `agent_suggestion`
- suggestion generation is async (queue + worker), so ingestion stays stable

### Operations scripts

- `scripts/dev.ps1`: `up`, `down`, `ps`, `logs`, `restart`, `health`, `backup`, `smoke`
- `scripts/backup.ps1`: backup workflow (cold backup by default)
- `scripts/smoke.ps1`: end-to-end validation (can inject test events)

## 3) Daily operations (exact commands)

Run from repo root:

```powershell
cd "C:\Users\PC\Desktop\AI Home Lab"
```

Health:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\dev.ps1 health
```

Smoke test:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\dev.ps1 smoke
```

Backup:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\dev.ps1 backup
```

Start/stop:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\dev.ps1 up
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\dev.ps1 down
```

## 4) Reproduce on this machine (clean restart)

From repo root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\dev.ps1 down
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\dev.ps1 up
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\dev.ps1 health
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\dev.ps1 smoke
```

## 5) Reproduce from backup

Use `docs/backup-restore.md`.

Latest backup folder pattern:

- `backups/home-lab-backup-YYYYMMDD-HHMMSS`

After restore, validate:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\dev.ps1 health
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\dev.ps1 smoke
```

## 6) Ports and UIs

- HA: `http://localhost:8123`
- Node-RED: `http://localhost:1880`
- InfluxDB 3: `http://localhost:8181`
- Grafana: `http://localhost:3000`
- Ollama: `http://localhost:11434`

## 7) Expected smoke behavior

Smoke test checks these data paths:

- `home/ha/switch/p304m_tapo_p304m_2/state` exists in `mqtt_event`
- `home/automation/p304m/switch_event` exists in `mqtt_event`
- `home/automation/p304m/safety_alert` exists in `mqtt_event`

Note:

- Running smoke repeatedly increases counts because it injects test messages.

## 8) Common failure and fix

### PowerShell script blocked

Use:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

or always call with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\dev.ps1 health
```

### Container mount/path mismatch after older changes

- Run `docker compose up -d` from current `docker/compose.yaml` to reconcile containers.
- Backup resume already uses `up -d` (not `start`) to avoid stale mount reuse.

## 9) Security note

Secrets are currently in `docker/.env` and local files. Rotate if these values were shared externally:

- `INFLUXDB_TOKEN`
- `GRAFANA_ADMIN_PASSWORD`
- `MQTT_PASSWORD`
