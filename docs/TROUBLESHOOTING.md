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
docker compose up -d
```

## Observability stack did not start on boot
```bash
sudo systemctl status subaru-observability.service --no-pager -n 80
journalctl -u subaru-observability.service -n 120 --no-pager
```
