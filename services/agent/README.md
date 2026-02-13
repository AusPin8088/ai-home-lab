# Agent Service

## Structure

- `src/agent/main.py`: MQTT ingest and suggestion worker entrypoint
- `tests/test_topic_filter.py`: basic topic-selection tests
- `tests/test_action_parser.py`: action command parsing tests
- `requirements.txt`: runtime dependencies
- `Dockerfile`: container build and start command

## Run Locally

```powershell
python -m pip install -r requirements.txt
$env:PYTHONPATH = "$PWD/src"
python -m agent.main
```

## Tests

```powershell
$env:PYTHONPATH = "$PWD/src"
python -m unittest discover -s tests -p "test_*.py"
```

## Action Bridge

When `ACTION_BRIDGE_ENABLED=true`, the agent accepts natural language commands on
`home/ai/command`, resolves `turn on/off plug 1..4`, and calls Home Assistant service API.

Required environment variables:

- `HA_URL` (default `http://homeassistant:8123`)
- `HA_TOKEN` (Home Assistant long-lived access token)

Result topic:

- `home/ai/action_result`

## Packaging Note

This service still uses `requirements.txt`. A future cleanup can replace it with `pyproject.toml`.
