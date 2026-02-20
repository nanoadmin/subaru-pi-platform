# Setup Guide For New Raspberry Pi

## 1. Flash OS
- Use Raspberry Pi Imager
- OS: Raspberry Pi OS (64-bit)
- Set hostname, Wi-Fi, SSH during imaging (recommended)

## 2. First boot
```bash
sudo apt update && sudo apt full-upgrade -y
sudo reboot
```

## 3. Clone and install
```bash
git clone <your-repo-url> ~/subaru-pi-platform
cd ~/subaru-pi-platform
bash scripts/install_prereqs.sh
```

## 4. Telemetry service
```bash
bash scripts/setup_telemetry_service.sh
sudo systemctl status subaru-telemetry.service
```

## 5. Observability
```bash
cp observability/.env.example observability/.env
nano observability/.env
bash scripts/start_observability.sh
bash scripts/setup_observability_service.sh
```

## 6. Validate end-to-end
```bash
mosquitto_sub -h 127.0.0.1 -v -t 'subaru/#' -C 10 -W 10
```

## 7. Confirm InfluxDB is receiving telemetry
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

## 8. Optional GPS simulator + HUD
Terminal 1:
```bash
cd ~/subaru-pi-platform
python3 gps/mqtt_gps_map_server.py --mqtt-topic subaru/gps --host 0.0.0.0 --port 8091
```

Terminal 2:
```bash
cd ~/subaru-pi-platform
python3 gps/gps_wanneroo_sim.py --mqtt-topic subaru/gps --rate-hz 5 --speed-mps 36
```

Then open `http://<pi-ip>:8091/`.
