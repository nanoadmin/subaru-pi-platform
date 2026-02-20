# Troubleshooting

## Telemetry service not running
```bash
sudo systemctl status subaru-telemetry.service --no-pager -n 80
tail -n 120 ~/subaru-pi-platform/telemetry/logs/service.log
```

## No MQTT messages
- Check local broker:
```bash
sudo systemctl status mosquitto
```
- Manual one-shot test:
```bash
~/subaru-pi-platform/telemetry/ssm_logger.py mqtt --hz 1 --samples 1
```

## K-line adapter missing
```bash
ls -l /dev/ttyUSB*
```
- If not present, replug adapter and check power/cable.

## Docker permission denied
- Log out/in after install, or reboot once.
- Temporary workaround: prefix docker commands with `sudo`.

## Grafana cannot connect to InfluxDB
- Re-check `observability/.env` values.
- Recreate stack:
```bash
cd ~/subaru-pi-platform/observability
docker compose down
docker compose up -d --build
```

## InfluxDB has no live telemetry points
- Check Telegraf container:
```bash
cd ~/subaru-pi-platform/observability
docker compose ps telegraf
docker logs --tail 120 telegraf-mqtt
```
- Check MQTT source messages:
```bash
mosquitto_sub -h 127.0.0.1 -v -t 'subaru/data' -C 1 -W 10
```
- Check Influx query result:
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

## GPS publisher or HUD not updating
- Check GPS topic has payloads:
```bash
mosquitto_sub -h 127.0.0.1 -v -t 'subaru/gps' -C 3 -W 10
```
- Check UART port permissions/presence for real GPS:
```bash
ls -l /dev/ttyS0
```
- Ensure serial console is disabled on `ttyS0` if using hardware GPS:
```bash
sudo systemctl stop serial-getty@ttyS0.service
sudo systemctl disable serial-getty@ttyS0.service
sudo systemctl mask serial-getty@ttyS0.service
```
- Check GPS Influx measurement (`subaru_gps`):
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

## Observability stack did not start on boot
```bash
sudo systemctl status subaru-observability.service --no-pager -n 80
journalctl -u subaru-observability.service -n 120 --no-pager
```

## Race pane on Node-RED dashboard is blank
- The right pane embeds `http://<pi-ip>:8091/`.
- Start the GPS HUD server on `8091`:
```bash
cd ~/subaru-pi-platform
python3 gps/mqtt_gps_map_server.py --mqtt-topic subaru/gps --host 0.0.0.0 --port 8091
```
- Then hard-refresh browser on `http://<pi-ip>:1880/ui/`.

## Keyring popup appears in kiosk mode
- Keyring services can be disabled for kiosk deployments:
```bash
systemctl --user mask --now gnome-keyring-daemon.service gnome-keyring-daemon.socket
pkill -f gnome-keyring-daemon || true
```
- Kiosk launcher should include Chromium flag `--password-store=basic`:
```bash
sed -n '1,120p' ~/.local/bin/wrx-dashboard-kiosk.sh
```
