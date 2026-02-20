# Operations Runbook (Human + Automation)

## Scope
This file is for repeatable operational commands in a running deployment.

## Service Status
```bash
systemctl status subaru-telemetry.service --no-pager -n 40
systemctl status subaru-observability.service --no-pager -n 40
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

## Restart Stack
```bash
cd ~/subaru-pi-platform
sudo systemctl restart subaru-telemetry.service
cd observability && docker compose up -d --build
```

## MQTT Smoke Tests
```bash
mosquitto_sub -h 127.0.0.1 -v -t 'subaru/status' -C 1 -W 10
mosquitto_sub -h 127.0.0.1 -v -t 'subaru/data' -C 1 -W 10
mosquitto_sub -h 127.0.0.1 -v -t 'subaru/gps' -C 1 -W 10
```

## Influx Smoke Tests
```bash
cd ~/subaru-pi-platform
source observability/.env
curl -sS \
  -H "Authorization: Token $INFLUXDB_TOKEN" \
  -H "Content-Type: application/vnd.flux" \
  -H "Accept: application/csv" \
  "http://127.0.0.1:8086/api/v2/query?org=$INFLUXDB_ORG" \
  --data-binary "from(bucket: \"$INFLUXDB_BUCKET\") |> range(start: -2m) |> filter(fn: (r) => r._measurement == \"subaru_metrics\") |> limit(n: 5)"
```

```bash
cd ~/subaru-pi-platform
source observability/.env
curl -sS \
  -H "Authorization: Token $INFLUXDB_TOKEN" \
  -H "Content-Type: application/vnd.flux" \
  -H "Accept: application/csv" \
  "http://127.0.0.1:8086/api/v2/query?org=$INFLUXDB_ORG" \
  --data-binary "from(bucket: \"$INFLUXDB_BUCKET\") |> range(start: -2m) |> filter(fn: (r) => r._measurement == \"subaru_gps\") |> limit(n: 5)"
```

## Node-RED Dashboard Flow Backup/Restore
Backup:
```bash
cd ~/subaru-pi-platform/observability/nodered/data
cp flows.json "flows.backup.$(date +%Y%m%d_%H%M%S).json"
```

Restore:
```bash
cd ~/subaru-pi-platform/observability/nodered/data
cp flows.backup.<timestamp>.json flows.json
docker restart nodered
```

## Kiosk Launch (GUI session)
```bash
/home/pi/.local/bin/wrx-dashboard-kiosk.sh
```

## Race HUD Launch (for right-pane embed at port 8091)
```bash
cd ~/subaru-pi-platform
python3 gps/mqtt_gps_map_server.py --mqtt-topic subaru/gps --host 0.0.0.0 --port 8091
```

## GPS Simulator Launch
```bash
cd ~/subaru-pi-platform
python3 gps/gps_wanneroo_sim.py --mqtt-topic subaru/gps --rate-hz 5 --speed-mps 36
```
