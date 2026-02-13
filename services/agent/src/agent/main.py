import json
import os
import queue
import re
import threading
import time
from typing import Any

import paho.mqtt.client as mqtt
import requests
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

TOPIC = "home/#"


def is_actionable_topic(topic: str) -> bool:
    topic_lower = topic.lower()
    is_actionable_state = topic_lower.endswith("/state") and (
        "/switch/" in topic_lower
        or "/light/" in topic_lower
        or "/climate/" in topic_lower
        or "/binary_sensor/" in topic_lower
    )
    is_power_topic = "power" in topic_lower
    return is_actionable_state or is_power_topic


def parse_direct_action_command(text: str) -> tuple[str | None, int | None, str]:
    normalized = " ".join(text.lower().split())

    on_match = any(
        re.search(pattern, normalized)
        for pattern in (r"\bturn on\b", r"\bswitch on\b", r"\bpower on\b")
    )
    off_match = any(
        re.search(pattern, normalized)
        for pattern in (
            r"\bturn off\b",
            r"\bswitch off\b",
            r"\bpower off\b",
            r"\bshut off\b",
        )
    )

    action: str | None = None
    if on_match and off_match:
        return None, None, "ambiguous action (both on/off found)"
    if on_match:
        action = "turn_on"
    if off_match:
        action = "turn_off"

    if action is None:
        has_on = re.search(r"\bon\b", normalized) is not None
        has_off = re.search(r"\boff\b", normalized) is not None
        if has_on and not has_off:
            action = "turn_on"
        elif has_off and not has_on:
            action = "turn_off"

    outlet_match = (
        re.search(r"\b(?:plug|outlet)\s*([1-4])\b", normalized)
        or re.search(r"\bp304m[_\s-]*([1-4])\b", normalized)
    )
    if not outlet_match:
        return None, None, "no plug number 1-4 found"

    outlet = int(outlet_match.group(1))
    if action is None:
        return None, outlet, "no on/off intent found"
    return action, outlet, "parsed by deterministic rules"


def _extract_json_object(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def parse_ollama_action_command(
    ollama_url: str,
    timeout: int,
    text: str,
) -> tuple[str | None, int | None, str]:
    payload = {
        "model": "llama3.1:8b",
        "prompt": (
            "Convert this home automation command into JSON only. "
            "Return exactly one object with keys: action, outlet, reason. "
            "action must be 'turn_on' or 'turn_off'. "
            "outlet must be integer 1,2,3,4. "
            "If unsupported, return {\"action\":\"reject\",\"reason\":\"...\"}.\n\n"
            f"Command: {text}\n\nJSON:"
        ),
        "stream": False,
    }

    try:
        response = requests.post(
            f"{ollama_url}/api/generate",
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
    except Exception as exc:
        return None, None, f"ollama parse request failed: {exc}"

    parsed = _extract_json_object(response.json().get("response", ""))
    if not parsed:
        return None, None, "ollama response did not contain valid JSON"

    action_raw = str(parsed.get("action", "")).strip().lower()
    if action_raw in {"turn_on", "on", "switch.turn_on"}:
        action = "turn_on"
    elif action_raw in {"turn_off", "off", "switch.turn_off"}:
        action = "turn_off"
    else:
        return None, None, str(parsed.get("reason", "unsupported action"))

    outlet_raw = parsed.get("outlet")
    try:
        outlet = int(outlet_raw)
    except (TypeError, ValueError):
        return None, None, "ollama JSON missing valid outlet 1-4"

    if outlet not in {1, 2, 3, 4}:
        return None, None, "ollama outlet must be 1-4"

    return action, outlet, "parsed by ollama JSON"


def execute_home_assistant_action(
    ha_url: str,
    ha_token: str,
    action: str,
    entity_id: str,
    timeout: int,
) -> tuple[bool, str]:
    if action == "turn_on":
        service = "turn_on"
    elif action == "turn_off":
        service = "turn_off"
    else:
        return False, f"unsupported action: {action}"

    url = f"{ha_url.rstrip('/')}/api/services/switch/{service}"
    headers = {
        "Authorization": f"Bearer {ha_token}",
        "Content-Type": "application/json",
    }
    body = {"entity_id": entity_id}
    try:
        response = requests.post(url, headers=headers, json=body, timeout=timeout)
    except Exception as exc:
        return False, f"home assistant request failed: {exc}"

    if 200 <= response.status_code < 300:
        return True, f"home assistant service switch.{service} ok"
    return False, f"home assistant service failed {response.status_code}: {response.text[:200]}"


def ollama_suggest(ollama_url: str, timeout: int, text: str) -> str:
    payload = {
        "model": "llama3.1:8b",
        "prompt": (
            "You are a cautious home automation analyst. "
            "Given the event/log below, propose ONE safe automation suggestion. "
            "Do not assume you can execute changes. Keep it short.\n\n"
            f"Event: {text}\n\nSuggestion:"
        ),
        "stream": False,
    }
    try:
        response = requests.post(
            f"{ollama_url}/api/generate",
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json().get("response", "").strip()
    except Exception as exc:
        return f"(ollama error: {exc})"


def main() -> None:
    mqtt_host = os.getenv("MQTT_HOST", "mosquitto")
    mqtt_port = int(os.getenv("MQTT_PORT", "1883"))
    mqtt_user = os.getenv("MQTT_USER", "")
    mqtt_password = os.getenv("MQTT_PASSWORD", "")
    mqtt_keepalive = int(os.getenv("MQTT_KEEPALIVE", "120"))

    ollama_url = os.getenv("OLLAMA_URL", "http://ollama:11434")
    influx_url = os.getenv("INFLUX_URL", "http://influxdb:8181")
    influx_token = os.getenv("INFLUX_TOKEN", "")
    influx_org = os.getenv("INFLUX_ORG", "homelab")
    influx_bucket = os.getenv("INFLUX_BUCKET", "home")

    suggestion_queue_max = int(os.getenv("SUGGESTION_QUEUE_MAX", "1000"))
    suggestion_http_timeout = int(os.getenv("SUGGESTION_HTTP_TIMEOUT", "20"))

    action_bridge_enabled = os.getenv("ACTION_BRIDGE_ENABLED", "false").lower() == "true"
    action_parse_with_ollama = os.getenv("ACTION_PARSE_WITH_OLLAMA", "true").lower() == "true"
    action_command_topic = os.getenv("ACTION_COMMAND_TOPIC", "home/ai/command")
    action_result_topic = os.getenv("ACTION_RESULT_TOPIC", "home/ai/action_result")
    action_queue_max = int(os.getenv("ACTION_QUEUE_MAX", "100"))
    action_http_timeout = int(os.getenv("ACTION_HTTP_TIMEOUT", "20"))
    action_parse_timeout = int(
        os.getenv("ACTION_PARSE_TIMEOUT", str(suggestion_http_timeout))
    )

    ha_url = os.getenv("HA_URL", "http://homeassistant:8123")
    ha_token = os.getenv("HA_TOKEN", "")
    outlet_entity_map = {
        1: os.getenv("ACTION_ENTITY_PLUG_1", "switch.p304m_tapo_p304m_1"),
        2: os.getenv("ACTION_ENTITY_PLUG_2", "switch.p304m_tapo_p304m_2"),
        3: os.getenv("ACTION_ENTITY_PLUG_3", "switch.p304m_tapo_p304m_3"),
        4: os.getenv("ACTION_ENTITY_PLUG_4", "switch.p304m_tapo_p304m_4"),
    }

    if not influx_token:
        raise RuntimeError("INFLUX_TOKEN is required for authenticated InfluxDB access.")

    influx = InfluxDBClient(url=influx_url, token=influx_token, org=influx_org)
    write_api = influx.write_api(write_options=SYNCHRONOUS)
    suggestion_queue: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=suggestion_queue_max)
    action_queue: queue.Queue[str] = queue.Queue(maxsize=action_queue_max)

    if action_bridge_enabled and not ha_token:
        print(
            "ACTION_BRIDGE_ENABLED=true but HA_TOKEN is empty; action bridge will reject commands",
            flush=True,
        )

    def on_connect(client, userdata, flags, rc, properties=None):
        print("MQTT connected rc=", rc, flush=True)
        subscribe_result, _ = client.subscribe(TOPIC)
        print("MQTT subscribe rc=", subscribe_result, "topic=", TOPIC, flush=True)

    def on_disconnect(client, userdata, disconnect_flags, reason_code, properties=None):
        print("MQTT disconnected reason=", reason_code, flush=True)

    def on_message(client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", errors="replace")
        except Exception:
            payload = str(msg.payload)

        try:
            point = (
                Point("mqtt_event")
                .tag("topic", msg.topic)
                .field("payload", payload[:5000])
                .time(time.time_ns(), WritePrecision.NS)
            )
            write_api.write(bucket=influx_bucket, record=point)
        except Exception as exc:
            print("influx write mqtt_event failed:", exc, flush=True)

        if action_bridge_enabled and msg.topic == action_command_topic:
            try:
                action_queue.put_nowait(payload)
            except queue.Full:
                print("action queue full; dropping command payload", flush=True)

        if is_actionable_topic(msg.topic):
            try:
                suggestion_queue.put_nowait((msg.topic, payload))
            except queue.Full:
                print("suggestion queue full; dropping topic=", msg.topic, flush=True)

    def suggestion_worker() -> None:
        while True:
            topic, payload = suggestion_queue.get()
            try:
                suggestion = ollama_suggest(
                    ollama_url=ollama_url,
                    timeout=suggestion_http_timeout,
                    text=f"topic={topic} payload={payload}",
                )
                suggestion_point = (
                    Point("agent_suggestion")
                    .tag("topic", topic)
                    .field("suggestion", suggestion[:5000])
                    .time(time.time_ns(), WritePrecision.NS)
                )
                write_api.write(bucket=influx_bucket, record=suggestion_point)
            except Exception as exc:
                print("influx write agent_suggestion failed:", exc, flush=True)
            finally:
                suggestion_queue.task_done()

    def action_worker() -> None:
        while True:
            command_text = action_queue.get()
            status = "rejected"
            detail = ""
            action = ""
            outlet = 0
            entity_id = ""

            try:
                action, outlet, detail = parse_direct_action_command(command_text)
                if (not action or outlet not in {1, 2, 3, 4}) and action_parse_with_ollama:
                    action, outlet, detail = parse_ollama_action_command(
                        ollama_url=ollama_url,
                        timeout=action_parse_timeout,
                        text=command_text,
                    )

                if not action or outlet not in {1, 2, 3, 4}:
                    status = "rejected"
                else:
                    entity_id = outlet_entity_map.get(outlet, "")
                    if not entity_id:
                        status = "rejected"
                        detail = f"outlet {outlet} has no mapped entity_id"
                    elif not ha_token:
                        status = "rejected"
                        detail = "HA_TOKEN is empty"
                    else:
                        ok, exec_detail = execute_home_assistant_action(
                            ha_url=ha_url,
                            ha_token=ha_token,
                            action=action,
                            entity_id=entity_id,
                            timeout=action_http_timeout,
                        )
                        status = "executed" if ok else "failed"
                        detail = f"{detail}; {exec_detail}" if detail else exec_detail
            except Exception as exc:
                status = "failed"
                detail = f"action worker exception: {exc}"

            result = {
                "status": status,
                "command": command_text,
                "action": action or "none",
                "outlet": outlet,
                "entity_id": entity_id or "none",
                "detail": detail,
                "time": time.time(),
            }
            try:
                publish_result = client.publish(
                    action_result_topic,
                    json.dumps(result, ensure_ascii=True),
                    qos=0,
                    retain=False,
                )
                if publish_result.rc != mqtt.MQTT_ERR_SUCCESS:
                    print("failed to publish action result rc=", publish_result.rc, flush=True)
            except Exception as exc:
                print("action result publish failed:", exc, flush=True)

            try:
                action_point = (
                    Point("agent_action")
                    .tag("status", status)
                    .tag("action", action or "none")
                    .tag("entity_id", entity_id or "none")
                    .field("command", command_text[:5000])
                    .field("detail", detail[:5000])
                    .time(time.time_ns(), WritePrecision.NS)
                )
                write_api.write(bucket=influx_bucket, record=action_point)
            except Exception as exc:
                print("influx write agent_action failed:", exc, flush=True)
            finally:
                action_queue.task_done()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if mqtt_user:
        client.username_pw_set(mqtt_user, mqtt_password)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    threading.Thread(target=suggestion_worker, daemon=True).start()
    if action_bridge_enabled:
        threading.Thread(target=action_worker, daemon=True).start()

    client.connect(mqtt_host, mqtt_port, mqtt_keepalive)
    client.loop_forever()


if __name__ == "__main__":
    main()
