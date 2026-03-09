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

### Home Power Dashboard Query Fixes (P304M)

If `Outlet switch states` shows `No data`, it is usually because the panel time range is short and no switch toggles happened in that window.

Use these SQL queries in your custom dashboard panels.

`Outlet switch states (events table)`:

```sql
SELECT
  time,
  CASE
    WHEN topic = 'home/ha/switch/p304m_tapo_p304m_1/state' THEN 'Plug 1'
    WHEN topic = 'home/ha/switch/p304m_tapo_p304m_2/state' THEN 'Plug 2'
    WHEN topic = 'home/ha/switch/p304m_tapo_p304m_3/state' THEN 'Plug 3'
    WHEN topic = 'home/ha/switch/p304m_tapo_p304m_4/state' THEN 'Plug 4'
    ELSE topic
  END AS outlet,
  payload AS state
FROM mqtt_event
WHERE $__timeFilter(time)
  AND topic IN (
    'home/ha/switch/p304m_tapo_p304m_1/state',
    'home/ha/switch/p304m_tapo_p304m_2/state',
    'home/ha/switch/p304m_tapo_p304m_3/state',
    'home/ha/switch/p304m_tapo_p304m_4/state'
  )
ORDER BY time DESC
LIMIT 200
```

`Outlet current state (always shows latest row per plug)`:

```sql
WITH latest AS (
  SELECT topic, MAX(time) AS max_time
  FROM mqtt_event
  WHERE topic IN (
    'home/ha/switch/p304m_tapo_p304m_1/state',
    'home/ha/switch/p304m_tapo_p304m_2/state',
    'home/ha/switch/p304m_tapo_p304m_3/state',
    'home/ha/switch/p304m_tapo_p304m_4/state'
  )
  GROUP BY topic
)
SELECT
  CASE
    WHEN e.topic = 'home/ha/switch/p304m_tapo_p304m_1/state' THEN 'Plug 1'
    WHEN e.topic = 'home/ha/switch/p304m_tapo_p304m_2/state' THEN 'Plug 2'
    WHEN e.topic = 'home/ha/switch/p304m_tapo_p304m_3/state' THEN 'Plug 3'
    WHEN e.topic = 'home/ha/switch/p304m_tapo_p304m_4/state' THEN 'Plug 4'
    ELSE e.topic
  END AS outlet,
  e.payload AS state,
  e.time
FROM mqtt_event e
JOIN latest l ON e.topic = l.topic AND e.time = l.max_time
ORDER BY outlet
```

`Latest power readings (table)`:

```sql
SELECT
  time,
  CASE
    WHEN topic LIKE '%_p304m_1_current_consumption/state' THEN 'Plug 1'
    WHEN topic LIKE '%_p304m_2_current_consumption/state' THEN 'Plug 2'
    WHEN topic LIKE '%_p304m_3_current_consumption/state' THEN 'Plug 3'
    WHEN topic LIKE '%_p304m_4_current_consumption/state' THEN 'Plug 4'
    ELSE topic
  END AS outlet,
  CAST(payload AS DOUBLE) AS watts
FROM mqtt_event
WHERE $__timeFilter(time)
  AND topic LIKE 'home/ha/sensor/%current_consumption/state'
  AND topic LIKE '%p304m%'
ORDER BY time DESC
LIMIT 200
```

`Xiaomi fan on/off states (table)`:

```sql
SELECT
  time,
  topic,
  payload AS state
FROM mqtt_event
WHERE $__timeFilter(time)
  AND topic LIKE 'home/ha/fan/%/state'
ORDER BY time DESC
LIMIT 200
```

`Combined device states (plug + fan)`:

```sql
SELECT
  time,
  CASE
    WHEN topic = 'home/ha/switch/p304m_tapo_p304m_1/state' THEN 'Plug 1'
    WHEN topic = 'home/ha/switch/p304m_tapo_p304m_2/state' THEN 'Plug 2'
    WHEN topic = 'home/ha/switch/p304m_tapo_p304m_3/state' THEN 'Plug 3'
    WHEN topic = 'home/ha/switch/p304m_tapo_p304m_4/state' THEN 'Plug 4'
    WHEN topic LIKE 'home/ha/fan/%/state' THEN topic
    ELSE topic
  END AS device,
  payload AS state
FROM mqtt_event
WHERE $__timeFilter(time)
  AND (
    topic IN (
      'home/ha/switch/p304m_tapo_p304m_1/state',
      'home/ha/switch/p304m_tapo_p304m_2/state',
      'home/ha/switch/p304m_tapo_p304m_3/state',
      'home/ha/switch/p304m_tapo_p304m_4/state'
    )
    OR topic LIKE 'home/ha/fan/%/state'
  )
ORDER BY time DESC
LIMIT 300
```

## AI Action Bridge

1. Set `HA_TOKEN` in `docker/.env` (Home Assistant long-lived token).
2. Optional model routing in `docker/.env`:
   - `OLLAMA_MODEL=llama3.1:8b`
   - `ACTION_PARSE_OLLAMA_MODEL=` (optional override)
   - `SUGGESTION_OLLAMA_MODEL=` (optional override)
3. Restart agent:

```powershell
.\scripts\dev.ps1 restart agent
```

4. Publish command:

```powershell
docker exec mosquitto sh -lc "mosquitto_pub -h localhost -u '<MQTT_USER>' -P '<MQTT_PASSWORD>' -t home/ai/command -m 'turn off plug 2'"
docker exec mosquitto sh -lc "mosquitto_pub -h localhost -u '<MQTT_USER>' -P '<MQTT_PASSWORD>' -t home/ai/command -m 'turn on plug 3 and 4 and turn off plug 2'"
docker exec mosquitto sh -lc "mosquitto_pub -h localhost -u '<MQTT_USER>' -P '<MQTT_PASSWORD>' -t home/ai/command -m 'what can xiaomi fan do'"
docker exec mosquitto sh -lc "mosquitto_pub -h localhost -u '<MQTT_USER>' -P '<MQTT_PASSWORD>' -t home/ai/command -m 'what devices do you control'"
docker exec mosquitto sh -lc "mosquitto_pub -h localhost -u '<MQTT_USER>' -P '<MQTT_PASSWORD>' -t home/ai/command -m 'set xiaomi fan speed to 66'"
docker exec mosquitto sh -lc "mosquitto_pub -h localhost -u '<MQTT_USER>' -P '<MQTT_PASSWORD>' -t home/ai/command -m 'increase xiaomi fan speed'"
docker exec mosquitto sh -lc "mosquitto_pub -h localhost -u '<MQTT_USER>' -P '<MQTT_PASSWORD>' -t home/ai/command -m 'turn on xiaomi fan oscillation'"
docker exec mosquitto sh -lc "mosquitto_pub -h localhost -u '<MQTT_USER>' -P '<MQTT_PASSWORD>' -t home/ai/command -m 'set xiaomi fan to sleeping mode'"
```

5. Read result:

```powershell
docker exec mosquitto sh -lc "mosquitto_sub -h localhost -u '<MQTT_USER>' -P '<MQTT_PASSWORD>' -t home/ai/action_result -C 1 -v"
docker exec mosquitto sh -lc "mosquitto_sub -h localhost -u '<MQTT_USER>' -P '<MQTT_PASSWORD>' -t home/ai/device_suggestion -C 1 -v"
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

Approve/reject newly discovered devices:

```powershell
docker exec mosquitto sh -lc "mosquitto_pub -h localhost -u '<MQTT_USER>' -P '<MQTT_PASSWORD>' -t home/ai/command -m 'approve device fan.some_entity as living room fan'"
docker exec mosquitto sh -lc "mosquitto_pub -h localhost -u '<MQTT_USER>' -P '<MQTT_PASSWORD>' -t home/ai/command -m 'reject device fan.some_entity'"
```

Ignore helper/config entities in discovery suggestions (recommended for Xiaomi-style integrations):

- Default ignore pattern already skips entities like `physical_controls_locked`, `brightness`, `indicator`, `auto_off_enabled`.
- Config key in `docker/.env`: `ACTION_DEVICE_DISCOVERY_IGNORE_OBJECTID_REGEX`
- After changing this key, restart only agent:

```powershell
.\scripts\dev.ps1 restart agent
```

If old suggestions are still visible in Node-RED UI, use the `Clear` button in the `New Device Suggestions` section.

Capability query result shape:

- `action=capabilities`
- `capabilities=[{target, entity_id, domain, allowed, supported_actions}]`

Fan command examples:

- `set xiaomi fan speed to 33|66|100`
- `set xiaomi fan speed low|medium|high`
- `increase xiaomi fan speed`
- `decrease xiaomi fan speed`
- `can you turn the fan a bit` (natural phrase -> brief oscillation pulse, default 5s)
- `turn the fan down a bit` (natural phrase -> decrease speed)
- `turn on xiaomi fan oscillation`
- `turn off xiaomi fan oscillation`
- `set xiaomi fan to sleeping mode`
- `set xiaomi fan to direct breeze`

If fan alias commands are rejected with `no target device found`:

1. Verify alias persistence in `runtime/agent/dynamic_aliases.json`.
2. Restart agent: `.\scripts\dev.ps1 restart agent`.
3. Re-test with:
   - `turn off xiaomi fan`
   - `turn off xiao mi fan`
   - `what can xiaomi fan do`

Dynamic aliases are persisted at:

- `runtime/agent/dynamic_aliases.json`

## Node-RED Control Console

1. Open `http://localhost:1880`.
2. Import `nodered/flows/ai-control-console.json`.
3. Configure MQTT broker credentials in imported flow (`MQTT_USER` / `MQTT_PASSWORD` from `docker/.env`).
4. Deploy and open `http://localhost:1880/ai-console`.
5. In command input:
   - Press `Enter` to send (same as clicking `Send`).
   - Use capability query text such as `what can xiaomi fan do`.

## Voice Command (PC)

Install voice dependencies once:

```powershell
python -m venv .venv-voice
.\.venv-voice\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r .\scripts\requirements-voice.txt
```

Note: use Python 3.12+ for voice dependencies.

Voice config keys (`docker/.env`, optional; defaults are applied if missing):

- `VOICE_STT_ENGINE=whisper`
- `VOICE_WHISPER_MODEL=small`
- `VOICE_WHISPER_DEVICE=cpu`
- `VOICE_WHISPER_COMPUTE_TYPE=int8`
- `VOICE_LANGUAGES=en,ms,zh`
- `VOICE_TTS_ENABLED=true`
- `VOICE_PUSH_TO_TALK_TIMEOUT_SECONDS=5`
- `VOICE_RESULT_TIMEOUT_SECONDS=20`

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\voice-bridge.ps1
```

Behavior:

- Press Enter on empty prompt for push-to-talk.
- Uses local Whisper STT first (`scripts/voice-stt.py`), then Windows speech fallback.
- Publishes commands to `home/ai/command` with source `voice`.
- Payload includes `raw_command` and `lang` (`en|ms|zh`) metadata.
- In `ask` mode, `confirm=true` is sent only for explicit confirmation speech/text.
- Reads one response from `home/ai/action_result` and prints concise status line.
- Speaks concise response with TTS unless `-NoTts` or `VOICE_TTS_ENABLED=false`.

Optional flags:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\voice-bridge.ps1 -VerboseOutput
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\voice-bridge.ps1 -NoTts
```

Troubleshooting matrix:

| Symptom | Likely cause | Action |
| --- | --- | --- |
| `audio capture failed` | Mic device unavailable/blocked | Check Windows mic permission and default input device |
| `failed to load whisper model` | `faster-whisper` not installed or model download issue | `python -m pip install -r .\scripts\requirements-voice.txt` then retry |
| High command latency | First model load/cold start | Keep script running; use smaller model if needed |
| `No action_result received within timeout` | MQTT/agent path delay or service issue | Run `.\scripts\dev.ps1 health`, then inspect `docker logs --tail 40 homelab-agent` |

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
