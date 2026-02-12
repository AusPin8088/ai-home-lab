import os
import queue
import threading
import time

import paho.mqtt.client as mqtt
import requests
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

MQTT_HOST = os.getenv("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")
MQTT_KEEPALIVE = int(os.getenv("MQTT_KEEPALIVE", "120"))

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")
INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8181")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "")
INFLUX_ORG = os.getenv("INFLUX_ORG", "homelab")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "home")

TOPIC = "home/#"
SUGGESTION_QUEUE_MAX = int(os.getenv("SUGGESTION_QUEUE_MAX", "1000"))
SUGGESTION_HTTP_TIMEOUT = int(os.getenv("SUGGESTION_HTTP_TIMEOUT", "20"))

if not INFLUX_TOKEN:
    raise RuntimeError("INFLUX_TOKEN is required for authenticated InfluxDB access.")

influx = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
write_api = influx.write_api(write_options=SYNCHRONOUS)
suggestion_queue: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=SUGGESTION_QUEUE_MAX)


def ollama_suggest(text: str) -> str:
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
            f"{OLLAMA_URL}/api/generate",
            json=payload,
            timeout=SUGGESTION_HTTP_TIMEOUT,
        )
        response.raise_for_status()
        return response.json().get("response", "").strip()
    except Exception as exc:
        return f"(ollama error: {exc})"


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
        write_api.write(bucket=INFLUX_BUCKET, record=point)
    except Exception as exc:
        print("influx write mqtt_event failed:", exc, flush=True)

    topic_lower = msg.topic.lower()
    is_actionable_state = (
        topic_lower.endswith("/state")
        and (
            "/switch/" in topic_lower
            or "/light/" in topic_lower
            or "/climate/" in topic_lower
            or "/binary_sensor/" in topic_lower
        )
    )
    is_power_topic = "power" in topic_lower

    if is_actionable_state or is_power_topic:
        try:
            suggestion_queue.put_nowait((msg.topic, payload))
        except queue.Full:
            print("suggestion queue full; dropping topic=", msg.topic, flush=True)


def suggestion_worker():
    while True:
        topic, payload = suggestion_queue.get()
        try:
            suggestion = ollama_suggest(f"topic={topic} payload={payload}")
            suggestion_point = (
                Point("agent_suggestion")
                .tag("topic", topic)
                .field("suggestion", suggestion[:5000])
                .time(time.time_ns(), WritePrecision.NS)
            )
            write_api.write(bucket=INFLUX_BUCKET, record=suggestion_point)
        except Exception as exc:
            print("influx write agent_suggestion failed:", exc, flush=True)
        finally:
            suggestion_queue.task_done()


client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
if MQTT_USER:
    client.username_pw_set(MQTT_USER, MQTT_PASSWORD)

client.on_connect = on_connect
client.on_disconnect = on_disconnect
client.on_message = on_message
client.reconnect_delay_set(min_delay=1, max_delay=30)

threading.Thread(target=suggestion_worker, daemon=True).start()

client.connect(MQTT_HOST, MQTT_PORT, MQTT_KEEPALIVE)
client.loop_forever()
