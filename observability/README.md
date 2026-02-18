# Observability

Docker Compose stack for:
- InfluxDB 2.x
- Grafana OSS
- Node-RED dashboard
- optional Telegraf MQTT ingest profile

## Setup
```bash
cp .env.example .env
nano .env
```

## Start
```bash
docker compose up -d
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

## Start with MQTT ingest
```bash
docker compose --profile mqtt up -d
```

## Node-RED files
- `nodered/data/flows.json`: dashboard flow
- `nodered/data/package.json`: Node-RED dependency list (`node-red-dashboard`)
