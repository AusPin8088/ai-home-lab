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
DEFAULT_DEVICE_DISCOVERY_IGNORE_OBJECTID_REGEX = (
    r"(auto_off_enabled|auto_update_enabled|led|brightness(?:_p_\d+_\d+)?|"
    r"physical_controls_locked(?:_p_\d+_\d+)?|indicator(?:_light)?|child_lock|"
    r"buzzer|beep|display|screen|volume)$"
)
ACTION_SEGMENT_RE = re.compile(
    r"\b(turn on|switch on|power on|turn off|switch off|power off|shut off)\b"
)
CAPABILITY_QUERY_RE = re.compile(
    r"\b(what can|what does|what commands|available commands|supported commands|capabilities|capability)\b"
)
DEVICE_LIST_QUERY_RE = re.compile(
    r"\b(list|show|what)\s+(?:all\s+)?(?:devices|device)\b|\bwhat can you control\b|\bwhat do you control\b"
)
DISCOVERABLE_STATE_TOPIC_RE = re.compile(
    r"^home/ha/(switch|light|fan|input_boolean)/([a-z0-9_]+)/state$"
)
DEVICE_APPROVE_RE = re.compile(
    r"^\s*(approve|allow)\s+(?:device\s+)?([a-z_]+\.[a-z0-9_]+)(?:\s+as\s+(.+))?\s*$"
)
DEVICE_REJECT_RE = re.compile(
    r"^\s*(reject|deny|ignore)\s+(?:device\s+)?([a-z_]+\.[a-z0-9_]+)\s*$"
)
NOISY_ALIAS_TOKENS = {"tapo", "p304m", "dmaker", "sg", "cn", "us", "de", "ru", "i2"}


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


def _compact_alnum(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


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


def merge_entity_alias_maps(
    static_alias_map: dict[str, str],
    dynamic_alias_map: dict[str, str],
) -> dict[str, str]:
    merged = dict(static_alias_map)
    for alias, entity_id in dynamic_alias_map.items():
        if alias not in merged:
            merged[alias] = entity_id
    return merged


def parse_discoverable_entity_from_topic(topic: str) -> tuple[str, str] | None:
    match = DISCOVERABLE_STATE_TOPIC_RE.match(topic.lower())
    if not match:
        return None
    return match.group(1), match.group(2)


def suggest_alias_from_entity_id(entity_id: str) -> str:
    if "." not in entity_id:
        return "new device"
    domain, object_id = entity_id.split(".", 1)
    tokens = []
    for raw in object_id.split("_"):
        token = raw.strip().lower()
        if not token:
            continue
        if token in NOISY_ALIAS_TOKENS:
            continue
        if re.fullmatch(r"\d+", token):
            continue
        if re.fullmatch(r"[0-9a-f]{6,}", token):
            continue
        if len(token) == 1 and token not in {"a", "b", "c"}:
            continue
        tokens.append(token)
    if not tokens:
        tokens = [domain, "device"]
    alias = " ".join(tokens[-3:])
    return _normalize_alias(alias)


def load_dynamic_entity_alias_map(file_path: str) -> dict[str, str]:
    if not file_path:
        return {}
    try:
        with open(file_path, "r", encoding="utf-8") as handle:
            parsed = json.load(handle)
    except FileNotFoundError:
        return {}
    except Exception:
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


def save_dynamic_entity_alias_map(file_path: str, alias_map: dict[str, str]) -> None:
    if not file_path:
        return
    folder = os.path.dirname(file_path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as handle:
        json.dump(alias_map, handle, ensure_ascii=True, indent=2, sort_keys=True)


def parse_device_management_payload(raw_payload: str) -> tuple[str | None, str, str, str]:
    payload = raw_payload.strip()
    if not (payload.startswith("{") and payload.endswith("}")):
        return None, "", "", ""
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return None, "", "", ""
    if not isinstance(parsed, dict):
        return None, "", "", ""

    action = str(parsed.get("device_action", "")).strip().lower()
    entity_id = str(parsed.get("entity_id", "")).strip().lower()
    alias = _normalize_alias(str(parsed.get("alias", "")))
    if action in {"approve", "allow"}:
        return "approve", entity_id, alias, "parsed device approval from JSON payload"
    if action in {"reject", "deny", "ignore"}:
        return "reject", entity_id, "", "parsed device rejection from JSON payload"
    return None, "", "", ""


def parse_device_management_command(text: str) -> tuple[str | None, str, str, str]:
    normalized = _normalize_spaces(text)
    if not normalized:
        return None, "", "", ""

    approve_match = DEVICE_APPROVE_RE.match(normalized)
    if approve_match:
        entity_id = approve_match.group(2).strip().lower()
        alias_raw = approve_match.group(3) or ""
        alias = _normalize_alias(alias_raw)
        return "approve", entity_id, alias, "parsed device approval command"

    reject_match = DEVICE_REJECT_RE.match(normalized)
    if reject_match:
        entity_id = reject_match.group(2).strip().lower()
        return "reject", entity_id, "", "parsed device rejection command"

    return None, "", "", ""


def is_valid_entity_id(entity_id: str) -> bool:
    if "." not in entity_id:
        return False
    domain, object_id = entity_id.split(".", 1)
    return (
        domain in SUPPORTED_DEVICE_DOMAINS
        and re.fullmatch(r"[a-z0-9_]+", object_id) is not None
    )


def extract_targets_from_text(
    text: str,
    extra_entity_alias_map: dict[str, str],
) -> list[dict[str, Any]]:
    normalized = _normalize_spaces(text)
    normalized_compact = _compact_alnum(normalized)
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
            alias_matched = re.search(pattern, normalized) is not None
            if not alias_matched:
                # Allow spacing variants, e.g. "xiaomi fan" <-> "xiao mi fan".
                alias_compact = _compact_alnum(alias)
                alias_matched = len(alias_compact) >= 6 and alias_compact in normalized_compact
            if not alias_matched:
                continue
            key = ("alias", alias)
            if key in seen:
                continue
            seen.add(key)
            targets.append({"outlet": 0, "entity_alias": alias})

    return targets


def parse_capability_query(
    text: str,
    extra_entity_alias_map: dict[str, str],
) -> tuple[bool, list[dict[str, Any]], str]:
    normalized = _normalize_spaces(text)
    if not normalized:
        return False, [], ""
    # If the user already asked for explicit on/off, this is an action command.
    if ACTION_SEGMENT_RE.search(normalized):
        return False, [], ""

    is_capability_query = (
        CAPABILITY_QUERY_RE.search(normalized) is not None
        or normalized.startswith("help ")
        or normalized == "help"
    )
    is_device_list_query = DEVICE_LIST_QUERY_RE.search(normalized) is not None
    if is_device_list_query:
        return True, [], "device inventory query"
    if not is_capability_query:
        return False, [], ""

    targets = extract_targets_from_text(normalized, extra_entity_alias_map)
    if targets:
        return True, targets, "capabilities query with explicit target(s)"
    return True, [], "capabilities query (no explicit target)"


def capabilities_for_domain(domain: str) -> list[str]:
    # Keep this aligned with what the action bridge actually executes.
    if domain == "fan":
        return [
            "turn_on",
            "turn_off",
            "set_percentage",
            "increase_speed",
            "decrease_speed",
            "oscillate_on",
            "oscillate_off",
            "oscillate_brief",
            "set_preset_mode",
        ]
    if domain in SUPPORTED_DEVICE_DOMAINS:
        return ["turn_on", "turn_off"]
    return []


def parse_direct_action_plan(
    text: str,
    extra_entity_alias_map: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    alias_map = extra_entity_alias_map or {}
    normalized = _normalize_spaces(text)
    if not normalized:
        return [], "empty command"

    def append_steps(
        plan: list[dict[str, Any]],
        action: str,
        targets: list[dict[str, Any]],
        seen_keys: set[tuple[str, str, str, str]],
        extra_fields: dict[str, Any] | None = None,
    ) -> None:
        extras = extra_fields or {}
        extras_sig = json.dumps(extras, sort_keys=True, ensure_ascii=True)
        for target in targets:
            outlet = int(target.get("outlet", 0))
            alias = str(target.get("entity_alias", "")).strip().lower()
            target_key = f"outlet:{outlet}" if outlet else f"alias:{alias}"
            dedupe_key = (action, target_key, extras_sig, normalized)
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            step = {
                "action": action,
                "outlet": outlet,
                "entity_alias": alias,
            }
            step.update(extras)
            plan.append(step)

    plan: list[dict[str, Any]] = []
    seen_plan_keys: set[tuple[str, str, str, str]] = set()

    # Fan-specific controls (speed/oscillation/preset) should be parsed before generic on/off.
    fan_targets = extract_targets_from_text(normalized, alias_map)
    fan_alias_targets = sorted(
        [alias for alias, eid in alias_map.items() if str(eid).startswith("fan.")],
        key=len,
        reverse=True,
    )
    fan_context = any(
        key in normalized
        for key in (
            "fan",
            "speed",
            "oscillat",
            "swing",
            "left right",
            "left-right",
            "left and right",
            "breeze",
            "sleep mode",
            "sleeping mode",
            "preset",
        )
    )

    if fan_context:
        # If user says "the fan" and we only have one approved fan alias, auto-target it.
        if (
            not fan_targets
            and len(fan_alias_targets) == 1
            and re.search(r"\b(?:the\s+)?fan\b", normalized)
        ):
            fan_targets = [{"outlet": 0, "entity_alias": fan_alias_targets[0]}]

        if not fan_targets:
            return [], "no target device found"

        speed_percent_match = (
            re.search(
                r"\b(?:set|change|adjust)\b.*\bspeed\b.*?\bto\b\s*(\d{1,3})\s*%?\b",
                normalized,
            )
            or re.search(r"\bspeed\b.*?\bto\b\s*(\d{1,3})\s*%?\b", normalized)
            or re.search(r"\b(\d{1,3})\s*%?\s*speed\b", normalized)
        )
        if speed_percent_match is not None:
            percentage = int(speed_percent_match.group(1))
            percentage = max(1, min(100, percentage))
            append_steps(
                plan,
                "set_percentage",
                fan_targets,
                seen_plan_keys,
                {"percentage": percentage},
            )
            return plan, "parsed fan speed percentage"

        speed_word_map = {"low": 33, "medium": 66, "mid": 66, "high": 100}
        speed_word = ""
        if "speed" in normalized:
            for candidate in speed_word_map:
                if re.search(rf"\b{candidate}\b", normalized):
                    speed_word = candidate
                    break
        if speed_word:
            append_steps(
                plan,
                "set_percentage",
                fan_targets,
                seen_plan_keys,
                {"percentage": speed_word_map[speed_word]},
            )
            return plan, f"parsed fan speed preset '{speed_word}'"

        if (
            re.search(r"\b(increase|raise|higher|faster|speed up)\b", normalized)
            and "speed" in normalized
        ):
            append_steps(plan, "increase_speed", fan_targets, seen_plan_keys)
            return plan, "parsed fan increase speed"

        if (
            re.search(r"\b(decrease|lower|slower|slow down)\b", normalized)
            and "speed" in normalized
        ):
            append_steps(plan, "decrease_speed", fan_targets, seen_plan_keys)
            return plan, "parsed fan decrease speed"

        # Natural phrasing support:
        # - "turn the fan a bit" => oscillation (left/right)
        # - "turn the fan down a bit" => speed down
        turn_fan_up = re.search(
            r"\b(turn|set|make)\b.*\bfan\b.*\b(up|higher|faster|more)\b",
            normalized,
        )
        turn_fan_down = re.search(
            r"\b(turn|set|make)\b.*\bfan\b.*\b(down|lower|slower|less)\b",
            normalized,
        )
        small_adjust = re.search(r"\b(a bit|a little|slightly)\b", normalized)
        down_hint = re.search(r"\b(down|lower|slower|less|decrease)\b", normalized)
        up_hint = re.search(r"\b(up|higher|faster|more|increase)\b", normalized)

        if turn_fan_down or (small_adjust and down_hint):
            append_steps(plan, "decrease_speed", fan_targets, seen_plan_keys)
            return plan, "parsed natural fan decrease speed"

        if turn_fan_up or (small_adjust and up_hint):
            append_steps(plan, "increase_speed", fan_targets, seen_plan_keys)
            return plan, "parsed natural fan increase speed"

        # "turn the fan a bit" usually means oscillate (left/right), not speed.
        turn_fan_plain = re.search(r"\b(turn|set|make)\b.*\bfan\b", normalized)
        if (
            turn_fan_plain
            and small_adjust
            and not up_hint
            and not down_hint
            and "speed" not in normalized
        ):
            pulse_seconds = 5
            pulse_match = re.search(
                r"\bfor\s+(\d{1,2})(?:\s*(?:s|sec|secs|second|seconds))?\b",
                normalized,
            )
            if pulse_match is not None:
                pulse_seconds = max(1, min(30, int(pulse_match.group(1))))
            append_steps(
                plan,
                "oscillate_brief",
                fan_targets,
                seen_plan_keys,
                {"oscillating": True, "pulse_seconds": pulse_seconds},
            )
            return plan, "parsed natural fan brief oscillation"

        oscillation_mentioned = re.search(
            r"\b(oscillat(?:e|ion)?|swing|left[-\s]*(?:and\s*)?right)\b",
            normalized,
        )
        if oscillation_mentioned:
            if re.search(r"\b(off|stop|disable)\b", normalized):
                append_steps(
                    plan,
                    "oscillate_off",
                    fan_targets,
                    seen_plan_keys,
                    {"oscillating": False},
                )
                return plan, "parsed fan oscillation off"
            if re.search(r"\b(on|start|enable)\b", normalized):
                append_steps(
                    plan,
                    "oscillate_on",
                    fan_targets,
                    seen_plan_keys,
                    {"oscillating": True},
                )
                return plan, "parsed fan oscillation on"
            return [], "no on/off intent found for oscillation"

        if re.search(r"\b(sleep(?:ing)? mode)\b", normalized):
            append_steps(
                plan,
                "set_preset_mode",
                fan_targets,
                seen_plan_keys,
                {"preset_mode": "Sleeping Mode"},
            )
            return plan, "parsed fan preset mode 'Sleeping Mode'"

        if re.search(r"\b(direct breeze|normal mode)\b", normalized):
            append_steps(
                plan,
                "set_preset_mode",
                fan_targets,
                seen_plan_keys,
                {"preset_mode": "Direct Breeze"},
            )
            return plan, "parsed fan preset mode 'Direct Breeze'"

    if re.search(r"\bon\s*(?:and|/)\s*off\b|\boff\s*(?:and|/)\s*on\b", normalized):
        return [], "ambiguous action (both on/off found)"

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
    step: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    if "." not in entity_id:
        return False, f"invalid entity_id: {entity_id}"
    domain, _ = entity_id.split(".", 1)
    if domain not in SUPPORTED_DEVICE_DOMAINS:
        return False, f"unsupported entity domain: {domain}"

    def call_service(
        call_service_domain: str,
        call_service: str,
        call_body: dict[str, Any],
    ) -> tuple[bool, str]:
        url = f"{ha_url.rstrip('/')}/api/services/{call_service_domain}/{call_service}"
        headers = {
            "Authorization": f"Bearer {ha_token}",
            "Content-Type": "application/json",
        }
        try:
            response = requests.post(url, headers=headers, json=call_body, timeout=timeout)
        except Exception as exc:
            return False, f"home assistant request failed: {exc}"

        if 200 <= response.status_code < 300:
            return True, f"home assistant service {call_service_domain}.{call_service} ok"
        return (
            False,
            f"home assistant service {call_service_domain}.{call_service} failed {response.status_code}: {response.text[:200]}",
        )

    body: dict[str, Any] = {"entity_id": entity_id}
    service_domain = domain
    service = ""

    if action == "turn_on":
        service = "turn_on"
    elif action == "turn_off":
        service = "turn_off"
    elif action == "set_percentage":
        if domain != "fan":
            return False, f"unsupported action {action} for domain {domain}"
        service_domain = "fan"
        service = "set_percentage"
        raw_percentage = 0
        if step is not None:
            try:
                raw_percentage = int(step.get("percentage", 0))
            except (TypeError, ValueError):
                raw_percentage = 0
        if raw_percentage <= 0:
            return False, "set_percentage requires percentage 1-100"
        body["percentage"] = max(1, min(100, raw_percentage))
    elif action in {"increase_speed", "decrease_speed"}:
        if domain != "fan":
            return False, f"unsupported action {action} for domain {domain}"
        service_domain = "fan"
        service = action
    elif action in {"oscillate_on", "oscillate_off"}:
        if domain != "fan":
            return False, f"unsupported action {action} for domain {domain}"
        service_domain = "fan"
        service = "oscillate"
        body["oscillating"] = action == "oscillate_on"
    elif action == "oscillate_brief":
        if domain != "fan":
            return False, f"unsupported action {action} for domain {domain}"
        pulse_seconds = 5.0
        if step is not None:
            try:
                pulse_seconds = float(step.get("pulse_seconds", 5))
            except (TypeError, ValueError):
                pulse_seconds = 5.0
        pulse_seconds = max(1.0, min(30.0, pulse_seconds))

        ok_on, detail_on = call_service(
            "fan",
            "oscillate",
            {"entity_id": entity_id, "oscillating": True},
        )
        if not ok_on:
            return False, f"brief oscillation start failed: {detail_on}"

        time.sleep(pulse_seconds)

        ok_off, detail_off = call_service(
            "fan",
            "oscillate",
            {"entity_id": entity_id, "oscillating": False},
        )
        if not ok_off:
            return False, (
                f"brief oscillation stop failed after {pulse_seconds:.1f}s: {detail_off}"
            )
        return True, f"home assistant service fan.oscillate pulse ok ({pulse_seconds:.1f}s)"
    elif action == "set_preset_mode":
        if domain != "fan":
            return False, f"unsupported action {action} for domain {domain}"
        service_domain = "fan"
        service = "set_preset_mode"
        preset_mode = ""
        if step is not None:
            preset_mode = str(step.get("preset_mode", "")).strip()
        if not preset_mode:
            return False, "set_preset_mode requires preset_mode"
        body["preset_mode"] = preset_mode
    else:
        return False, f"unsupported action: {action}"

    return call_service(service_domain, service, body)


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
    action_device_suggestion_topic = os.getenv(
        "ACTION_DEVICE_SUGGESTION_TOPIC",
        "home/ai/device_suggestion",
    )
    action_mode_topic = os.getenv("ACTION_MODE_TOPIC", "home/ai/mode")
    action_mode_set_topic = os.getenv("ACTION_MODE_SET_TOPIC", "home/ai/mode/set")
    action_mode_default = os.getenv("ACTION_MODE_DEFAULT", "auto").lower()
    action_queue_max = int(os.getenv("ACTION_QUEUE_MAX", "100"))
    action_device_discovery_enabled = (
        os.getenv("ACTION_DEVICE_DISCOVERY_ENABLED", "true").lower() == "true"
    )
    action_device_discovery_cooldown_seconds = float(
        os.getenv("ACTION_DEVICE_DISCOVERY_COOLDOWN_SECONDS", "600")
    )
    action_device_discovery_ignore_regex = os.getenv(
        "ACTION_DEVICE_DISCOVERY_IGNORE_OBJECTID_REGEX",
        DEFAULT_DEVICE_DISCOVERY_IGNORE_OBJECTID_REGEX,
    )
    action_dynamic_alias_store_path = os.getenv(
        "ACTION_DYNAMIC_ALIAS_STORE_PATH",
        "/app/runtime/dynamic_aliases.json",
    )
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
    dynamic_entity_alias_map = load_dynamic_entity_alias_map(action_dynamic_alias_store_path)
    allowed_entity_ids = {v for v in outlet_entity_map.values() if v}
    allowed_entity_ids.update(extra_entity_alias_map.values())
    allowed_entity_ids.update(dynamic_entity_alias_map.values())
    state_lock = threading.Lock()
    pending_device_suggestions: dict[str, dict[str, Any]] = {}
    discovery_last_published_at: dict[str, float] = {}
    try:
        discovery_ignore_pattern = re.compile(action_device_discovery_ignore_regex)
    except re.error:
        discovery_ignore_pattern = re.compile(DEFAULT_DEVICE_DISCOVERY_IGNORE_OBJECTID_REGEX)

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

    def get_current_alias_map() -> dict[str, str]:
        with state_lock:
            return merge_entity_alias_maps(extra_entity_alias_map, dynamic_entity_alias_map)

    def publish_action_result_payload(payload: dict[str, Any]) -> None:
        try:
            publish_result = client.publish(
                action_result_topic,
                json.dumps(payload, ensure_ascii=True),
                qos=0,
                retain=False,
            )
            if publish_result.rc != mqtt.MQTT_ERR_SUCCESS:
                print("failed to publish action result rc=", publish_result.rc, flush=True)
        except Exception as exc:
            print("action result publish failed:", exc, flush=True)

    def publish_device_suggestion(
        *,
        entity_id: str,
        domain: str,
        topic: str,
        payload: str,
        suggested_alias: str,
    ) -> None:
        suggestion = {
            "status": "suggested",
            "event": "device_suggestion",
            "command": f"discover {entity_id}",
            "action": "suggest_device",
            "entity_id": entity_id,
            "domain": domain,
            "topic": topic,
            "state": payload[:500],
            "suggested_alias": suggested_alias,
            "approve_example": f"approve device {entity_id} as {suggested_alias}",
            "reject_example": f"reject device {entity_id}",
            "source": "agent",
            "mode": current_mode,
            "detail": (
                "new controllable device discovered; "
                f"approve with: approve device {entity_id} as {suggested_alias}"
            ),
            "time": time.time(),
        }
        try:
            suggestion_result = client.publish(
                action_device_suggestion_topic,
                json.dumps(suggestion, ensure_ascii=True),
                qos=0,
                retain=False,
            )
            if suggestion_result.rc != mqtt.MQTT_ERR_SUCCESS:
                print("failed to publish device suggestion rc=", suggestion_result.rc, flush=True)
        except Exception as exc:
            print("device suggestion publish failed:", exc, flush=True)
        publish_action_result_payload(suggestion)

        write_action_audit(
            status="suggested",
            action="suggest_device",
            command=f"discover {entity_id}",
            detail=f"suggested_alias={suggested_alias}; topic={topic}",
            source="agent",
            entity_id=entity_id,
            mode=current_mode,
        )

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

        if action_bridge_enabled and action_device_discovery_enabled:
            discovered = parse_discoverable_entity_from_topic(msg.topic)
            if discovered is not None:
                domain, object_id = discovered
                if discovery_ignore_pattern.search(object_id) is not None:
                    return
                entity_id = f"{domain}.{object_id}"
                now = time.time()
                suggestion_alias = ""
                should_publish = False
                with state_lock:
                    alias_map = merge_entity_alias_maps(
                        extra_entity_alias_map,
                        dynamic_entity_alias_map,
                    )
                    if entity_id in allowed_entity_ids:
                        should_publish = False
                    elif entity_id in pending_device_suggestions:
                        pending_device_suggestions[entity_id]["last_seen"] = now
                        should_publish = False
                    else:
                        last_published = discovery_last_published_at.get(entity_id, 0.0)
                        if (now - last_published) >= action_device_discovery_cooldown_seconds:
                            suggestion_alias = suggest_alias_from_entity_id(entity_id)
                            if (
                                suggestion_alias in alias_map
                                and alias_map[suggestion_alias] != entity_id
                            ):
                                suggestion_alias = f"{suggestion_alias} {domain}"
                            pending_device_suggestions[entity_id] = {
                                "entity_id": entity_id,
                                "domain": domain,
                                "topic": msg.topic,
                                "suggested_alias": suggestion_alias,
                                "first_seen": now,
                                "last_seen": now,
                            }
                            discovery_last_published_at[entity_id] = now
                            should_publish = True
                if should_publish:
                    publish_device_suggestion(
                        entity_id=entity_id,
                        domain=domain,
                        topic=msg.topic,
                        payload=payload,
                        suggested_alias=suggestion_alias,
                    )

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
            capability_rows: list[dict[str, Any]] = []

            try:
                command_text, source, confirm, payload_detail = parse_command_payload(raw_payload)
                now = received_ts
                device_action, device_entity_id, device_alias, device_detail = (
                    parse_device_management_payload(raw_payload)
                )
                if device_action is None:
                    device_action, device_entity_id, device_alias, device_detail = (
                        parse_device_management_command(command_text)
                    )

                if device_action is not None:
                    action = f"{device_action}_device"
                    entity_id = device_entity_id or "none"
                    if not is_valid_entity_id(device_entity_id):
                        status = "rejected"
                        detail = f"{device_detail}; invalid entity_id '{device_entity_id}'"
                    elif device_action == "approve":
                        alias_to_use = _normalize_alias(device_alias)
                        save_required = False
                        save_error = ""
                        with state_lock:
                            alias_map = merge_entity_alias_maps(
                                extra_entity_alias_map,
                                dynamic_entity_alias_map,
                            )
                            if not alias_to_use:
                                pending_alias = (
                                    pending_device_suggestions.get(device_entity_id, {}).get(
                                        "suggested_alias",
                                        "",
                                    )
                                )
                                alias_to_use = _normalize_alias(
                                    pending_alias or suggest_alias_from_entity_id(device_entity_id)
                                )
                            if not alias_to_use:
                                status = "rejected"
                                detail = f"{device_detail}; could not infer alias for {device_entity_id}"
                            elif (
                                alias_to_use in extra_entity_alias_map
                                and extra_entity_alias_map[alias_to_use] != device_entity_id
                            ):
                                status = "rejected"
                                detail = (
                                    f"{device_detail}; alias '{alias_to_use}' is reserved by static config"
                                )
                            elif (
                                alias_to_use in alias_map
                                and alias_map[alias_to_use] != device_entity_id
                            ):
                                status = "rejected"
                                detail = (
                                    f"{device_detail}; alias '{alias_to_use}' already maps to {alias_map[alias_to_use]}"
                                )
                            else:
                                dynamic_entity_alias_map[alias_to_use] = device_entity_id
                                allowed_entity_ids.add(device_entity_id)
                                pending_device_suggestions.pop(device_entity_id, None)
                                discovery_last_published_at[device_entity_id] = now
                                save_required = True
                                status = "executed"
                                detail = (
                                    f"{device_detail}; approved {device_entity_id} as alias '{alias_to_use}'"
                                )
                        if save_required:
                            try:
                                save_dynamic_entity_alias_map(
                                    action_dynamic_alias_store_path,
                                    dynamic_entity_alias_map,
                                )
                            except Exception as exc:
                                status = "failed"
                                save_error = str(exc)
                                detail = f"{detail}; failed to persist aliases: {save_error}"
                    else:
                        with state_lock:
                            removed = pending_device_suggestions.pop(device_entity_id, None)
                            discovery_last_published_at[device_entity_id] = now
                        status = "executed"
                        if removed is None:
                            detail = (
                                f"{device_detail}; no pending suggestion for {device_entity_id}, cooldown updated"
                            )
                        else:
                            detail = f"{device_detail}; rejected suggestion for {device_entity_id}"

                else:
                    current_alias_map = get_current_alias_map()
                    is_cap_query, cap_targets, cap_detail = parse_capability_query(
                        command_text,
                        current_alias_map,
                    )
                    if is_cap_query:
                        action = "capabilities"
                        status = "executed"
                        detail_parts: list[str] = [payload_detail, cap_detail]
                        if cap_targets:
                            for target in cap_targets:
                                target_outlet = int(target.get("outlet", 0))
                                target_alias = str(target.get("entity_alias", "")).strip().lower()
                                target_entity = (
                                    outlet_entity_map.get(target_outlet, "")
                                    if target_outlet in {1, 2, 3, 4}
                                    else current_alias_map.get(target_alias, "")
                                )
                                if not target_entity:
                                    capability_rows.append(
                                        {
                                            "target": (
                                                f"plug {target_outlet}"
                                                if target_outlet in {1, 2, 3, 4}
                                                else target_alias
                                            ),
                                            "status": "unknown",
                                            "detail": "target not resolved",
                                        }
                                    )
                                    continue
                                domain = target_entity.split(".", 1)[0]
                                capability_rows.append(
                                    {
                                        "target": (
                                            f"plug {target_outlet}"
                                            if target_outlet in {1, 2, 3, 4}
                                            else target_alias
                                        ),
                                        "entity_id": target_entity,
                                        "domain": domain,
                                        "allowed": target_entity in allowed_entity_ids,
                                        "supported_actions": capabilities_for_domain(domain),
                                    }
                                )
                            detail_parts.append(
                                f"found {len(capability_rows)} target(s); see capabilities field"
                            )
                            first = capability_rows[0]
                            entity_id = str(first.get("entity_id", "none"))
                            outlet = int(cap_targets[0].get("outlet", 0))
                        else:
                            seen_entities: set[str] = set()
                            for plug_outlet, plug_entity in sorted(outlet_entity_map.items()):
                                if not plug_entity:
                                    continue
                                domain = plug_entity.split(".", 1)[0]
                                capability_rows.append(
                                    {
                                        "target": f"plug {plug_outlet}",
                                        "entity_id": plug_entity,
                                        "domain": domain,
                                        "allowed": plug_entity in allowed_entity_ids,
                                        "supported_actions": capabilities_for_domain(domain),
                                    }
                                )
                                seen_entities.add(plug_entity)
                            for alias_key, alias_entity in sorted(current_alias_map.items()):
                                if alias_entity in seen_entities:
                                    continue
                                domain = alias_entity.split(".", 1)[0]
                                capability_rows.append(
                                    {
                                        "target": alias_key,
                                        "entity_id": alias_entity,
                                        "domain": domain,
                                        "allowed": alias_entity in allowed_entity_ids,
                                        "supported_actions": capabilities_for_domain(domain),
                                    }
                                )
                            detail_parts.append(
                                f"showing {len(capability_rows)} controllable target(s)"
                            )
                            entity_id = "multiple"
                        detail = "; ".join(part for part in detail_parts if part)

                if (
                    status == "rejected"
                    and not action
                    and (now - last_command_ts) < action_rate_limit_seconds
                ):
                    status = "rejected"
                    detail = (
                        f"rate limited: wait at least {action_rate_limit_seconds:.1f}s between commands"
                    )
                elif status == "rejected" and not action and current_mode == "suggest":
                    status = "rejected"
                    detail = "mode=suggest: action execution disabled"
                elif status == "rejected" and not action:
                    planned_steps, detail = parse_direct_action_plan(
                        command_text,
                        extra_entity_alias_map=current_alias_map,
                    )
                    if not planned_steps and action_parse_with_ollama:
                        parsed_steps, parsed_detail = parse_ollama_action_plan(
                            ollama_url=ollama_url,
                            timeout=action_parse_timeout,
                            text=command_text,
                            extra_entity_alias_map=current_alias_map,
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
                                else current_alias_map.get(step_alias, "")
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
                                    and prev[0] in {"turn_on", "turn_off"}
                                    and step_action in {"turn_on", "turn_off"}
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
                                        step=step,
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
                                    "percentage": int(step.get("percentage", 0))
                                    if step.get("percentage") is not None
                                    else 0,
                                    "oscillating": step.get("oscillating")
                                    if "oscillating" in step
                                    else None,
                                    "preset_mode": str(step.get("preset_mode", ""))
                                    if step.get("preset_mode") is not None
                                    else "",
                                    "pulse_seconds": float(step.get("pulse_seconds", 0))
                                    if step.get("pulse_seconds") is not None
                                    else 0.0,
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
                                else current_alias_map.get(first_alias, "")
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
            if capability_rows:
                result["capabilities"] = capability_rows

            publish_action_result_payload(result)

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
