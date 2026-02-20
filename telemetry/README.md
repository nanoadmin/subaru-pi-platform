# Telemetry

Subaru SSM2/K-line ECU logger that publishes to MQTT.

## Main script
- `ssm_logger.py`

## Hardware/OS assumptions
- K-line adapter on `/dev/ttyUSB0`
- Local MQTT broker (`mosquitto`) on `127.0.0.1:1883`
- Raspberry Pi OS with Python 3 and serial access permissions

## Quick manual tests
```bash
./ssm_logger.py info
./ssm_logger.py mqtt --hz 1 --samples 1
```

## Service setup
Use repo helper:
```bash
bash ../scripts/setup_telemetry_service.sh
```

## Published topics
- `subaru/data`
- `subaru/status`
- `subaru/dtc`
- `subaru/<metric>`
