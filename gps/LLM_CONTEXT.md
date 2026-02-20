# GPS LLM Context

## Scope
- Directory: `/home/pi/subaru-pi-platform/gps`
- Keep edits inside this directory.

## Scripts
- `gps_to_mqtt_fast.py`: real UART GPS -> MQTT.
- `gps_wanneroo_sim.py`: simulated Wanneroo lap generator -> MQTT.
- `mqtt_gps_map_server.py`: base race HUD server (map + lap/split timing).
- `mqtt_gps_map_server_latest.py`: extended HUD server with per-driver event topics and optional Influx writes.
- `wanneroo_main_loop.json`: track path source.

## Default transport
- MQTT broker: `127.0.0.1:1883`
- Default topic: `subaru/gps`

## HUD behavior
- Script default port is `8090`, but project integration uses `8091`.
- Fixed to Wanneroo track map.
- Shows start marker and split markers (`S1`, `S2`, `S3`).
- Computes timing from projected GPS position on track:
  - `current_lap_sec`
  - `last_lap_sec`
  - `best_lap_sec`
  - `lap_count`
  - `current_splits_sec`
  - `last_splits_sec`
- Endpoints:
  - `/` -> HUD HTML
  - `/latest` -> latest telemetry + timing snapshot
  - `/meta` -> track/start/split geometry for map

## Simulation behavior
- Continuous loop on `wanneroo_main_loop.json`.
- Publishes plausible telemetry fields including speed and heading.
- Key args: `--rate-hz`, `--speed-mps`, `--split-variation-pct`, `--random-seed`, `--mqtt-topic`, `--track-file`.

## Verified status
- End-to-end validated: simulator -> MQTT -> HUD server.
- Verified split and lap timings are produced (`lap_count`, `last_lap_sec`, split arrays).

## Integration note
- Node-RED dashboard (`http://<pi>:1880/ui/`) embeds race HUD from `http://<pi>:8091/`.

## Operational cautions
- Serial port contention on `/dev/ttyS0` will break real GPS reads.
- Avoid running simulator and real GPS publisher into same topic unless intentional.
