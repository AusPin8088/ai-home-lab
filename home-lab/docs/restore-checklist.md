# Restore Checklist

Use this when restoring from a backup created by `scripts/backup.ps1`.

## 1) Stop the stack

From `home-lab/`:

```powershell
.\scripts\dev.ps1 down
```

## 2) Restore files from backup folder

Restore these paths into repo root:

- `ha/config`
- `mosquitto/config`
- `mosquitto/data`
- `nodered/data`
- `grafana/data`
- `influxdb3/data`
- `influxdb3/secrets`
- `docker.env` -> copy back to `docker/.env`
- `compose.yaml` -> copy back to `docker/compose.yaml` (optional if unchanged)

## 3) Start stack

```powershell
.\scripts\dev.ps1 up
```

## 4) Validate

```powershell
.\scripts\dev.ps1 health
.\scripts\dev.ps1 smoke
```

## Notes

- If host ports changed since backup, update `docker/compose.yaml` before `up`.
- If token/auth errors appear, verify `docker/.env` token matches `influxdb3/secrets/admin-token.json`.
