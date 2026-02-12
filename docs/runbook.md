# Runbook

## Start and Health

From repo root:

```powershell
.\scripts\dev.ps1 up
.\scripts\dev.ps1 ps
.\scripts\dev.ps1 health
```

## Stop and Restart

```powershell
.\scripts\dev.ps1 down
.\scripts\dev.ps1 restart
.\scripts\dev.ps1 restart agent
```

## Logs

```powershell
.\scripts\dev.ps1 logs
.\scripts\dev.ps1 logs influxdb
```

## Smoke Test

```powershell
.\scripts\dev.ps1 smoke
```

## Backup

```powershell
.\scripts\dev.ps1 backup
```

Additional options:

```powershell
.\scripts\backup.ps1 -Online
.\scripts\backup.ps1 -IncludeOllama
.\scripts\backup.ps1 -Zip
```

## Service URLs

- Home Assistant: `http://localhost:8123`
- Node-RED: `http://localhost:1880`
- InfluxDB: `http://localhost:8181`
- Grafana: `http://localhost:3000`
- Ollama API: `http://localhost:11434`

## Image Pins

| Service | Image Reference |
| --- | --- |
| homeassistant | `ghcr.io/home-assistant/home-assistant@sha256:17441c45ba14560b4ef727ee06aac4d605cf0dc0625fc4f2e043cb2551d72749` |
| mosquitto | `eclipse-mosquitto@sha256:9cfdd46ad59f3e3e5f592f6baf57ab23e1ad00605509d0f5c1e9b179c5314d87` |
| nodered | `nodered/node-red@sha256:7dfe40efdd7b9f21916f083802bfe60a762bc020969d95553ffa020c97a72eb9` |
| influxdb | `quay.io/influxdb/influxdb3-core@sha256:ad4ad468af9b2fbbe92523a5764217916cd1bdd43f578aef504da133ff3f0d0b` |
| grafana | `grafana/grafana@sha256:5683be4319a6da1d6ab28c3443b3739683e367f8d72d800638390a04a2680c1c` |
| ollama | `ollama/ollama@sha256:5f7a20da9b4d42d1909b4693f90942135bcabc335ee42d529c0d143c44a92311` |
| agent | Local build from `services/agent/Dockerfile` |

## Common Issues

- Influx unauthorized:
  - check `INFLUXDB_TOKEN` in `docker/.env`
  - verify it matches `runtime/influxdb3/secrets/admin-token.json`
- Grafana datasource missing:
  - restart Grafana with `./scripts/dev.ps1 restart grafana`
- MQTT auth failures:
  - ensure `MQTT_USER` and `MQTT_PASSWORD` match `mosquitto/config/passwordfile`
