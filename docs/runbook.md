# Runbook

## Start and Health

From repo root:

```powershell
.\scripts\dev.ps1 up
.\scripts\dev.ps1 ps
.\scripts\dev.ps1 health
.\scripts\dev.ps1 uptime
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
.\scripts\dev.ps1 ai-smoke
```

## Backup

```powershell
.\scripts\dev.ps1 backup
```

For always-on hosting, schedule:

1. `.\scripts\dev.ps1 uptime` every 5 minutes
2. `.\scripts\dev.ps1 backup` daily (off-peak)

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
- Node-RED AI Console: `http://localhost:1880/ai-console` (after importing flow)

## Grafana Dashboards

Provisioned dashboard:

- `AI Action Guardrails`

If missing, restart Grafana:

```powershell
.\scripts\dev.ps1 restart grafana
```

## AI Action Bridge

1. Set `HA_TOKEN` in `docker/.env` (Home Assistant long-lived token).
2. Restart agent:

```powershell
.\scripts\dev.ps1 restart agent
```

3. Publish command:

```powershell
docker exec mosquitto sh -lc "mosquitto_pub -h localhost -u '<MQTT_USER>' -P '<MQTT_PASSWORD>' -t home/ai/command -m 'turn off plug 2'"
docker exec mosquitto sh -lc "mosquitto_pub -h localhost -u '<MQTT_USER>' -P '<MQTT_PASSWORD>' -t home/ai/command -m 'turn on plug 3 and 4 and turn off plug 2'"
```

4. Read result:

```powershell
docker exec mosquitto sh -lc "mosquitto_sub -h localhost -u '<MQTT_USER>' -P '<MQTT_PASSWORD>' -t home/ai/action_result -C 1 -v"
```

Set mode:

```powershell
docker exec mosquitto sh -lc "mosquitto_pub -h localhost -u '<MQTT_USER>' -P '<MQTT_PASSWORD>' -t home/ai/mode/set -m ask"
docker exec mosquitto sh -lc "mosquitto_sub -h localhost -u '<MQTT_USER>' -P '<MQTT_PASSWORD>' -t home/ai/mode -C 1 -v"
```

Mode semantics:

- `suggest`: always rejects execution.
- `ask`: requires explicit confirmation.
- `auto`: executes if guardrails pass.

Default mode in this repo is `auto`.

## Node-RED Control Console

1. Open `http://localhost:1880`.
2. Import `nodered/flows/ai-control-console.json`.
3. Configure MQTT broker credentials in imported flow (`MQTT_USER` / `MQTT_PASSWORD` from `docker/.env`).
4. Deploy and open `http://localhost:1880/ai-console`.

## Voice Command (PC)

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\voice-bridge.ps1
```

Behavior:

- Press Enter to use microphone dictation (if available), or type command directly.
- Publishes commands to `home/ai/command` with source `voice`.
- Reads one response from `home/ai/action_result`.

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
