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

## 6. Optional Telegraf MQTT ingest
```bash
cd ~/subaru-pi-platform/observability
docker compose --profile mqtt up -d
```

## 7. Validate end-to-end
```bash
mosquitto_sub -h 127.0.0.1 -v -t 'subaru/#' -C 10 -W 10
```
