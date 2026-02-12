# Agent Service

## Structure

- `src/agent/main.py`: MQTT ingest and suggestion worker entrypoint
- `tests/test_topic_filter.py`: basic topic-selection tests
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

## Packaging Note

This service still uses `requirements.txt`. A future cleanup can replace it with `pyproject.toml`.
