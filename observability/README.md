# Observability

Docker Compose stack for:
- InfluxDB 2.x
- Grafana OSS
- Node-RED dashboard
- Telegraf MQTT ingest (enabled by default)

## Setup
```bash
cp .env.example .env
nano .env
```

## Start
```bash
docker compose up -d --build
```

## Enable As Boot Service (Recommended)
From repo root:
```bash
bash scripts/setup_observability_service.sh
```

## URLs
- Grafana: `http://<pi-ip>:3000`
- InfluxDB: `http://<pi-ip>:8086`
- Node-RED: `http://<pi-ip>:1880/`
- Node-RED dashboard UI: `http://<pi-ip>:1880/ui/`
- Race HUD (embedded right pane target): `http://<pi-ip>:8091/`

Telegraf now ingests:
- `subaru/data` -> InfluxDB measurement `subaru_metrics`
- `subaru/gps` -> InfluxDB measurement `subaru_gps`

Run race HUD server (separate process):
```bash
cd ~/subaru-pi-platform
python3 gps/mqtt_gps_map_server.py --mqtt-topic subaru/gps --host 0.0.0.0 --port 8091
```

## Node-RED files
- `nodered/Dockerfile`: pinned Node-RED image with dashboard node preinstalled
- `nodered/data/flows.json`: dashboard flow
- `nodered/data/package.json`: Node-RED dependency list (`node-red-dashboard`)
