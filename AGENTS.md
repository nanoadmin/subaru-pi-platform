# AGENTS Guide

This file is for coding/ops agents working in this repo.

## Repo purpose
- Raspberry Pi telemetry + observability stack for Subaru ECU and GPS race HUD.

## Primary directories
- `telemetry/`: ECU polling and MQTT publish (`subaru/*`)
- `observability/`: Docker stack (InfluxDB, Grafana, Node-RED, Telegraf)
- `gps/`: UART GPS publisher, simulator, race HUD web server
- `scripts/`: install/bootstrap helpers
- `docs/`: human runbooks

## Canonical runtime paths
- Repo root: `/home/pi/subaru-pi-platform`
- Node-RED flow file: `observability/nodered/data/flows.json`
- Telegraf config: `observability/telegraf/telegraf.conf`
- Telemetry service script: `telemetry/ssm_logger.py`

## Network/ports
- Mosquitto MQTT: `127.0.0.1:1883`
- Node-RED editor: `:1880`
- Node-RED dashboard: `:1880/ui/`
- InfluxDB: `:8086`
- Grafana: `:3000`
- Race HUD: `:8091` (project integration target)

## Topic contract
- `subaru/data` -> Influx measurement `subaru_metrics`
- `subaru/gps` -> Influx measurement `subaru_gps`
- `subaru/status`, `subaru/dtc`, `subaru/<metric>` also published

## Startup and services
- Install prereqs: `bash scripts/install_prereqs.sh`
- Start observability: `bash scripts/start_observability.sh`
- Install telemetry service: `bash scripts/setup_telemetry_service.sh`
- Install observability service: `bash scripts/setup_observability_service.sh`

## Validation commands
- Python compile check:
  - `python3 -m py_compile telemetry/ssm_logger.py telemetry/read_error_codes.py gps/gps_to_mqtt_fast.py gps/gps_wanneroo_sim.py gps/mqtt_gps_map_server.py gps/mqtt_gps_map_server_latest.py`
- MQTT smoke:
  - `mosquitto_sub -h 127.0.0.1 -v -t 'subaru/data' -C 1 -W 10`
  - `mosquitto_sub -h 127.0.0.1 -v -t 'subaru/gps' -C 1 -W 10`

## Editing rules for agents
- Keep secrets out of git (`observability/.env` is ignored).
- Do not commit runtime artifacts (logs, caches, backup flow dumps).
- Keep dashboard race embed pointed to `:8091` unless explicitly changed by maintainers.
- Prefer updating docs when behavior/ports/services change.
