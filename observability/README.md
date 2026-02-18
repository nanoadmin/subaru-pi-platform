# Observability

Docker Compose stack for:
- InfluxDB 2.x
- Grafana OSS
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

## Start with MQTT ingest
```bash
docker compose --profile mqtt up -d
```
