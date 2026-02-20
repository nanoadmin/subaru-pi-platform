# GPS Tools (UART, MQTT, Simulator, Race HUD)

This directory contains GPS tooling for real hardware and simulation, all publishing to local MQTT.

## Files

- `gps_to_mqtt_fast.py`: real GPS UART (`/dev/ttyS0`) -> MQTT
- `gps_wanneroo_sim.py`: continuous Wanneroo Raceway lap simulator -> MQTT
- `mqtt_gps_map_server.py`: base race HUD web server (map + split/lap timing)
- `mqtt_gps_map_server_latest.py`: extended race HUD server (adds driver events + optional Influx writes)
- `wanneroo_main_loop.json`: track polyline used by simulator/HUD
- `LLM_CONTEXT.md`: concise machine-readable context for automation agents

## MQTT defaults

- Host: `127.0.0.1`
- Port: `1883`
- Topic: `subaru/gps`
- QoS: `0`
- Retain: `false`

## 1) Real GPS publisher

Run with hardware GPS:

```bash
cd /home/pi/subaru-pi-platform/gps
python3 gps_to_mqtt_fast.py
```

For faster coordinate updates from the GPS module:

```bash
python3 gps_to_mqtt_fast.py \
  --serial-port /dev/ttyS0 \
  --set-gps-rmc-gga-only \
  --set-gps-rate-hz 5
```

## 2) Wanneroo race simulation publisher

Simulates a car driving continuous laps on the Wanneroo main loop in real time.

```bash
cd /home/pi/subaru-pi-platform/gps
python3 gps_wanneroo_sim.py \
  --mqtt-topic subaru/gps \
  --rate-hz 5 \
  --speed-mps 36
```

Useful flags:

- `--rate-hz`: publish frequency (default `5`)
- `--speed-mps`: simulated speed along track (default `38`)
- `--split-variation-pct`: random split-time variation percentage (default `5.0`)
- `--random-seed`: deterministic simulation seed (default random)
- `--mqtt-topic`: target topic (default `subaru/gps`)
- `--track-file`: override track JSON file (default `wanneroo_main_loop.json`)

## 3) Race HUD web display (right-half screen)

Recommended for dashboard embed (`:8091`):

```bash
cd /home/pi/subaru-pi-platform/gps
python3 mqtt_gps_map_server.py \
  --mqtt-topic subaru/gps \
  --host 0.0.0.0 \
  --port 8091
```

Extended variant (events + optional Influx output):

```bash
cd /home/pi/subaru-pi-platform/gps
python3 mqtt_gps_map_server_latest.py \
  --mqtt-topic subaru/gps \
  --events-topic-base drivers \
  --influx-enabled \
  --influx-url http://127.0.0.1:8086 \
  --influx-db subaru \
  --influx-v1-user <user> \
  --influx-v1-password <password> \
  --host 0.0.0.0 \
  --port 8091
```

Open in browser:

- On same Pi: `http://127.0.0.1:8091`
- From LAN: `http://<PI_IP>:8091`

## Typical simulator + HUD test flow

Terminal 1 (HUD):

```bash
cd /home/pi/subaru-pi-platform/gps
python3 mqtt_gps_map_server.py --mqtt-topic subaru/gps --host 0.0.0.0 --port 8091
```

Terminal 2 (simulator):

```bash
cd /home/pi/subaru-pi-platform/gps
python3 gps_wanneroo_sim.py --mqtt-topic subaru/gps --rate-hz 5 --speed-mps 36
```

Terminal 3 (optional MQTT inspect):

```bash
mosquitto_sub -h 127.0.0.1 -p 1883 -t subaru/gps -v
mosquitto_sub -h 127.0.0.1 -p 1883 -t 'drivers/+/splits' -v
mosquitto_sub -h 127.0.0.1 -p 1883 -t 'drivers/+/laps' -v
```

## Optional: open Chromium on right half of 7-inch display

If your display is 800x480:

```bash
chromium-browser --app=http://127.0.0.1:8091 --window-size=400,480 --window-position=400,0
```

## Optional: start on Pi reboot (systemd)

Create service file:

```bash
sudo tee /etc/systemd/system/wrx-gps-hud.service >/dev/null <<'EOF_SERVICE'
[Unit]
Description=WRX GPS HUD (MQTT + lap/split timing)
After=network-online.target mosquitto.service
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/subaru-pi-platform/gps
ExecStart=/usr/bin/python3 /home/pi/subaru-pi-platform/gps/mqtt_gps_map_server.py --mqtt-host 127.0.0.1 --mqtt-port 1883 --mqtt-topic subaru/gps --host 0.0.0.0 --port 8091
Restart=always
RestartSec=1

[Install]
WantedBy=multi-user.target
EOF_SERVICE
```

Enable now and on reboot:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now wrx-gps-hud.service
```

Check status/logs:

```bash
systemctl status wrx-gps-hud.service
journalctl -u wrx-gps-hud.service -f
```

## Dependencies

```bash
python3 -m pip install paho-mqtt pyserial
```

## Notes

- `/dev/ttyS0` can only be read by one process at a time.
- Do not run real GPS publisher and simulator on the same topic unless intentionally mixing streams.
- Node-RED main dashboard expects race HUD at `http://<pi-ip>:8091/` for right-pane embed.
