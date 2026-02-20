# MQTT Topics

Base topic: `subaru`

## Core topics
- `subaru/data`: full JSON payload with `metrics` and `units`
- `subaru/status`: logger health and stats
- `subaru/dtc`: DTC snapshot (current + historic)
- `subaru/gps`: GPS payload from real UART publisher or simulator

Notes:
- Telegraf ingests `subaru/data` into InfluxDB measurement `subaru_metrics`.
- Telegraf ingests `subaru/gps` into InfluxDB measurement `subaru_gps`.

## Per-metric topics
Published as: `subaru/<metric>`
Examples:
- `subaru/engine_speed`
- `subaru/battery_v`
- `subaru/coolant_temperature`

## GPS payload fields
Typical fields on `subaru/gps`:
- `ts_ns`
- `lat`
- `lon`
- `speed_mps`
- `track_deg`
- `fixq`
- `sats`
- `hdop`
- `alt_m`
