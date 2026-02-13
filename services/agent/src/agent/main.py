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
VALID_ACTION_MODES = {"suggest", "ask", "auto"}
KNOWN_SOURCES = {"manual", "node_red", "voice", "api"}
SUPPORTED_DEVICE_DOMAINS = {"switch", "light", "fan", "input_boolean"}
ACTION_SEGMENT_RE = re.compile(
    r"\b(turn on|switch on|power on|turn off|switch off|power off|shut off)\b"
)


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


def parse_command_payload(raw_payload: str) -> tuple[str, str, bool, str]:
    payload = raw_payload.strip()
    source = "manual"
    confirm = False
    detail = "plain text payload"

    parsed: dict[str, Any] | None = None
    if payload.startswith("{") and payload.endswith("}"):
        try:
            maybe_json = json.loads(payload)
            if isinstance(maybe_json, dict):
                parsed = maybe_json
                detail = "json payload"
        except json.JSONDecodeError:
            parsed = None

    if parsed is not None:
        payload = str(parsed.get("command", "")).strip()
        src = str(parsed.get("source", "manual")).strip().lower()
        source = src if src in KNOWN_SOURCES else "manual"
        confirm_value = parsed.get("confirm", False)
        confirm = bool(confirm_value)

    lowered = payload.lower()
    if lowered.startswith("confirm "):
        confirm = True
        payload = payload[8:].strip()
    elif lowered.startswith("confirm:"):
        confirm = True
        payload = payload[8:].strip()

    return payload, source, confirm, detail


def _normalize_spaces(text: str) -> str:
    return " ".join(text.lower().split())


def _normalize_alias(alias: str) -> str:
    return " ".join(alias.strip().lower().split())


def parse_extra_entity_alias_map(raw_json: str) -> dict[str, str]:
    if not raw_json.strip():
        return {}
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}

    valid: dict[str, str] = {}
    for alias_raw, entity_raw in parsed.items():
        alias = _normalize_alias(str(alias_raw))
        entity_id = str(entity_raw).strip().lower()
        if not alias or "." not in entity_id:
            continue
        domain, object_id = entity_id.split(".", 1)
        if (
            domain in SUPPORTED_DEVICE_DOMAINS
            and re.fullmatch(r"[a-z0-9_]+", object_id) is not None
        ):
            valid[alias] = entity_id
    return valid


def extract_targets_from_text(
    text: str,
    extra_entity_alias_map: dict[str, str],
) -> list[dict[str, Any]]:
    normalized = _normalize_spaces(text)
    targets: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    outlet_matches = []
    outlet_matches.extend(re.findall(r"\b(?:plug|outlet)\s*([1-4])\b", normalized))
    outlet_matches.extend(re.findall(r"\bp304m[_\s-]*([1-4])\b", normalized))
    if re.search(r"\b(?:plug|outlet)\b", normalized):
        outlet_matches.extend(re.findall(r"\b([1-4])\b", normalized))

    for outlet_raw in outlet_matches:
        outlet = int(outlet_raw)
        key = ("outlet", str(outlet))
        if key in seen:
            continue
        seen.add(key)
        targets.append({"outlet": outlet, "entity_alias": ""})

    if extra_entity_alias_map:
        for alias in sorted(extra_entity_alias_map.keys(), key=len, reverse=True):
            pattern = rf"(?<![a-z0-9_]){re.escape(alias)}(?![a-z0-9_])"
            if re.search(pattern, normalized) is None:
                continue
            key = ("alias", alias)
            if key in seen:
                continue
            seen.add(key)
            targets.append({"outlet": 0, "entity_alias": alias})

    return targets


def parse_direct_action_plan(
    text: str,
    extra_entity_alias_map: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    alias_map = extra_entity_alias_map or {}
    normalized = _normalize_spaces(text)
    if not normalized:
        return [], "empty command"

    if re.search(r"\bon\s*(?:and|/)\s*off\b|\boff\s*(?:and|/)\s*on\b", normalized):
        return [], "ambiguous action (both on/off found)"

    def append_steps(
        plan: list[dict[str, Any]],
        action: str,
        targets: list[dict[str, Any]],
        seen_keys: set[tuple[str, str, str]],
    ) -> None:
        for target in targets:
            outlet = int(target.get("outlet", 0))
            alias = str(target.get("entity_alias", "")).strip().lower()
            target_key = f"outlet:{outlet}" if outlet else f"alias:{alias}"
            dedupe_key = (action, target_key, normalized)
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            plan.append(
                {
                    "action": action,
                    "outlet": outlet,
                    "entity_alias": alias,
                }
            )

    plan: list[dict[str, Any]] = []
    seen_plan_keys: set[tuple[str, str, str]] = set()

    matches = list(ACTION_SEGMENT_RE.finditer(normalized))
    if matches:
        for idx, match in enumerate(matches):
            phrase = match.group(1)
            action = "turn_on" if "on" in phrase else "turn_off"
            seg_start = match.end()
            seg_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(normalized)
            segment = normalized[seg_start:seg_end]
            targets = extract_targets_from_text(segment, alias_map)
            if not targets and idx == 0:
                # Also try full text for cases where target appears before conjunction boundaries.
                targets = extract_targets_from_text(normalized, alias_map)
            append_steps(plan, action, targets, seen_plan_keys)
        if plan:
            detail = (
                "parsed by deterministic rules"
                if len(plan) == 1
                else "parsed by deterministic multi-step rules"
            )
            return plan, detail

    has_on = re.search(r"\bon\b", normalized) is not None
    has_off = re.search(r"\boff\b", normalized) is not None
    if has_on and has_off:
        return [], "ambiguous action (both on/off found)"

    action = "turn_on" if has_on and not has_off else "turn_off" if has_off else ""
    targets = extract_targets_from_text(normalized, alias_map)
    if not targets:
        return [], "no target device found"
    if not action:
        first_target = targets[0]
        outlet = int(first_target.get("outlet", 0))
        if outlet:
            return [], "no on/off intent found"
        return [], "no on/off intent found for target alias"
    append_steps(plan, action, targets, seen_plan_keys)
    if not plan:
        return [], "no actionable target found"
    detail = (
        "parsed by deterministic rules"
        if len(plan) == 1
        else "parsed by deterministic multi-step rules"
    )
    return plan, detail


def parse_direct_action_command(text: str) -> tuple[str | None, int | None, str]:
    normalized = _normalize_spaces(text)
    if not normalized:
        return None, None, "empty command"

    if re.search(r"\bon\s*(?:and|/)\s*off\b|\boff\s*(?:and|/)\s*on\b", normalized):
        return None, None, "ambiguous action (both on/off found)"

    has_on = re.search(r"\bon\b", normalized) is not None
    has_off = re.search(r"\boff\b", normalized) is not None
    if has_on and has_off:
        return None, None, "ambiguous action (both on/off found)"

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


def parse_ollama_action_plan(
    ollama_url: str,
    timeout: int,
    text: str,
    extra_entity_alias_map: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    alias_map = extra_entity_alias_map or {}
    alias_instructions = ""
    if alias_map:
        aliases = ", ".join(sorted(alias_map.keys()))
        alias_instructions = (
            "You may also use entity_alias for non-plug devices. "
            f"Allowed entity_alias values: {aliases}. "
        )

    payload = {
        "model": "llama3.1:8b",
        "prompt": (
            "Convert this home automation command into JSON only. "
            "Return exactly one object with keys: steps, reason. "
            "steps must be an array of objects with action plus one target: outlet or entity_alias. "
            "action must be 'turn_on' or 'turn_off'. "
            "outlet must be integer 1,2,3,4 when provided. "
            f"{alias_instructions}"
            "If unsupported, return {\"steps\":[],\"reason\":\"...\"}.\n\n"
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
        return [], f"ollama parse request failed: {exc}"

    parsed = _extract_json_object(response.json().get("response", ""))
    if not parsed:
        return [], "ollama response did not contain valid JSON"

    raw_steps = parsed.get("steps")
    if not isinstance(raw_steps, list):
        # Backward compatibility: single-action shape.
        raw_steps = [parsed]

    steps: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw_step in raw_steps:
        if not isinstance(raw_step, dict):
            continue
        action_raw = str(raw_step.get("action", "")).strip().lower()
        if action_raw in {"turn_on", "on", "switch.turn_on"}:
            action = "turn_on"
        elif action_raw in {"turn_off", "off", "switch.turn_off"}:
            action = "turn_off"
        else:
            continue

        outlet = 0
        entity_alias = ""
        outlet_raw = raw_step.get("outlet")
        if outlet_raw is not None:
            try:
                outlet = int(outlet_raw)
            except (TypeError, ValueError):
                outlet = 0
        alias_raw = str(raw_step.get("entity_alias", "")).strip().lower()
        if alias_raw:
            entity_alias = _normalize_alias(alias_raw)

        if outlet not in {0, 1, 2, 3, 4}:
            continue
        if outlet == 0:
            if not entity_alias:
                continue
            if entity_alias not in alias_map:
                continue

        target_key = f"outlet:{outlet}" if outlet else f"alias:{entity_alias}"
        dedupe = (action, target_key)
        if dedupe in seen:
            continue
        seen.add(dedupe)
        steps.append({"action": action, "outlet": outlet, "entity_alias": entity_alias})

    if not steps:
        return [], str(parsed.get("reason", "ollama returned no actionable steps"))
    detail = (
        "parsed by ollama JSON"
        if len(steps) == 1
        else "parsed by ollama multi-step JSON"
    )
    return steps, detail


def parse_ollama_action_command(
    ollama_url: str,
    timeout: int,
    text: str,
) -> tuple[str | None, int | None, str]:
    steps, detail = parse_ollama_action_plan(
        ollama_url=ollama_url,
        timeout=timeout,
        text=text,
        extra_entity_alias_map={},
    )
    if not steps:
        return None, None, detail
    first = steps[0]
    outlet = int(first.get("outlet", 0))
    if outlet not in {1, 2, 3, 4}:
        return None, None, "ollama JSON missing valid outlet 1-4"
    return str(first.get("action", "")), outlet, detail


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

    if "." not in entity_id:
        return False, f"invalid entity_id: {entity_id}"
    domain, _ = entity_id.split(".", 1)
    if domain not in SUPPORTED_DEVICE_DOMAINS:
        return False, f"unsupported entity domain: {domain}"

    url = f"{ha_url.rstrip('/')}/api/services/{domain}/{service}"
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
        return True, f"home assistant service {domain}.{service} ok"
    return False, f"home assistant service {domain}.{service} failed {response.status_code}: {response.text[:200]}"


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
    suggestion_http_timeout = int(os.getenv("SUGGESTION_HTTP_TIMEOUT", "120"))

    action_bridge_enabled = os.getenv("ACTION_BRIDGE_ENABLED", "false").lower() == "true"
    action_parse_with_ollama = os.getenv("ACTION_PARSE_WITH_OLLAMA", "true").lower() == "true"
    action_command_topic = os.getenv("ACTION_COMMAND_TOPIC", "home/ai/command")
    action_result_topic = os.getenv("ACTION_RESULT_TOPIC", "home/ai/action_result")
    action_mode_topic = os.getenv("ACTION_MODE_TOPIC", "home/ai/mode")
    action_mode_set_topic = os.getenv("ACTION_MODE_SET_TOPIC", "home/ai/mode/set")
    action_mode_default = os.getenv("ACTION_MODE_DEFAULT", "auto").lower()
    action_queue_max = int(os.getenv("ACTION_QUEUE_MAX", "100"))
    action_http_timeout = int(os.getenv("ACTION_HTTP_TIMEOUT", "20"))
    action_parse_timeout = int(
        os.getenv("ACTION_PARSE_TIMEOUT", str(suggestion_http_timeout))
    )
    action_rate_limit_seconds = float(os.getenv("ACTION_RATE_LIMIT_SECONDS", "2"))
    action_flip_cooldown_seconds = float(os.getenv("ACTION_FLIP_COOLDOWN_SECONDS", "3"))

    ha_url = os.getenv("HA_URL", "http://homeassistant:8123")
    ha_token = os.getenv("HA_TOKEN", "")
    outlet_entity_map = {
        1: os.getenv("ACTION_ENTITY_PLUG_1", "switch.p304m_tapo_p304m_1"),
        2: os.getenv("ACTION_ENTITY_PLUG_2", "switch.p304m_tapo_p304m_2"),
        3: os.getenv("ACTION_ENTITY_PLUG_3", "switch.p304m_tapo_p304m_3"),
        4: os.getenv("ACTION_ENTITY_PLUG_4", "switch.p304m_tapo_p304m_4"),
    }
    extra_entity_alias_map = parse_extra_entity_alias_map(
        os.getenv("ACTION_EXTRA_ENTITY_MAP_JSON", "")
    )
    allowed_entity_ids = {v for v in outlet_entity_map.values() if v}
    allowed_entity_ids.update(extra_entity_alias_map.values())

    if not influx_token:
        raise RuntimeError("INFLUX_TOKEN is required for authenticated InfluxDB access.")

    current_mode = action_mode_default if action_mode_default in VALID_ACTION_MODES else "auto"

    influx = InfluxDBClient(url=influx_url, token=influx_token, org=influx_org)
    write_api = influx.write_api(write_options=SYNCHRONOUS)
    suggestion_queue: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=suggestion_queue_max)
    action_queue: queue.Queue[tuple[str, str, str, float]] = queue.Queue(maxsize=action_queue_max)

    if action_bridge_enabled and not ha_token:
        print(
            "ACTION_BRIDGE_ENABLED=true but HA_TOKEN is empty; action bridge will reject commands",
            flush=True,
        )

    def write_action_audit(
        *,
        status: str,
        action: str,
        command: str,
        detail: str,
        source: str,
        entity_id: str,
        mode: str,
    ) -> None:
        try:
            action_point = (
                Point("agent_action")
                .tag("status", status)
                .tag("action", action or "none")
                .tag("entity_id", entity_id or "none")
                .tag("source", source or "manual")
                .tag("mode", mode)
                .field("command", command[:5000])
                .field("detail", detail[:5000])
                .time(time.time_ns(), WritePrecision.NS)
            )
            write_api.write(bucket=influx_bucket, record=action_point)
        except Exception as exc:
            print("influx write agent_action failed:", exc, flush=True)

    def publish_mode(client: mqtt.Client, mode: str, source: str, detail: str) -> None:
        payload = json.dumps(
            {
                "mode": mode,
                "source": source,
                "detail": detail,
                "time": time.time(),
            },
            ensure_ascii=True,
        )
        result = client.publish(action_mode_topic, payload, qos=0, retain=True)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            print("failed to publish mode rc=", result.rc, flush=True)

    def on_connect(client, userdata, flags, rc, properties=None):
        print("MQTT connected rc=", rc, flush=True)
        subscribe_result, _ = client.subscribe(TOPIC)
        print("MQTT subscribe rc=", subscribe_result, "topic=", TOPIC, flush=True)
        if action_bridge_enabled:
            publish_mode(
                client=client,
                mode=current_mode,
                source="agent_boot",
                detail="published current mode on connect",
            )

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
                action_queue.put_nowait(("command", payload, "mqtt", time.time()))
            except queue.Full:
                print("action queue full; dropping command payload", flush=True)

        if action_bridge_enabled and msg.topic == action_mode_set_topic:
            try:
                action_queue.put_nowait(("mode_set", payload, "mqtt", time.time()))
            except queue.Full:
                print("action queue full; dropping mode payload", flush=True)

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
        nonlocal current_mode

        last_command_ts = 0.0
        last_entity_action: dict[str, tuple[str, float]] = {}

        while True:
            item_type, raw_payload, inbound_source, received_ts = action_queue.get()

            if item_type == "mode_set":
                requested = raw_payload.strip().lower()
                if requested not in VALID_ACTION_MODES:
                    detail = f"invalid mode '{requested}', expected suggest|ask|auto"
                    write_action_audit(
                        status="rejected",
                        action="set_mode",
                        command=raw_payload,
                        detail=detail,
                        source=inbound_source,
                        entity_id="none",
                        mode=current_mode,
                    )
                else:
                    current_mode = requested
                    detail = f"mode set to {current_mode}"
                    publish_mode(client=client, mode=current_mode, source=inbound_source, detail=detail)
                    write_action_audit(
                        status="mode_change",
                        action="set_mode",
                        command=raw_payload,
                        detail=detail,
                        source=inbound_source,
                        entity_id="none",
                        mode=current_mode,
                    )
                action_queue.task_done()
                continue

            command_text = ""
            source = "manual"
            confirm = False
            payload_detail = ""
            status = "rejected"
            detail = ""
            action = ""
            outlet = 0
            entity_id = ""
            planned_steps: list[dict[str, Any]] = []
            executed_steps: list[dict[str, Any]] = []

            try:
                command_text, source, confirm, payload_detail = parse_command_payload(raw_payload)
                now = received_ts

                if (now - last_command_ts) < action_rate_limit_seconds:
                    status = "rejected"
                    detail = (
                        f"rate limited: wait at least {action_rate_limit_seconds:.1f}s between commands"
                    )
                elif current_mode == "suggest":
                    status = "rejected"
                    detail = "mode=suggest: action execution disabled"
                else:
                    planned_steps, detail = parse_direct_action_plan(
                        command_text,
                        extra_entity_alias_map=extra_entity_alias_map,
                    )
                    if not planned_steps and action_parse_with_ollama:
                        parsed_steps, parsed_detail = parse_ollama_action_plan(
                            ollama_url=ollama_url,
                            timeout=action_parse_timeout,
                            text=command_text,
                            extra_entity_alias_map=extra_entity_alias_map,
                        )
                        if parsed_steps:
                            planned_steps = parsed_steps
                            detail = (
                                f"{detail}; {parsed_detail}" if detail else parsed_detail
                            )
                        elif parsed_detail:
                            detail = f"{detail}; {parsed_detail}" if detail else parsed_detail

                    if not planned_steps:
                        status = "rejected"
                    elif current_mode == "ask" and not confirm:
                        status = "rejected"
                        detail = (
                            f"mode=ask requires confirmation; planned {len(planned_steps)} step(s); "
                            f"resend: confirm {command_text}"
                        )
                    else:
                        step_summaries: list[str] = []
                        executed_count = 0
                        failed_count = 0
                        rejected_count = 0

                        for idx, step in enumerate(planned_steps, start=1):
                            step_action = str(step.get("action", ""))
                            step_outlet = int(step.get("outlet", 0))
                            step_alias = str(step.get("entity_alias", "")).strip().lower()
                            step_entity_id = (
                                outlet_entity_map.get(step_outlet, "")
                                if step_outlet in {1, 2, 3, 4}
                                else extra_entity_alias_map.get(step_alias, "")
                            )

                            if not step_entity_id:
                                step_status = "rejected"
                                step_detail = (
                                    f"step {idx}: unresolved target "
                                    f"(outlet={step_outlet}, alias='{step_alias}')"
                                )
                            elif step_entity_id not in allowed_entity_ids:
                                step_status = "rejected"
                                step_detail = f"step {idx}: entity {step_entity_id} not in allowlist"
                            elif not ha_token:
                                step_status = "rejected"
                                step_detail = f"step {idx}: HA_TOKEN is empty"
                            else:
                                prev = last_entity_action.get(step_entity_id)
                                if (
                                    prev
                                    and prev[0] != step_action
                                    and (now - prev[1]) < action_flip_cooldown_seconds
                                ):
                                    step_status = "rejected"
                                    step_detail = (
                                        f"step {idx}: cooldown active for {step_entity_id}; "
                                        f"wait {action_flip_cooldown_seconds:.1f}s before flip"
                                    )
                                else:
                                    ok, exec_detail = execute_home_assistant_action(
                                        ha_url=ha_url,
                                        ha_token=ha_token,
                                        action=step_action,
                                        entity_id=step_entity_id,
                                        timeout=action_http_timeout,
                                    )
                                    step_status = "executed" if ok else "failed"
                                    step_detail = f"step {idx}: {exec_detail}"
                                    if step_status == "executed":
                                        last_entity_action[step_entity_id] = (step_action, now)

                            if step_status == "executed":
                                executed_count += 1
                            elif step_status == "failed":
                                failed_count += 1
                            else:
                                rejected_count += 1

                            step_label = (
                                f"plug {step_outlet}"
                                if step_outlet in {1, 2, 3, 4}
                                else (step_alias or step_entity_id or "unknown")
                            )
                            step_summaries.append(
                                f"{idx}/{len(planned_steps)} {step_action} {step_label}: {step_status}"
                            )
                            executed_steps.append(
                                {
                                    "index": idx,
                                    "action": step_action,
                                    "outlet": step_outlet,
                                    "entity_alias": step_alias,
                                    "entity_id": step_entity_id or "none",
                                    "status": step_status,
                                    "detail": step_detail,
                                }
                            )

                        if executed_count == len(planned_steps):
                            status = "executed"
                        elif executed_count > 0 or failed_count > 0:
                            status = "failed"
                        else:
                            status = "rejected"

                        if planned_steps:
                            first = planned_steps[0]
                            first_outlet = int(first.get("outlet", 0))
                            first_alias = str(first.get("entity_alias", "")).strip().lower()
                            first_entity = (
                                outlet_entity_map.get(first_outlet, "")
                                if first_outlet in {1, 2, 3, 4}
                                else extra_entity_alias_map.get(first_alias, "")
                            )
                            if len(planned_steps) == 1:
                                action = str(first.get("action", ""))
                                outlet = first_outlet
                                entity_id = first_entity or "none"
                            else:
                                action = "multi"
                                outlet = 0
                                entity_id = "multiple"

                        detail = (
                            f"{payload_detail}; {detail}; "
                            f"executed={executed_count}/{len(planned_steps)}; "
                            f"failed={failed_count}; rejected={rejected_count}; "
                            + " | ".join(step_summaries)
                        )
                        if status == "executed":
                            last_command_ts = now
            except Exception as exc:
                status = "failed"
                detail = f"action worker exception: {exc}"

            result = {
                "status": status,
                "command": command_text or raw_payload,
                "action": action or "none",
                "outlet": outlet,
                "entity_id": entity_id or "none",
                "source": source,
                "mode": current_mode,
                "detail": detail,
                "steps": executed_steps,
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

            write_action_audit(
                status=status,
                action=action or "none",
                command=command_text or raw_payload,
                detail=detail,
                source=source,
                entity_id=entity_id or "none",
                mode=current_mode,
            )
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
