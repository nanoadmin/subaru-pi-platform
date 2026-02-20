# Node-RED Dashboard

This folder contains versioned Node-RED runtime files for the Subaru dashboard.

## Files
- `Dockerfile`: Node-RED image with dashboard plugin preinstalled
- `data/flows.json`: dashboard flow (MQTT -> UI)
- `data/package.json`: Node-RED node dependencies (`node-red-dashboard`)

## MQTT source
The flow is configured to read MQTT from `host.docker.internal:1883` so Dockerized Node-RED can consume host Mosquitto topics (`subaru/*`).

## Layout
- Left pane: WRX telemetry dashboard (speed/RPM/coolant/DTC)
- Right pane: embedded race HUD iframe targeting `http://<pi-host>:8091/`

If the right pane is blank, start:
```bash
python3 /home/pi/subaru-pi-platform/gps/mqtt_gps_map_server.py --mqtt-topic subaru/gps --host 0.0.0.0 --port 8091
```
