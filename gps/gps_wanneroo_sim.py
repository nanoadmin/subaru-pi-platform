#!/usr/bin/env python3
"""Simulate continuous laps at Wanneroo Raceway and publish GPS to MQTT."""

import argparse
import json
import math
import os
import random
import signal
import sys
import time
from typing import List, Tuple

import paho.mqtt.client as mqtt

DEFAULT_TRACK_FILE = os.path.join(os.path.dirname(__file__), "wanneroo_main_loop.json")


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dlat = p2 - p1
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2.0) ** 2
    return 2.0 * r * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlon)
    b = math.degrees(math.atan2(y, x))
    return (b + 360.0) % 360.0


def build_segments(points: List[Tuple[float, float]]):
    segs = []
    total = 0.0
    for i in range(len(points)):
        lat1, lon1 = points[i]
        lat2, lon2 = points[(i + 1) % len(points)]
        dist = haversine_m(lat1, lon1, lat2, lon2)
        segs.append((lat1, lon1, lat2, lon2, dist, total))
        total += dist
    return segs, total


def interpolate_position(segments, lap_len_m: float, distance_m: float):
    d = distance_m % lap_len_m
    for lat1, lon1, lat2, lon2, seg_len, seg_start in segments:
        seg_end = seg_start + seg_len
        if d <= seg_end:
            t = 0.0 if seg_len == 0 else (d - seg_start) / seg_len
            lat = lat1 + t * (lat2 - lat1)
            lon = lon1 + t * (lon2 - lon1)
            track = bearing_deg(lat1, lon1, lat2, lon2)
            return lat, lon, track
    lat1, lon1, lat2, lon2, _, _ = segments[-1]
    return lat2, lon2, bearing_deg(lat1, lon1, lat2, lon2)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Wanneroo Raceway GPS simulator -> MQTT")
    p.add_argument("--mqtt-host", default="127.0.0.1")
    p.add_argument("--mqtt-port", type=int, default=1883)
    p.add_argument("--mqtt-topic", default="subaru/gps")
    p.add_argument("--qos", type=int, default=0, choices=(0, 1, 2))
    p.add_argument("--retain", action="store_true")
    p.add_argument("--rate-hz", type=float, default=5.0, help="Publish frequency")
    p.add_argument("--speed-mps", type=float, default=38.0, help="Car speed along track")
    p.add_argument(
        "--split-variation-pct",
        type=float,
        default=15.0,
        help="Per-split speed variation (+/- percent from base speed)",
    )
    p.add_argument(
        "--random-seed",
        type=int,
        default=42,
        help="Seed for deterministic split speed variation",
    )
    p.add_argument("--alt-m", type=float, default=42.0)
    p.add_argument("--fixq", type=int, default=2)
    p.add_argument("--sats", type=int, default=14)
    p.add_argument("--hdop", type=float, default=0.7)
    p.add_argument(
        "--track-file",
        default=DEFAULT_TRACK_FILE,
        help="JSON track file with {\"points\": [{\"lat\":..,\"lon\":..}, ...]}",
    )
    return p


def load_track_points(path: str) -> List[Tuple[float, float]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    pts = data.get("points")
    if not isinstance(pts, list) or len(pts) < 4:
        raise ValueError("track file must contain at least 4 points")
    out: List[Tuple[float, float]] = []
    for p in pts:
        out.append((float(p["lat"]), float(p["lon"])))
    return out


def split_index_for_distance(lap_d: float, lap_len_m: float) -> int:
    third = lap_len_m / 3.0
    if lap_d < third:
        return 0
    if lap_d < (2.0 * third):
        return 1
    return 2


def build_split_multipliers(rng: random.Random, variation_frac: float) -> List[float]:
    if variation_frac <= 0:
        return [1.0, 1.0, 1.0]
    return [rng.uniform(1.0 - variation_frac, 1.0 + variation_frac) for _ in range(3)]


def main() -> int:
    args = build_parser().parse_args()
    if args.rate_hz <= 0:
        print("rate-hz must be > 0", file=sys.stderr)
        return 1
    if args.speed_mps <= 0:
        print("speed-mps must be > 0", file=sys.stderr)
        return 1
    if args.split_variation_pct < 0:
        print("split-variation-pct must be >= 0", file=sys.stderr)
        return 1

    try:
        track_points = load_track_points(args.track_file)
    except Exception as exc:
        print(f"Track load failed ({args.track_file}): {exc}", file=sys.stderr)
        return 1

    segments, lap_len_m = build_segments(track_points)
    period = 1.0 / args.rate_hz

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    try:
        client.connect(args.mqtt_host, args.mqtt_port, keepalive=20)
    except Exception as exc:
        print(f"MQTT connect failed: {exc}", file=sys.stderr)
        return 2
    client.loop_start()

    running = True

    def stop(_sig, _frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    start_t = time.monotonic()
    last_t = start_t
    tick = 0
    dist = 0.0
    variation_frac = args.split_variation_pct / 100.0
    rng = random.Random(args.random_seed)
    split_multipliers = build_split_multipliers(rng, variation_frac)
    lap_idx = 0

    try:
        while running:
            now_mono = time.monotonic()
            dt = max(now_mono - last_t, 0.0)
            last_t = now_mono

            lap_d_before = dist % lap_len_m
            split_idx = split_index_for_distance(lap_d_before, lap_len_m)
            speed_mps = args.speed_mps * split_multipliers[split_idx]
            dist += dt * speed_mps

            new_lap_idx = int(dist // lap_len_m)
            if new_lap_idx > lap_idx:
                lap_idx = new_lap_idx
                split_multipliers = build_split_multipliers(rng, variation_frac)

            lat, lon, track = interpolate_position(segments, lap_len_m, dist)

            payload = {
                "ts_ns": time.time_ns(),
                "lat": round(lat, 7),
                "lon": round(lon, 7),
                "fixq": args.fixq,
                "sats": args.sats,
                "hdop": args.hdop,
                "alt_m": args.alt_m,
                "speed_mps": round(speed_mps, 3),
                "track_deg": round(track, 2),
                "sim": True,
                "sim_track": "wanneroo",
                "lap_distance_m": round(dist % lap_len_m, 2),
                "lap_length_m": round(lap_len_m, 2),
                "split_idx": split_idx + 1,
                "split_speed_mult": round(split_multipliers[split_idx], 4),
            }

            data = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
            result = client.publish(args.mqtt_topic, data, qos=args.qos, retain=args.retain)
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                print(f"MQTT publish failed rc={result.rc}", file=sys.stderr)

            tick += 1
            next_t = start_t + tick * period
            sleep_s = next_t - time.monotonic()
            if sleep_s > 0:
                time.sleep(sleep_s)

    finally:
        client.loop_stop()
        client.disconnect()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
