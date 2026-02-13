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

## 2) Node-RED Console (Browser UI)

1. Import `nodered/flows/ai-control-console.json`.
2. Deploy.
3. Open `http://localhost:1880/ai-console`.

Features:

- text command box
- quick ON/OFF buttons for plug 1..4
- mode selector (`suggest`, `ask`, `auto`)
- live result/status panel
- simple avatar/status bubble

## 3) PC Voice Bridge

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\voice-bridge.ps1
```

Behavior:

- microphone dictation when available (press Enter)
- typed fallback
- publishes with source `voice`
- reads result topic and optional TTS reply

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

## 6) Validation

Run:

```powershell
.\scripts\dev.ps1 ai-smoke
```

Dashboard:

- Grafana -> `AI Action Guardrails`
