# Backup and Restore

## Create Backup

From repo root:

```powershell
.\scripts\dev.ps1 backup
```

Optional flags:

```powershell
.\scripts\backup.ps1 -Online
.\scripts\backup.ps1 -IncludeOllama
.\scripts\backup.ps1 -Zip
```

Backup output defaults to `backups/home-lab-backup-<timestamp>`.

## Backup Contents

- `ha/config`
- `mosquitto/config`
- `runtime/mosquitto/data`
- `runtime/mosquitto/log`
- `runtime/nodered/data`
- `runtime/grafana/data`
- `runtime/influxdb3/data`
- `runtime/influxdb3/secrets`
- optional: `runtime/ollama/data`
- `docker.env` copy of `docker/.env`
- `compose.yaml` copy of `docker/compose.yaml`
- `runbook.md`
- `manifest.json`

## Restore Procedure

1. Stop stack:

```powershell
.\scripts\dev.ps1 down
```

2. Copy backup files into repo root preserving paths.

3. Restore env and compose files:
- `docker.env` -> `docker/.env`
- `compose.yaml` -> `docker/compose.yaml` (if desired)

4. Start stack:

```powershell
.\scripts\dev.ps1 up
```

5. Validate:

```powershell
.\scripts\dev.ps1 health
.\scripts\dev.ps1 smoke
```

## Notes

- Cold backup (default) is safer for consistency.
- Online backup may capture in-flight writes.
- Ollama models are large; include only when needed.
