# Subaru Pi Platform

Raspberry Pi stack for Subaru ECU telemetry, live dashboarding, GPS race HUD, and time-series storage.

## What This Repo Provides
- `telemetry/`: Subaru SSM2/K-line ECU polling and MQTT publishing (`subaru/*`)
- `observability/`: InfluxDB + Grafana + Node-RED + Telegraf ingest
- `gps/`: real UART GPS publisher, race simulator, and race HUD web server
- `scripts/`: install and service bootstrap helpers
- `docs/`: setup/troubleshooting/runbook docs for humans and automation

## Hardware
Minimum:
- Raspberry Pi 4/5
- microSD (32GB+ recommended)
- LAN/Wi-Fi connectivity

ECU telemetry:
- Subaru-compatible K-line adapter on `/dev/ttyUSB0`

GPS (optional):
- UART GPS module wired to Pi UART (`/dev/ttyS0`)

Display/kiosk (optional):
- HDMI/DSI screen for always-on dashboard

## Software Baseline
- Raspberry Pi OS Bookworm (64-bit recommended)
- Docker + Compose plugin
- Mosquitto broker
- Python 3

## Quick Start (Fresh Clone)
1. Clone:
```bash
git clone <your-repo-url> ~/subaru-pi-platform
cd ~/subaru-pi-platform
```
2. Install prerequisites:
```bash
bash scripts/install_prereqs.sh
```
3. Configure ECU telemetry service:
```bash
bash scripts/setup_telemetry_service.sh
```
4. Configure observability env:
```bash
cp observability/.env.example observability/.env
nano observability/.env
```
5. Start observability stack:
```bash
bash scripts/start_observability.sh
```
6. Enable observability stack at boot:
```bash
bash scripts/setup_observability_service.sh
```

## Runtime URLs
- Node-RED UI: `http://<pi-ip>:1880/`
- Main dashboard: `http://<pi-ip>:1880/ui/`
- Grafana: `http://<pi-ip>:3000`
- InfluxDB: `http://<pi-ip>:8086`
- Race HUD server (if running): `http://<pi-ip>:8091/`

Note: the Node-RED main dashboard embeds race HUD from `:8091` on the right pane.

## Verification
Telemetry topics:
```bash
mosquitto_sub -h 127.0.0.1 -v -t 'subaru/status' -C 1 -W 10
mosquitto_sub -h 127.0.0.1 -v -t 'subaru/data' -C 1 -W 10
```
Influx ingest (`subaru_metrics`):
```bash
source observability/.env
curl -sS \
  -H "Authorization: Token $INFLUXDB_TOKEN" \
  -H "Content-Type: application/vnd.flux" \
  -H "Accept: application/csv" \
  "http://127.0.0.1:8086/api/v2/query?org=$INFLUXDB_ORG" \
  --data-binary "from(bucket: \"$INFLUXDB_BUCKET\") |> range(start: -2m) |> filter(fn: (r) => r._measurement == \"subaru_metrics\") |> limit(n: 5)"
```
GPS ingest (`subaru_gps`):
```bash
source observability/.env
curl -sS \
  -H "Authorization: Token $INFLUXDB_TOKEN" \
  -H "Content-Type: application/vnd.flux" \
  -H "Accept: application/csv" \
  "http://127.0.0.1:8086/api/v2/query?org=$INFLUXDB_ORG" \
  --data-binary "from(bucket: \"$INFLUXDB_BUCKET\") |> range(start: -2m) |> filter(fn: (r) => r._measurement == \"subaru_gps\") |> limit(n: 5)"
```

## Human + Robot Docs
- Human setup: `docs/SETUP_PI.md`
- Human troubleshooting: `docs/TROUBLESHOOTING.md`
- Topic contracts: `docs/MQTT_TOPICS.md`
- GPS usage: `gps/README.md`
- Robot/operator runbook: `docs/OPERATIONS.md`
- Robot agent guide: `AGENTS.md`
- Git push flow: `docs/GIT_PUBLISH.md`
