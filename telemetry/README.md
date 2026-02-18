# Telemetry

Subaru SSM2/K-line ECU logger that publishes to MQTT.

## Main script
- `ssm_logger.py`

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
