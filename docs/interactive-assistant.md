# Interactive Assistant Guide

This guide maps the implemented interaction channels and autonomy behavior.

## 1) Text Commands (MQTT)

Command topic:

- `home/ai/command`

Examples:

- `turn off plug 2`
- `confirm turn on plug 2` (only needed in `ask` mode)
- `turn on plug 3 and 4 and turn off plug 2` (multi-action)

Result topic:

- `home/ai/action_result`
- `home/ai/device_suggestion` (new-device discovery suggestions)

## 2) Node-RED Console (Browser UI)

1. Import `nodered/flows/ai-control-console.json`.
2. Deploy.
3. Open `http://localhost:1880/ai-console`.

Features:

- text command box
- quick ON/OFF buttons for plug 1..4
- mode selector (`suggest`, `ask`, `auto`)
- live result/status panel
- capability query panel (example: `what can xiaomi fan do`)
- simple avatar/status bubble
- `Enter` key submits command from the text box
- command parsing/suggestion model is configurable via `OLLAMA_MODEL` (or split parse/suggestion vars)

## 3) PC Voice Bridge

Run:

```powershell
python -m venv .venv-voice
.\.venv-voice\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r .\scripts\requirements-voice.txt
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\voice-bridge.ps1
```

Use Python 3.12+ for voice dependencies.

Behavior:

- push-to-talk voice capture when pressing Enter on empty input
- local Whisper STT as primary (`VOICE_STT_ENGINE=whisper`)
- fallback to Windows speech recognizer if Whisper backend is unavailable
- deterministic normalization for EN/MS/ZH high-frequency home-control phrases
- typed fallback
- publishes with source `voice`
- publishes metadata `raw_command` and `lang` in command JSON
- reads result topic and provides concise text + optional TTS reply
- result wait timeout is configurable via `VOICE_RESULT_TIMEOUT_SECONDS` (default 20s)
- no wake-word/always-listening in this phase (push-to-talk only)

## 4) Autonomy Modes

Mode topics:

- state: `home/ai/mode`
- set: `home/ai/mode/set`

Modes:

- `suggest`: reject all execution requests
- `ask`: require explicit confirmation
- `auto`: execute valid allowlisted commands directly

## 5) Guardrails

- strict allowlist (`switch.p304m_tapo_p304m_1..4`)
- optional allowlisted aliases via `ACTION_EXTRA_ENTITY_MAP_JSON`
- rate limit (`ACTION_RATE_LIMIT_SECONDS`)
- flip cooldown (`ACTION_FLIP_COOLDOWN_SECONDS`)
- ambiguous prompt rejection unless confirmed
- source tagging in `iox.agent_action`
- new devices require explicit approval before becoming controllable

Fan actions supported after approval:

- `turn_on`, `turn_off`
- `set_percentage` (speed)
- `increase_speed`, `decrease_speed`
- `oscillate_on`, `oscillate_off`
- `set_preset_mode` (`Sleeping Mode`, `Direct Breeze`)

## 6) New Device Discovery

- Agent auto-detects new controllable HA entities from MQTT state topics.
- Suggestion events are published to `home/ai/device_suggestion`.
- Approve with:
  - `approve device fan.some_entity as living room fan`
- Reject with:
  - `reject device fan.some_entity`
- Approved aliases are persisted in `runtime/agent/dynamic_aliases.json`.

## 7) Validation

Run:

```powershell
.\scripts\dev.ps1 ai-smoke
```

Dashboard:

- Grafana -> `AI Action Guardrails`

Voice acceptance checklist:

- EN command executes (`turn off plug 2`)
- MS equivalent command executes after normalization
- ZH equivalent command executes after normalization
- ask mode without explicit confirm is rejected
- ask mode with explicit confirm executes
