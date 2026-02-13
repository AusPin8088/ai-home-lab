# Architecture

## System Overview

AI Home Lab runs a local automation and observability stack:

- Home Assistant for automation logic and entity model.
- Mosquitto for MQTT messaging.
- Node-RED for flow-based integration.
- InfluxDB 3 Core for time-series storage.
- Grafana for dashboards.
- Ollama for local LLM inference.
- Python agent for MQTT ingest and suggestion generation.
- Guarded AI action bridge (agent consumes commands, enforces policies, calls HA API).

![Plan diagram](architecture/plan_diagram.png)

## Data Flow

1. Devices and Home Assistant publish MQTT topics to Mosquitto.
2. The `agent` subscribes to `home/#` and writes events to InfluxDB.
3. Actionable topics trigger local LLM suggestions via Ollama.
4. Command topic `home/ai/command` flows through guardrails (mode, rate-limit, cooldown, allowlist).
5. Agent executes HA service API when allowed and publishes `home/ai/action_result`.
6. Suggestions and action audits are written to InfluxDB.
7. Node-RED console (`/ai-console`) publishes commands and reads results/mode for interactive control.
8. Grafana reads InfluxDB for visualization.

## Directory Layout

- `docker/`: compose file, env template, and init scripts.
- `services/agent/`: Python ingest/suggestion service.
- `ha/`: Home Assistant config files.
- `mosquitto/config/`: Mosquitto static config.
- `grafana/provisioning/`: Grafana datasource provisioning.
- `grafana/dashboards/`: provisioned dashboard JSON files.
- `runtime/`: runtime/state volumes (not tracked in Git).
- `scripts/`: developer and operations scripts.
- `nodered/flows/`: importable Node-RED flows.
- `docs/`: architecture, runbook, backup/restore, and notes.

## Runtime Volume Map

- `runtime/grafana/data`
- `runtime/influxdb3/data`
- `runtime/influxdb3/secrets`
- `runtime/mosquitto/data`
- `runtime/mosquitto/log`
- `runtime/nodered/data`
- `runtime/ollama/data`
