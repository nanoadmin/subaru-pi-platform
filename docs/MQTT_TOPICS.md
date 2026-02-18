# MQTT Topics

Base topic: `subaru`

## Core topics
- `subaru/data`: full JSON payload with `metrics` and `units`
- `subaru/status`: logger health and stats
- `subaru/dtc`: DTC snapshot (current + historic)

## Per-metric topics
Published as: `subaru/<metric>`
Examples:
- `subaru/engine_speed`
- `subaru/battery_v`
- `subaru/coolant_temperature`
