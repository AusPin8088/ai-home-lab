#!/bin/sh
set -eu

: "${INFLUX_URL:=http://influxdb:8181}"
: "${INFLUXDB_DATABASE:=home}"
: "${INFLUXDB_TOKEN:?INFLUXDB_TOKEN is required}"

echo "Waiting for InfluxDB at ${INFLUX_URL}..."
attempt=0
while ! influxdb3 show databases --host "${INFLUX_URL}" --token "${INFLUXDB_TOKEN}" --format json >/tmp/influx-databases.json 2>/tmp/influx-init.err
do
  attempt=$((attempt + 1))
  if [ "${attempt}" -ge 60 ]; then
    echo "InfluxDB did not become ready in time."
    cat /tmp/influx-init.err || true
    exit 1
  fi
  sleep 2
done

if grep -F "\"iox::database\":\"${INFLUXDB_DATABASE}\"" /tmp/influx-databases.json >/dev/null 2>&1; then
  echo "Database '${INFLUXDB_DATABASE}' already exists."
  exit 0
fi

echo "Creating database '${INFLUXDB_DATABASE}'..."
influxdb3 create database "${INFLUXDB_DATABASE}" --host "${INFLUX_URL}" --token "${INFLUXDB_TOKEN}"
echo "Database '${INFLUXDB_DATABASE}' is ready."
