#!/usr/bin/env python3
"""Race HUD map server: right-half display + live splits/lap timing from MQTT GPS."""

import argparse
import json
import math
import os
import threading
import time
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import paho.mqtt.client as mqtt

DEFAULT_TRACK_FILE = os.path.join(os.path.dirname(__file__), "wanneroo_main_loop.json")
DEFAULT_RECORDS_FILE = os.path.join(os.path.dirname(__file__), "lap_records.json")
DRIVER_NAMES = ["Beerens", "Frenchy", "Dave", "Noah", "Stig"]

INDEX_HTML = """<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Subaru Race HUD</title>
  <link rel=\"preconnect\" href=\"https://fonts.googleapis.com\"> 
  <link rel=\"preconnect\" href=\"https://fonts.gstatic.com\" crossorigin>
  <link href=\"https://fonts.googleapis.com/css2?family=Rajdhani:wght@500;600;700&family=Orbitron:wght@500;700&display=swap\" rel=\"stylesheet\">
  <link rel=\"stylesheet\" href=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.css\" integrity=\"sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=\" crossorigin=\"\"/>
  <style>
    :root {
      --shell: #06090f;
      --panel: #0f1621;
      --card: #182434;
      --accent: #ff6a21;
      --accent2: #00d2c3;
      --text: #edf3ff;
      --muted: #9aa9c2;
    }
    html, body {
      margin: 0;
      height: 100%;
      background: #000205;
      color: var(--text);
      overflow: hidden;
    }
    #hud {
      position: fixed;
      top: 0;
      right: 0;
      width: min(50vw, 410px);
      min-width: 392px;
      height: 100vh;
      display: grid;
      grid-template-rows: 50% 50%;
      border-left: 2px solid rgba(255,255,255,0.12);
      background: linear-gradient(180deg, #111a27, #0b111a 64%);
      box-shadow: -10px 0 28px rgba(0,0,0,0.55);
    }
    #map { height: 100%; width: 100%; }
    #timing {
      padding: 7px;
      display: grid;
      grid-template-columns: 1fr 1fr;
      grid-auto-rows: minmax(28px, auto);
      gap: 4px;
      background: linear-gradient(180deg, rgba(4,8,13,0.9), rgba(8,12,18,0.98));
      border-top: 1px solid rgba(255,255,255,0.1);
    }
    .card {
      background: linear-gradient(135deg, rgba(255,95,31,0.12), rgba(0,210,195,0.08));
      border: 1px solid rgba(255,255,255,0.12);
      border-radius: 8px;
      padding: 5px 7px;
    }
    .title {
      font-family: Rajdhani, sans-serif;
      font-size: 10px;
      letter-spacing: 1px;
      text-transform: uppercase;
      color: var(--muted);
    }
    .value {
      font-family: Orbitron, monospace;
      font-size: 13px;
      line-height: 1.1;
      color: var(--text);
    }
    .small {
      font-family: Rajdhani, sans-serif;
      font-size: 10px;
      color: var(--muted);
    }
    #header {
      position: absolute;
      z-index: 1200;
      top: 6px;
      left: 6px;
      right: 6px;
      background: rgba(7, 11, 17, 0.9);
      border: 1px solid rgba(255,255,255,0.15);
      border-radius: 8px;
      padding: 6px 9px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-family: Rajdhani, sans-serif;
      letter-spacing: 0.5px;
    }
    #headerRight {
      display: flex;
      align-items: center;
      gap: 6px;
    }
    #driverSelect, #resetBtn {
      height: 26px;
      border-radius: 7px;
      border: 1px solid rgba(255,255,255,0.2);
      background: #0d1522;
      color: var(--text);
      font-family: Rajdhani, sans-serif;
      font-size: 12px;
      padding: 0 8px;
    }
    #driverSelect { min-width: 92px; }
    #resetBtn {
      cursor: pointer;
      background: linear-gradient(135deg, rgba(255,95,31,0.2), rgba(255,95,31,0.35));
    }
    #title { font-size: 14px; font-weight: 700; color: var(--accent); }
    #sub { font-size: 10px; color: var(--muted); }
    .span2 { grid-column: span 2; }
    #splitDeltaCard {
      display: flex;
      align-items: center;
      justify-content: center;
      text-align: center;
      min-height: 34px;
      border: 2px solid rgba(255,255,255,0.2);
    }
    #splitDelta {
      font-family: Orbitron, monospace;
      font-size: 20px;
      line-height: 1;
      letter-spacing: 1px;
      font-weight: 700;
    }
    .ahead { color: #38f39a; }
    .behind { color: #ff7b7b; }
    .neutral { color: var(--text); }
    @media (max-width: 520px) {
      #hud {
        width: 100vw;
        min-width: 0;
      }
    }
  </style>
</head>
<body>
  <div id=\"hud\">
    <div style=\"position:relative\">
      <div id=\"header\">
        <div>
          <div id=\"title\">WANNEROO RACE HUD</div>
          <div id=\"sub\">Waiting for GPS...</div>
        </div>
        <div id=\"headerRight\">
          <select id=\"driverSelect\">
            <option>Beerens</option>
            <option>Frenchy</option>
            <option>Dave</option>
            <option>Noah</option>
            <option>Stig</option>
          </select>
          <button id=\"resetBtn\" type=\"button\">Reset Session</button>
        </div>
      </div>
      <div id=\"map\"></div>
    </div>
    <div id=\"timing\">
      <div id=\"splitDeltaCard\" class=\"card span2\">
        <div>
          <div id=\"splitDeltaTitle\" class=\"title\">Current Split Delta</div>
          <div id=\"splitDelta\" class=\"neutral\">--.---</div>
        </div>
      </div>
      <div class=\"card\"><div class=\"title\">Current Lap</div><div id=\"currLap\" class=\"value\">--:--.---</div></div>
      <div class=\"card\"><div class=\"title\">Last Lap</div><div id=\"lastLap\" class=\"value\">--:--.---</div></div>
      <div class=\"card\"><div class=\"title\">Best Lap</div><div id=\"bestLap\" class=\"value\">--:--.---</div></div>
      <div class=\"card\"><div class=\"title\">Lap Count</div><div id=\"lapCount\" class=\"value\">0</div></div>
      <div class=\"card\"><div class=\"title\">Session</div><div id=\"sessionId\" class=\"value\">1</div></div>
      <div class=\"card\"><div class=\"title\">Split 1 (Curr / Last)</div><div id=\"s1\" class=\"value\">--.--- / --.---</div></div>
      <div class=\"card\"><div class=\"title\">Split 2 (Curr / Last)</div><div id=\"s2\" class=\"value\">--.--- / --.---</div></div>
      <div class=\"card\"><div class=\"title\">Split 3 (Curr / Last)</div><div id=\"s3\" class=\"value\">--.--- / --.---</div></div>
      <div class=\"card\"><div class=\"title\">Position</div><div id=\"pos\" class=\"small\">lat=-- lon=--</div><div id=\"meta\" class=\"small\">track=-- speed=--</div></div>
    </div>
  </div>

  <script src=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.js\" integrity=\"sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=\" crossorigin=\"\"></script>
  <script>
    const fmtLap = (sec) => {
      if (typeof sec !== 'number' || !isFinite(sec) || sec <= 0) return '--:--.---';
      const m = Math.floor(sec / 60);
      const s = sec - m * 60;
      return `${String(m).padStart(2,'0')}:${s.toFixed(3).padStart(6,'0')}`;
    };
    const fmtSplit = (sec) => {
      if (typeof sec !== 'number' || !isFinite(sec) || sec <= 0) return '--.---';
      return sec.toFixed(3);
    };
    const fmtDelta = (sec) => {
      if (typeof sec !== 'number' || !isFinite(sec)) return '--.---';
      return `${Math.abs(sec).toFixed(3)}s ${sec <= 0 ? 'AHEAD' : 'BEHIND'}`;
    };

    let map, carMarker, trail, lastSeq = -1;
    let suppressDriverChangeEvent = false;

    async function initMap() {
      const metaResp = await fetch('/meta', { cache: 'no-store' });
      const meta = await metaResp.json();

      const center = meta.center || [-31.6654, 115.7895];
      map = L.map('map', { zoomControl: false }).setView(center, 16.8);
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 20,
        attribution: '&copy; OpenStreetMap contributors'
      }).addTo(map);

      if (Array.isArray(meta.track_points) && meta.track_points.length > 1) {
        L.polyline(meta.track_points, { color: '#7ec8ff', weight: 4, opacity: 0.9 }).addTo(map);
      }

      if (meta.start_point) {
        L.circleMarker(meta.start_point, { radius: 8, color: '#00ff99', fillColor: '#00cc77', fillOpacity: 0.95 }).addTo(map).bindTooltip('START', {permanent:true, direction:'right'});
      }
      if (Array.isArray(meta.split_points)) {
        meta.split_points.forEach((p, idx) => {
          L.circleMarker(p, { radius: 7, color: '#ffb703', fillColor: '#ff9f1c', fillOpacity: 0.95 }).addTo(map).bindTooltip(`S${idx+1}`, {permanent:true, direction:'right'});
        });
      }

      carMarker = L.circleMarker(center, {
        radius: 8,
        color: '#ff2d55',
        fillColor: '#ff5f1f',
        fillOpacity: 1.0,
      }).addTo(map);

      trail = L.polyline([], { color: '#00d2c3', weight: 3, opacity: 0.85 }).addTo(map);
    }

    function updateTiming(t) {
      if (!t) return;
      document.getElementById('currLap').textContent = fmtLap(t.current_lap_sec);
      document.getElementById('lastLap').textContent = fmtLap(t.last_lap_sec);
      document.getElementById('bestLap').textContent = fmtLap(t.best_lap_sec);
      document.getElementById('lapCount').textContent = String(t.lap_count ?? 0);
      document.getElementById('sessionId').textContent = String(t.session_id ?? 1);

      const cs = t.current_splits_sec || [];
      const ls = t.last_splits_sec || [];
      document.getElementById('s1').textContent = `${fmtSplit(cs[0])} / ${fmtSplit(ls[0])}`;
      document.getElementById('s2').textContent = `${fmtSplit(cs[1])} / ${fmtSplit(ls[1])}`;
      document.getElementById('s3').textContent = `${fmtSplit(cs[2])} / ${fmtSplit(ls[2])}`;

      const el = document.getElementById('splitDelta');
      const title = document.getElementById('splitDeltaTitle');
      const delta = t.split_delta_sec;
      const splitIdx = Number.isFinite(t.current_split_idx) ? t.current_split_idx : 0;
      title.textContent = splitIdx > 0 ? `Current Split Delta (S${splitIdx})` : 'Current Split Delta';
      el.classList.remove('ahead', 'behind', 'neutral');
      if (typeof delta === 'number' && isFinite(delta)) {
        el.textContent = fmtDelta(delta);
        el.classList.add(delta <= 0 ? 'ahead' : 'behind');
      } else {
        el.textContent = '--.---';
        el.classList.add('neutral');
      }
    }

    async function setDriver(driver) {
      await fetch('/driver', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ driver }),
      });
    }

    async function resetSession() {
      await fetch('/reset-session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
    }

    function initControls() {
      const select = document.getElementById('driverSelect');
      const resetBtn = document.getElementById('resetBtn');
      select.addEventListener('change', async () => {
        if (suppressDriverChangeEvent) return;
        try { await setDriver(select.value); } catch (_) {}
      });
      resetBtn.addEventListener('click', async () => {
        if (!confirm(`Reset session for ${select.value}?`)) return;
        try { await resetSession(); } catch (_) {}
      });
    }

    async function poll() {
      try {
        const r = await fetch('/latest', { cache: 'no-store' });
        if (!r.ok) return;
        const data = await r.json();
        const p = data.latest;
        document.getElementById('sub').textContent = `driver ${data.driver} | topic ${data.topic} | fixq ${p?.fixq ?? '-'} | sats ${p?.sats ?? '-'}`;
        const select = document.getElementById('driverSelect');
        if (select && data.driver && select.value !== data.driver) {
          suppressDriverChangeEvent = true;
          select.value = data.driver;
          suppressDriverChangeEvent = false;
        }

        if (p && data.seq !== lastSeq) {
          lastSeq = data.seq;
          const latlng = [p.lat, p.lon];
          carMarker.setLatLng(latlng);
          map.panTo(latlng, { animate: false });

          if (Array.isArray(data.history)) {
            trail.setLatLngs(data.history.map(x => [x.lat, x.lon]));
          }

          document.getElementById('pos').textContent = `lat=${p.lat.toFixed(7)} lon=${p.lon.toFixed(7)}`;
          const speed = (typeof p.speed_mps === 'number') ? p.speed_mps.toFixed(2) : '--';
          const dist = (typeof p.track_error_m === 'number') ? p.track_error_m.toFixed(1) : '--';
          const lapd = (typeof p.lap_distance_m === 'number') ? p.lap_distance_m.toFixed(1) : '--';
          document.getElementById('meta').textContent = `speed=${speed}m/s lapDist=${lapd}m err=${dist}m`;
        }

        updateTiming(data.timing);
      } catch (_) {}
    }

    initMap().then(() => {
      initControls();
      setInterval(poll, 100);
      poll();
    });
  </script>
</body>
</html>
"""


class TrackGeometry:
    def __init__(self, points: List[Tuple[float, float]]):
        if len(points) < 4:
            raise ValueError("track needs at least 4 points")

        if points[0] != points[-1]:
            points = points + [points[0]]

        self.points = points
        self.lat0 = sum(p[0] for p in points) / len(points)
        self.m_per_deg_lat = 111132.92
        self.m_per_deg_lon = 111412.84 * math.cos(math.radians(self.lat0))

        self.xy = [self.to_xy(lat, lon) for lat, lon in points]
        self.cum_len: List[float] = [0.0]
        self.seg_len: List[float] = []
        total = 0.0
        for i in range(len(points) - 1):
            x1, y1 = self.xy[i]
            x2, y2 = self.xy[i + 1]
            d = math.hypot(x2 - x1, y2 - y1)
            self.seg_len.append(d)
            total += d
            self.cum_len.append(total)
        self.total_len_m = total

    def to_xy(self, lat: float, lon: float) -> Tuple[float, float]:
        x = lon * self.m_per_deg_lon
        y = lat * self.m_per_deg_lat
        return x, y

    def point_at_s(self, s: float) -> Tuple[float, float]:
        d = s % self.total_len_m
        for i in range(len(self.seg_len)):
            start = self.cum_len[i]
            end = self.cum_len[i + 1]
            if d <= end:
                t = 0.0 if self.seg_len[i] == 0 else (d - start) / self.seg_len[i]
                lat1, lon1 = self.points[i]
                lat2, lon2 = self.points[i + 1]
                return (lat1 + (lat2 - lat1) * t, lon1 + (lon2 - lon1) * t)
        return self.points[-1]

    def project(self, lat: float, lon: float, hint_seg_idx: int) -> Tuple[float, int, float]:
        px, py = self.to_xy(lat, lon)
        n = len(self.seg_len)
        idxs = [(hint_seg_idx + k) % n for k in range(-8, 9)]
        best = None
        best_idx = 0
        best_t = 0.0
        for i in idxs:
            x1, y1 = self.xy[i]
            x2, y2 = self.xy[i + 1]
            vx, vy = (x2 - x1), (y2 - y1)
            seg2 = vx * vx + vy * vy
            if seg2 <= 1e-9:
                t = 0.0
            else:
                t = ((px - x1) * vx + (py - y1) * vy) / seg2
                t = max(0.0, min(1.0, t))
            cx = x1 + t * vx
            cy = y1 + t * vy
            d2 = (px - cx) ** 2 + (py - cy) ** 2
            if best is None or d2 < best:
                best = d2
                best_idx = i
                best_t = t

        s = self.cum_len[best_idx] + best_t * self.seg_len[best_idx]
        err_m = math.sqrt(best) if best is not None else 0.0
        return s, best_idx, err_m


class LapTiming:
    def __init__(self, lap_len_m: float):
        self.lap_len_m = lap_len_m
        self.split_dist = [lap_len_m / 3.0, (2.0 * lap_len_m) / 3.0, lap_len_m]
        self.lap_count = 0
        self.armed = False
        self.lap_start_ts = 0.0
        self.lap_progress_m = 0.0
        self.current_splits: List[Optional[float]] = [None, None, None]
        self.last_splits: List[Optional[float]] = [None, None, None]
        self.best_splits: List[Optional[float]] = [None, None, None]
        self.best_split_segments: List[Optional[float]] = [None, None, None]
        self.last_lap: Optional[float] = None
        self.best_lap: Optional[float] = None
        self.prev_s: Optional[float] = None

    def apply_benchmarks(self, best_lap: Optional[float], best_splits: List[Optional[float]], best_segments: List[Optional[float]]):
        self.best_lap = best_lap
        self.best_splits = best_splits[:3] + [None] * max(0, 3 - len(best_splits))
        self.best_splits = self.best_splits[:3]
        self.best_split_segments = best_segments[:3] + [None] * max(0, 3 - len(best_segments))
        self.best_split_segments = self.best_split_segments[:3]

    def update(self, ts_sec: float, s_m: float) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        if self.prev_s is None:
            self.prev_s = s_m
            return self.snapshot(ts_sec), None

        ds = s_m - self.prev_s
        if ds < -(self.lap_len_m * 0.5):
            ds += self.lap_len_m
        elif ds > (self.lap_len_m * 0.5):
            ds -= self.lap_len_m
        self.prev_s = s_m

        if not self.armed:
            if ds > 0 and (s_m < self.lap_len_m * 0.12):
                self.armed = True
                self.lap_start_ts = ts_sec
                self.lap_progress_m = 0.0
                self.current_splits = [None, None, None]
            return self.snapshot(ts_sec), None

        self.lap_progress_m += max(ds, 0.0)
        elapsed = ts_sec - self.lap_start_ts

        for i in range(3):
            if self.current_splits[i] is None and self.lap_progress_m >= self.split_dist[i]:
                self.current_splits[i] = elapsed

        completed_lap = None
        if self.lap_progress_m >= self.lap_len_m:
            lap_time = elapsed
            self.last_lap = lap_time
            if self.best_lap is None or lap_time < self.best_lap:
                self.best_lap = lap_time
            self.lap_count += 1
            self.last_splits = self.current_splits[:]
            segments = self._split_segments(self.last_splits)
            for i, split_time in enumerate(self.last_splits):
                if split_time is None:
                    continue
                if self.best_splits[i] is None or split_time < self.best_splits[i]:
                    self.best_splits[i] = split_time
                segment_time = segments[i]
                if segment_time is not None and (self.best_split_segments[i] is None or segment_time < self.best_split_segments[i]):
                    self.best_split_segments[i] = segment_time
            completed_lap = {
                "lap_number": self.lap_count,
                "lap_time_sec": lap_time,
                "splits_sec": self.last_splits[:],
                "completed_at_sec": ts_sec,
            }

            self.lap_start_ts = ts_sec
            self.lap_progress_m = max(self.lap_progress_m - self.lap_len_m, 0.0)
            self.current_splits = [None, None, None]

        return self.snapshot(ts_sec), completed_lap

    def snapshot(self, ts_sec: float) -> Dict:
        current_lap = None
        if self.armed and self.lap_start_ts > 0:
            current_lap = max(ts_sec - self.lap_start_ts, 0.0)

        current_split_idx = 0
        if self.armed:
            if self.lap_progress_m < self.split_dist[0]:
                current_split_idx = 1
            elif self.lap_progress_m < self.split_dist[1]:
                current_split_idx = 2
            else:
                current_split_idx = 3

        delta_sec = None
        if self.armed and current_lap is not None and current_split_idx > 0:
            if current_split_idx == 1:
                current_segment = current_lap
            elif current_split_idx == 2:
                split1 = self.current_splits[0]
                current_segment = (current_lap - split1) if split1 is not None else None
            else:
                split2 = self.current_splits[1]
                current_segment = (current_lap - split2) if split2 is not None else None
            best_segment = self.best_split_segments[current_split_idx - 1]
            if current_segment is not None and best_segment is not None:
                delta_sec = current_segment - best_segment

        return {
            "lap_count": self.lap_count,
            "current_lap_sec": current_lap,
            "last_lap_sec": self.last_lap,
            "best_lap_sec": self.best_lap,
            "current_splits_sec": self.current_splits[:],
            "last_splits_sec": self.last_splits[:],
            "best_splits_sec": self.best_splits[:],
            "best_split_segments_sec": self.best_split_segments[:],
            "current_split_idx": current_split_idx,
            "split_delta_sec": delta_sec,
            "lap_progress_m": self.lap_progress_m if self.armed else None,
        }

    @staticmethod
    def _split_segments(cumulative_splits: List[Optional[float]]) -> List[Optional[float]]:
        s1, s2, s3 = cumulative_splits
        seg1 = s1
        seg2 = (s2 - s1) if s1 is not None and s2 is not None else None
        seg3 = (s3 - s2) if s2 is not None and s3 is not None else None
        return [seg1, seg2, seg3]


class DriverRecordsStore:
    def __init__(self, path: str, drivers: List[str]):
        self.path = Path(path)
        self.drivers = drivers
        self.data = self._load()

    def _empty_driver(self) -> Dict[str, Any]:
        return {"current_session_id": 1, "sessions": {"1": {"laps": [], "created_at_sec": time.time()}}}

    def _load(self) -> Dict[str, Any]:
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    drivers = loaded.get("drivers", {})
                    if isinstance(drivers, dict):
                        for name in self.drivers:
                            if name not in drivers or not isinstance(drivers[name], dict):
                                drivers[name] = self._empty_driver()
                        loaded["drivers"] = drivers
                        return loaded
            except Exception:
                pass
        return {"drivers": {name: self._empty_driver() for name in self.drivers}}

    def persist(self):
        payload = json.dumps(self.data, indent=2, sort_keys=True)
        self.path.write_text(payload, encoding="utf-8")

    def ensure_driver(self, driver: str):
        drivers = self.data.setdefault("drivers", {})
        if driver not in drivers or not isinstance(drivers[driver], dict):
            drivers[driver] = self._empty_driver()

    def current_session_id(self, driver: str) -> int:
        self.ensure_driver(driver)
        return int(self.data["drivers"][driver].get("current_session_id", 1))

    def add_lap(self, driver: str, lap: Dict[str, Any]):
        self.ensure_driver(driver)
        driver_data = self.data["drivers"][driver]
        session_id = str(self.current_session_id(driver))
        sessions = driver_data.setdefault("sessions", {})
        if session_id not in sessions or not isinstance(sessions[session_id], dict):
            sessions[session_id] = {"laps": [], "created_at_sec": time.time()}
        sessions[session_id].setdefault("laps", []).append(lap)
        self.persist()

    def reset_session(self, driver: str) -> int:
        self.ensure_driver(driver)
        driver_data = self.data["drivers"][driver]
        next_id = int(driver_data.get("current_session_id", 1)) + 1
        driver_data["current_session_id"] = next_id
        sessions = driver_data.setdefault("sessions", {})
        sessions[str(next_id)] = {"laps": [], "created_at_sec": time.time()}
        self.persist()
        return next_id

    def driver_records(self, driver: str) -> Dict[str, Any]:
        self.ensure_driver(driver)
        return self.data["drivers"][driver]

    def driver_benchmarks(self, driver: str) -> Dict[str, Any]:
        self.ensure_driver(driver)
        driver_data = self.data["drivers"][driver]
        sessions = driver_data.get("sessions", {})
        laps: List[Dict[str, Any]] = []
        if isinstance(sessions, dict):
            for session in sessions.values():
                if isinstance(session, dict):
                    rows = session.get("laps", [])
                    if isinstance(rows, list):
                        laps.extend([row for row in rows if isinstance(row, dict)])

        best_lap: Optional[float] = None
        best_splits: List[Optional[float]] = [None, None, None]
        best_segments: List[Optional[float]] = [None, None, None]

        for lap in laps:
            lap_time = lap.get("lap_time_sec")
            if isinstance(lap_time, (int, float)) and lap_time > 0:
                if best_lap is None or lap_time < best_lap:
                    best_lap = float(lap_time)

            splits = lap.get("splits_sec")
            if isinstance(splits, list):
                safe_splits: List[Optional[float]] = []
                for idx in range(3):
                    val = splits[idx] if idx < len(splits) else None
                    if isinstance(val, (int, float)) and val > 0:
                        sval = float(val)
                        safe_splits.append(sval)
                        if best_splits[idx] is None or sval < best_splits[idx]:
                            best_splits[idx] = sval
                    else:
                        safe_splits.append(None)

                segments = LapTiming._split_segments(safe_splits)
                for idx, seg in enumerate(segments):
                    if seg is not None and (best_segments[idx] is None or seg < best_segments[idx]):
                        best_segments[idx] = seg

        return {
            "best_lap_sec": best_lap,
            "best_splits_sec": best_splits,
            "best_split_segments_sec": best_segments,
        }


class SharedState:
    def __init__(self, topic: str, history_size: int, track: TrackGeometry, records_file: str):
        self.topic = topic
        self.lock = threading.Lock()
        self.latest: Optional[Dict] = None
        self.seq = 0
        self.history: Deque[Dict] = deque(maxlen=history_size)
        self.track = track
        self.drivers = DRIVER_NAMES[:]
        self.active_driver = self.drivers[0]
        self.records = DriverRecordsStore(records_file, self.drivers)
        self.timings: Dict[str, LapTiming] = {name: self._new_timing_for_driver(name) for name in self.drivers}
        self.last_seg_idx = 0

    def _new_timing_for_driver(self, driver: str) -> LapTiming:
        timing = LapTiming(self.track.total_len_m)
        bench = self.records.driver_benchmarks(driver)
        timing.apply_benchmarks(
            bench.get("best_lap_sec"),
            bench.get("best_splits_sec", [None, None, None]),
            bench.get("best_split_segments_sec", [None, None, None]),
        )
        return timing

    def update(self, payload: Dict):
        lat = float(payload["lat"])
        lon = float(payload["lon"])
        s_m, self.last_seg_idx, err_m = self.track.project(lat, lon, self.last_seg_idx)

        ts_ns = payload.get("ts_ns")
        if isinstance(ts_ns, int) and ts_ns > 0:
            ts_sec = ts_ns / 1_000_000_000.0
        else:
            ts_sec = time.time()

        with self.lock:
            driver = self.active_driver
            timing_state = self.timings[driver]
            timing, completed_lap = timing_state.update(ts_sec, s_m)
            if completed_lap:
                lap_entry = dict(completed_lap)
                lap_entry["driver"] = driver
                lap_entry["session_id"] = self.records.current_session_id(driver)
                self.records.add_lap(driver, lap_entry)

            row = dict(payload)
            row["track_s_m"] = s_m
            row["lap_distance_m"] = timing_state.lap_progress_m if timing_state.armed else None
            row["track_error_m"] = err_m
            row["driver"] = driver

            self.latest = row
            self.seq += 1
            self.history.append({"lat": lat, "lon": lon})
            self._timing_snapshot = timing

    def snapshot(self):
        with self.lock:
            latest = self.latest.copy() if self.latest else None
            history = list(self.history)
            seq = self.seq
            active_driver = self.active_driver
            timing_state = self.timings[active_driver]
            timing = dict(self._timing_snapshot) if hasattr(self, "_timing_snapshot") else timing_state.snapshot(time.time())
            timing["session_id"] = self.records.current_session_id(active_driver)
        return {
            "topic": self.topic,
            "seq": seq,
            "latest": latest,
            "history": history,
            "timing": timing,
            "driver": active_driver,
            "drivers": self.drivers,
        }

    def set_driver(self, driver: str):
        if driver not in self.drivers:
            raise ValueError(f"unknown driver: {driver}")
        with self.lock:
            self.active_driver = driver
            self._timing_snapshot = self.timings[driver].snapshot(time.time())

    def reset_active_driver_session(self) -> Dict[str, Any]:
        with self.lock:
            driver = self.active_driver
            new_session_id = self.records.reset_session(driver)
            self.timings[driver] = self._new_timing_for_driver(driver)
            self._timing_snapshot = self.timings[driver].snapshot(time.time())
            return {"driver": driver, "session_id": new_session_id}

    def get_records(self, driver: str) -> Dict[str, Any]:
        if driver not in self.drivers:
            raise ValueError(f"unknown driver: {driver}")
        with self.lock:
            data = self.records.driver_records(driver)
            return {
                "driver": driver,
                "current_session_id": data.get("current_session_id", 1),
                "sessions": data.get("sessions", {}),
            }

    def meta(self):
        splits = [
            self.track.point_at_s(self.track.total_len_m / 3.0),
            self.track.point_at_s((2.0 * self.track.total_len_m) / 3.0),
            self.track.point_at_s(self.track.total_len_m),
        ]
        center = self.track.point_at_s(self.track.total_len_m / 2.0)
        return {
            "track_points": [[lat, lon] for (lat, lon) in self.track.points],
            "start_point": [self.track.points[0][0], self.track.points[0][1]],
            "split_points": [[lat, lon] for (lat, lon) in splits],
            "lap_length_m": self.track.total_len_m,
            "center": [center[0], center[1]],
        }


def load_track(path: str) -> TrackGeometry:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    points = data.get("points")
    if not isinstance(points, list):
        raise ValueError("track file missing 'points' list")
    parsed = [(float(p["lat"]), float(p["lon"])) for p in points]
    return TrackGeometry(parsed)


def build_handler(state: SharedState):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, content: bytes, content_type: str):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path

            if path in ("/", "/index.html"):
                self._send(HTTPStatus.OK, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
                return

            if path == "/latest":
                data = json.dumps(state.snapshot(), separators=(",", ":")).encode("utf-8")
                self._send(HTTPStatus.OK, data, "application/json")
                return

            if path == "/meta":
                data = json.dumps(state.meta(), separators=(",", ":")).encode("utf-8")
                self._send(HTTPStatus.OK, data, "application/json")
                return

            if path == "/records":
                params = parse_qs(parsed.query)
                driver = params.get("driver", [state.active_driver])[0]
                try:
                    data = json.dumps(state.get_records(driver), separators=(",", ":")).encode("utf-8")
                    self._send(HTTPStatus.OK, data, "application/json")
                except Exception as exc:
                    self._send(HTTPStatus.BAD_REQUEST, str(exc).encode("utf-8"), "text/plain; charset=utf-8")
                return

            self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain; charset=utf-8")

        def do_POST(self):
            parsed = urlparse(self.path)
            path = parsed.path
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8", errors="ignore"))
                if not isinstance(payload, dict):
                    payload = {}
            except Exception:
                payload = {}

            if path == "/driver":
                driver = payload.get("driver")
                if not isinstance(driver, str):
                    self._send(HTTPStatus.BAD_REQUEST, b"missing driver", "text/plain; charset=utf-8")
                    return
                try:
                    state.set_driver(driver)
                except Exception as exc:
                    self._send(HTTPStatus.BAD_REQUEST, str(exc).encode("utf-8"), "text/plain; charset=utf-8")
                    return
                data = json.dumps({"ok": True, "driver": driver}, separators=(",", ":")).encode("utf-8")
                self._send(HTTPStatus.OK, data, "application/json")
                return

            if path == "/reset-session":
                data = json.dumps(state.reset_active_driver_session(), separators=(",", ":")).encode("utf-8")
                self._send(HTTPStatus.OK, data, "application/json")
                return

            self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain; charset=utf-8")

        def log_message(self, fmt, *args):
            return

    return Handler


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Race HUD MQTT GPS map server")
    p.add_argument("--mqtt-host", default="127.0.0.1")
    p.add_argument("--mqtt-port", type=int, default=1883)
    p.add_argument("--mqtt-topic", default="subaru/gps")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8090)
    p.add_argument("--history-size", type=int, default=1800)
    p.add_argument("--track-file", default=DEFAULT_TRACK_FILE)
    p.add_argument("--records-file", default=DEFAULT_RECORDS_FILE)
    return p


def main() -> int:
    args = build_parser().parse_args()

    try:
        track = load_track(args.track_file)
    except Exception as exc:
        print(f"Failed to load track file {args.track_file}: {exc}")
        return 1

    state = SharedState(args.mqtt_topic, args.history_size, track, args.records_file)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

    def on_message(_client, _userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8", errors="ignore"))
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        if "lat" not in payload or "lon" not in payload:
            return
        try:
            state.update(payload)
        except Exception:
            return

    client.on_message = on_message

    client.connect(args.mqtt_host, args.mqtt_port, keepalive=20)
    client.subscribe(args.mqtt_topic, qos=0)
    client.loop_start()

    handler = build_handler(state)
    httpd = ThreadingHTTPServer((args.host, args.port), handler)

    try:
        print(f"Race HUD: http://{args.host}:{args.port}")
        print(f"MQTT source: {args.mqtt_host}:{args.mqtt_port} topic={args.mqtt_topic}")
        print(f"Track file: {args.track_file} ({track.total_len_m:.1f} m)")
        httpd.serve_forever(poll_interval=0.15)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        client.loop_stop()
        client.disconnect()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
