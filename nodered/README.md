# Node-RED Flows

## AI Control Console

Import file:

- `nodered/flows/ai-control-console.json`

What it provides:

- `GET /ai-console`: browser control page (text command, quick plug buttons, mode switch, live status)
- `POST /ai-command`: enqueue AI command to `home/ai/command`
- `GET /ai-result`: latest `home/ai/action_result`
- `POST /ai-mode`: set mode via `home/ai/mode/set`
- `GET /ai-mode`: current mode from `home/ai/mode`

Import steps in Node-RED:

1. Open `http://localhost:1880`
2. Menu (top-right) -> `Import`
3. Select `nodered/flows/ai-control-console.json`
4. Click `Import` then `Deploy`
5. Open `http://localhost:1880/ai-console`

MQTT broker credentials:

- The flow uses broker host `mosquitto` on port `1883` (inside Docker network).
- In Node-RED, open the `Mosquitto` broker config and set username/password from `docker/.env`.
