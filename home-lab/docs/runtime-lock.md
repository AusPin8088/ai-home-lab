# Runtime Lock

This file records pinned container image references used by `docker/compose.yaml`.

## Pinned Images

| Service | Image Reference |
| --- | --- |
| homeassistant | `ghcr.io/home-assistant/home-assistant@sha256:17441c45ba14560b4ef727ee06aac4d605cf0dc0625fc4f2e043cb2551d72749` |
| mosquitto | `eclipse-mosquitto@sha256:9cfdd46ad59f3e3e5f592f6baf57ab23e1ad00605509d0f5c1e9b179c5314d87` |
| nodered | `nodered/node-red@sha256:7dfe40efdd7b9f21916f083802bfe60a762bc020969d95553ffa020c97a72eb9` |
| influxdb | `quay.io/influxdb/influxdb3-core@sha256:ad4ad468af9b2fbbe92523a5764217916cd1bdd43f578aef504da133ff3f0d0b` |
| grafana | `grafana/grafana@sha256:5683be4319a6da1d6ab28c3443b3739683e367f8d72d800638390a04a2680c1c` |
| ollama | `ollama/ollama@sha256:5f7a20da9b4d42d1909b4693f90942135bcabc335ee42d529c0d143c44a92311` |
| agent | Local build from `services/agent/Dockerfile` |

## Refresh Procedure

1. Pull candidate images.
2. Validate startup and core workflows (`docker compose ps`, Influx auth, MQTT ingest, Grafana datasource).
3. Resolve new digests:
   - `docker image inspect <image:tag> --format "{{.RepoDigests}}"`
4. Update `docker/compose.yaml` with new digest references.
5. Update this file with the same references and date.
