#!/usr/bin/env python3
"""Low-latency GPS -> MQTT publisher for race telemetry."""

import argparse
import json
import signal
import sys
import time
from typing import Optional, Tuple

import paho.mqtt.client as mqtt
import serial


PMTK_RATE_COMMANDS = {
    1: "$PMTK220,1000*1F\r\n",
    5: "$PMTK220,200*2C\r\n",
    10: "$PMTK220,100*2F\r\n",
}

# RMC + GGA only. Reduces UART load so higher fix rates are practical.
PMTK_RMC_GGA_ONLY = "$PMTK314,0,1,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0*28\r\n"


def parse_dm_to_decimal(value: str, hemi: str, deg_len: int) -> Optional[float]:
    if not value or not hemi:
        return None
    try:
        degrees = int(value[:deg_len])
        minutes = float(value[deg_len:])
    except (TypeError, ValueError):
        return None

    dec = degrees + (minutes / 60.0)
    if hemi in ("S", "W"):
        dec = -dec
    return dec


def parse_gga(fields: list[str]) -> Optional[dict]:
    # $G?GGA,time,lat,NS,lon,EW,fixq,sats,hdop,alt,M,...
    if len(fields) < 10:
        return None

    try:
        fixq = int(fields[6] or "0")
    except ValueError:
        return None

    if fixq <= 0:
        return None

    lat = parse_dm_to_decimal(fields[2], fields[3], 2)
    lon = parse_dm_to_decimal(fields[4], fields[5], 3)
    if lat is None or lon is None:
        return None

    out = {
        "lat": lat,
        "lon": lon,
        "fixq": fixq,
        "nmea_time": fields[1],
    }

    if fields[7]:
        try:
            out["sats"] = int(fields[7])
        except ValueError:
            pass
    if fields[8]:
        try:
            out["hdop"] = float(fields[8])
        except ValueError:
            pass
    if fields[9]:
        try:
            out["alt_m"] = float(fields[9])
        except ValueError:
            pass

    return out


def parse_rmc(fields: list[str]) -> Optional[Tuple[float, float]]:
    # $G?RMC,time,status,lat,NS,lon,EW,speed_knots,course,...
    if len(fields) < 9:
        return None
    if fields[2] != "A":
        return None

    speed_kn = fields[7]
    course = fields[8]
    try:
        speed_mps = float(speed_kn) * 0.514444 if speed_kn else 0.0
        track_deg = float(course) if course else 0.0
    except ValueError:
        return None

    return speed_mps, track_deg


def configure_gps(ser: serial.Serial, rate_hz: int, rmc_gga_only: bool) -> None:
    cmds = []
    if rmc_gga_only:
        cmds.append(PMTK_RMC_GGA_ONLY)
    if rate_hz in PMTK_RATE_COMMANDS:
        cmds.append(PMTK_RATE_COMMANDS[rate_hz])

    for cmd in cmds:
        ser.write(cmd.encode("ascii"))
        ser.flush()
        time.sleep(0.08)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Fast GPS to MQTT bridge")
    p.add_argument("--serial-port", default="/dev/ttyS0")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--read-timeout", type=float, default=0.15)
    p.add_argument("--mqtt-host", default="127.0.0.1")
    p.add_argument("--mqtt-port", type=int, default=1883)
    p.add_argument("--mqtt-topic", default="subaru/gps")
    p.add_argument("--qos", type=int, default=0, choices=(0, 1, 2))
    p.add_argument("--retain", action="store_true")
    p.add_argument(
        "--min-interval-ms",
        type=int,
        default=0,
        help="Optional publish throttle. 0 = publish every valid fix",
    )
    p.add_argument(
        "--precision",
        type=int,
        default=7,
        help="Decimal precision for lat/lon in payload",
    )
    p.add_argument(
        "--set-gps-rate-hz",
        type=int,
        choices=(0, 1, 5, 10),
        default=0,
        help="Send PMTK command to set real GPS update rate (0 = no config)",
    )
    p.add_argument(
        "--set-gps-rmc-gga-only",
        action="store_true",
        help="Send PMTK sentence filter for RMC+GGA only",
    )
    return p


def main() -> int:
    args = build_parser().parse_args()
    running = True

    def stop(_sig, _frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.enable_logger()
    client.reconnect_delay_set(min_delay=1, max_delay=2)

    try:
        client.connect(args.mqtt_host, args.mqtt_port, keepalive=20)
    except Exception as exc:
        print(f"MQTT connect failed: {exc}", file=sys.stderr)
        return 2

    client.loop_start()

    try:
        ser = serial.Serial(args.serial_port, baudrate=args.baud, timeout=args.read_timeout)
    except Exception as exc:
        print(f"Serial open failed: {exc}", file=sys.stderr)
        client.loop_stop()
        client.disconnect()
        return 3

    if args.set_gps_rate_hz or args.set_gps_rmc_gga_only:
        try:
            configure_gps(ser, args.set_gps_rate_hz, args.set_gps_rmc_gga_only)
        except Exception as exc:
            print(f"GPS configure warning: {exc}", file=sys.stderr)

    latest_speed_mps = None
    latest_track_deg = None
    min_interval_ns = args.min_interval_ms * 1_000_000
    last_pub_ns = 0

    try:
        while running:
            raw = ser.readline()
            if not raw:
                continue
            if raw[0:1] != b"$":
                continue

            try:
                line = raw.decode("ascii", errors="ignore").strip()
            except Exception:
                continue
            if not line:
                continue

            if line.startswith(("$GPRMC", "$GNRMC")):
                fields = line.split(",")
                parsed = parse_rmc(fields)
                if parsed is not None:
                    latest_speed_mps, latest_track_deg = parsed
                continue

            if not line.startswith(("$GPGGA", "$GNGGA")):
                continue

            fields = line.split(",")
            fix = parse_gga(fields)
            if fix is None:
                continue

            now_ns = time.time_ns()
            if min_interval_ns and now_ns - last_pub_ns < min_interval_ns:
                continue

            payload = {
                "ts_ns": now_ns,
                "lat": round(fix["lat"], args.precision),
                "lon": round(fix["lon"], args.precision),
                "fixq": fix["fixq"],
            }
            if "nmea_time" in fix:
                payload["nmea_time"] = fix["nmea_time"]
            if "sats" in fix:
                payload["sats"] = fix["sats"]
            if "hdop" in fix:
                payload["hdop"] = fix["hdop"]
            if "alt_m" in fix:
                payload["alt_m"] = fix["alt_m"]
            if latest_speed_mps is not None:
                payload["speed_mps"] = latest_speed_mps
            if latest_track_deg is not None:
                payload["track_deg"] = latest_track_deg

            data = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
            result = client.publish(args.mqtt_topic, data, qos=args.qos, retain=args.retain)

            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                last_pub_ns = now_ns
            else:
                print(f"MQTT publish failed rc={result.rc}", file=sys.stderr)

    finally:
        try:
            ser.close()
        except Exception:
            pass
        client.loop_stop()
        client.disconnect()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
