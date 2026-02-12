import os
import queue
import threading
import time

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

    if not influx_token:
        raise RuntimeError("INFLUX_TOKEN is required for authenticated InfluxDB access.")

    influx = InfluxDBClient(url=influx_url, token=influx_token, org=influx_org)
    write_api = influx.write_api(write_options=SYNCHRONOUS)
    suggestion_queue: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=suggestion_queue_max)

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

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if mqtt_user:
        client.username_pw_set(mqtt_user, mqtt_password)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    threading.Thread(target=suggestion_worker, daemon=True).start()

    client.connect(mqtt_host, mqtt_port, mqtt_keepalive)
    client.loop_forever()


if __name__ == "__main__":
    main()
