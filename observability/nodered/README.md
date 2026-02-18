# Node-RED Dashboard

This folder contains versioned Node-RED runtime files for the Subaru dashboard.

## Files
- `data/flows.json`: dashboard flow (MQTT -> UI)
- `data/package.json`: Node-RED node dependencies (`node-red-dashboard`)

## MQTT source
The flow is configured to read MQTT from `host.docker.internal:1883` so Dockerized Node-RED can consume host Mosquitto topics (`subaru/*`).
