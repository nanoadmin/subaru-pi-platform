# Subaru Pi Platform

Complete Raspberry Pi stack for Subaru telemetry + observability.

This repo combines:
- `telemetry/`: ECU polling and MQTT publishing (`subaru/*` topics)
- `observability/`: InfluxDB + Grafana + optional Telegraf MQTT ingest

Designed for a fresh Raspberry Pi install.

## Hardware You Need
- Raspberry Pi (Pi 4/5 recommended)
- microSD card (32GB+)
- Subaru-compatible K-line adapter (`/dev/ttyUSB0`)
- Network access (LAN/Wi-Fi)

## Software Prereqs
- Raspberry Pi OS (Bookworm recommended)
- SSH enabled (optional but recommended)

## Quick Start (New Pi)
1. Clone repo:
```bash
git clone <your-repo-url> ~/subaru-pi-platform
cd ~/subaru-pi-platform
```

2. Install packages + Docker:
```bash
bash scripts/install_prereqs.sh
```

3. Configure telemetry service:
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

6. Verify telemetry MQTT output:
```bash
mosquitto_sub -h 127.0.0.1 -v -t 'subaru/status' -C 1 -W 10
mosquitto_sub -h 127.0.0.1 -v -t 'subaru/dtc' -C 1 -W 10
```

## URLs
- Grafana: `http://<pi-ip>:3000`
- InfluxDB: `http://<pi-ip>:8086`

## Repo Layout
- `telemetry/ssm_logger.py`: main ECU -> MQTT logger
- `telemetry/systemd/subaru-telemetry.service.template`: service template
- `telemetry/logrotate/subaru-telemetry`: logrotate template
- `observability/docker-compose.yml`: InfluxDB/Grafana/Telegraf stack
- `scripts/`: install and setup scripts
- `docs/`: guided setup and troubleshooting

## First Docs To Read
- `docs/SETUP_PI.md`
- `docs/TROUBLESHOOTING.md`
- `docs/MQTT_TOPICS.md`

- `docs/GIT_PUBLISH.md`
