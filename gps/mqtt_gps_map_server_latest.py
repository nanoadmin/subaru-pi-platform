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
from urllib.error import URLError
from urllib.parse import parse_qs, quote_plus, urlparse
from urllib.request import Request, urlopen

import paho.mqtt.client as mqtt

DEFAULT_TRACK_FILE = os.path.join(os.path.dirname(__file__), "wanneroo_main_loop.json")
DEFAULT_RECORDS_FILE = os.path.join(os.path.dirname(__file__), "lap_records.json")
DRIVER_NAMES = ["Beerens", "Frenchy", "Dave", "Noah", "Stig"]
MAX_TRACK_ERROR_M = 120.0
MIN_VALID_LAP_SEC = 20.0

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
      padding: 5px;
      display: grid;
      grid-template-columns: 1fr 1fr;
      grid-auto-rows: minmax(22px, auto);
      gap: 4px;
      background: linear-gradient(180deg, rgba(4,8,13,0.9), rgba(8,12,18,0.98));
      border-top: 1px solid rgba(255,255,255,0.1);
    }
    .card {
      background: linear-gradient(135deg, rgba(255,95,31,0.12), rgba(0,210,195,0.08));
      border: 1px solid rgba(255,255,255,0.12);
      border-radius: 8px;
      padding: 4px 6px;
    }
    .title {
      font-family: Rajdhani, sans-serif;
      font-size: 9px;
      letter-spacing: 1px;
      text-transform: uppercase;
      color: var(--muted);
    }
    .value {
      font-family: Orbitron, monospace;
      font-size: 12px;
      line-height: 1.1;
      color: var(--text);
    }
    .value.main {
      font-size: 16px;
      letter-spacing: 0.4px;
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
      font-size: 9px;
      padding: 0 7px;
    }
    #driverSelect { min-width: 92px; }
    #resetBtn {
      cursor: pointer;
      background: linear-gradient(135deg, rgba(255,95,31,0.2), rgba(255,95,31,0.35));
    }
    #title { font-size: 14px; font-weight: 700; color: var(--accent); }
    #sub { font-size: 9px; color: var(--muted); }
    #currLap { color: #7fd3ff; }
    #lastLap { color: #ffd08a; }
    #bestLap { color: #8bf0b6; }
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
      font-size: 17px;
      line-height: 1;
      letter-spacing: 1px;
      font-weight: 700;
    }
    .ahead { color: #38f39a; }
    .behind { color: #ff7b7b; }
    .neutral { color: var(--text); }
    #splitTable {
      display: grid;
      grid-template-columns: 30px 1fr 1fr;
      gap: 1px 7px;
      align-items: center;
      font-family: Orbitron, monospace;
      font-size: 10px;
      margin-top: 2px;
    }
    .splitHead {
      font-family: Rajdhani, sans-serif;
      font-size: 9px;
      letter-spacing: 0.8px;
      text-transform: uppercase;
      color: var(--muted);
    }
    .leaflet-control-attribution {
      font-size: 8px !important;
      line-height: 1.1 !important;
      padding: 1px 4px !important;
      margin: 0 2px 2px 0 !important;
      color: rgba(190, 204, 223, 0.65) !important;
      background: rgba(9, 14, 22, 0.55) !important;
      border: 1px solid rgba(255,255,255,0.08) !important;
      border-radius: 4px !important;
      backdrop-filter: blur(1px);
    }
    .leaflet-control-attribution a {
      color: rgba(142, 189, 255, 0.72) !important;
      text-decoration: none !important;
    }
    .split-pin {
      width: 18px;
      height: 18px;
      border-radius: 50%;
      background: radial-gradient(circle at 35% 35%, #ffd98f, #ff9f1c 70%);
      border: 1px solid rgba(28, 34, 47, 0.95);
      color: #141a23;
      font-family: Orbitron, monospace;
      font-size: 10px;
      font-weight: 700;
      display: flex;
      align-items: center;
      justify-content: center;
      box-shadow: 0 0 0 1px rgba(255,255,255,0.2), 0 2px 6px rgba(0,0,0,0.5);
    }
    .start-flag {
      position: relative;
      width: 14px;
      height: 14px;
    }
    .start-flag::before {
      content: '';
      position: absolute;
      left: 1px;
      top: 0px;
      width: 2px;
      height: 14px;
      background: #e7ecf3;
      border-radius: 1px;
      box-shadow: 0 0 2px rgba(0,0,0,0.6);
    }
    .start-flag::after {
      content: '';
      position: absolute;
      left: 3px;
      top: 1px;
      width: 10px;
      height: 8px;
      border: 1px solid rgba(17, 23, 31, 0.95);
      border-radius: 1px;
      background:
        linear-gradient(45deg, #f2f5f8 25%, #2b3442 25%, #2b3442 50%, #f2f5f8 50%, #f2f5f8 75%, #2b3442 75%, #2b3442 100%);
      background-size: 6px 6px;
      box-shadow: 0 1px 3px rgba(0,0,0,0.55);
    }
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
      <div class=\"card\"><div class=\"title\">Current Lap</div><div id=\"currLap\" class=\"value main\">--:--.---</div></div>
      <div class=\"card\"><div class=\"title\">Last Lap</div><div id=\"lastLap\" class=\"value main\">--:--.---</div></div>
      <div class=\"card\"><div class=\"title\">Best Lap</div><div id=\"bestLap\" class=\"value main\">--:--.---</div></div>
      <div class=\"card\"><div class=\"title\">Lap Count</div><div id=\"lapCount\" class=\"value\">0</div><div id=\"sessionInline\" class=\"small\">session 1</div></div>
      <div class=\"card span2\">
        <div class=\"title\">Splits</div>
        <div id=\"splitTable\">
          <div></div><div class=\"splitHead\">Current</div><div class=\"splitHead\">Last</div>
          <div>S1</div><div id=\"s1c\">--.---</div><div id=\"s1l\">--.---</div>
          <div>S2</div><div id=\"s2c\">--.---</div><div id=\"s2l\">--.---</div>
          <div>S3</div><div id=\"s3c\">--.---</div><div id=\"s3l\">--.---</div>
        </div>
      </div>
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

    const approxDistM = (a, b) => {
      const dLat = (b.lat - a.lat) * 111132.92;
      const latMid = (a.lat + b.lat) * 0.5 * Math.PI / 180.0;
      const dLon = (b.lon - a.lon) * (111412.84 * Math.cos(latMid));
      return Math.hypot(dLat, dLon);
    };

    const filterTrailTail = (history) => {
      if (!Array.isArray(history) || history.length === 0) return [];
      const maxJumpM = 120.0;
      let startIdx = history.length - 1;
      for (let i = history.length - 1; i > 0; i--) {
        const a = history[i - 1];
        const b = history[i];
        if (!a || !b || !isFinite(a.lat) || !isFinite(a.lon) || !isFinite(b.lat) || !isFinite(b.lon)) {
          startIdx = i;
          break;
        }
        if (approxDistM(a, b) > maxJumpM) {
          startIdx = i;
          break;
        }
      }
      const out = [];
      for (let i = startIdx; i < history.length; i++) {
        const p = history[i];
        if (p && isFinite(p.lat) && isFinite(p.lon)) out.push([p.lat, p.lon]);
      }
      return out;
    };

    let map, carMarker, trail, lastSeq = -1;
    let suppressDriverChangeEvent = false;

    async function initMap() {
      const metaResp = await fetch('/meta', { cache: 'no-store' });
      const meta = await metaResp.json();

      const center = meta.center || [-31.6654, 115.7895];
      map = L.map('map', { zoomControl: false }).setView(center, 16.4);
      L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        maxZoom: 20,
        attribution: '&copy; OpenStreetMap contributors &copy; CARTO'
      }).addTo(map);

      if (Array.isArray(meta.track_points) && meta.track_points.length > 1) {
        L.polyline(meta.track_points, { color: '#7ec8ff', weight: 4, opacity: 0.9 }).addTo(map);
        // Keep map fixed and show the full circuit area.
        map.fitBounds(meta.track_points, { padding: [0, 0], animate: false });
        map.zoomIn(1, { animate: false });
      }

      if (meta.start_point) {
        const startFlagPos = [meta.start_point[0] + 0.00016, meta.start_point[1]];
        const flagIcon = L.divIcon({
          className: '',
          html: '<div class=\"start-flag\"></div>',
          iconSize: [14, 14],
          iconAnchor: [2, 12],
        });
        L.marker(startFlagPos, { icon: flagIcon }).addTo(map);
      }
      if (Array.isArray(meta.split_points)) {
        meta.split_points.forEach((p, idx) => {
          const splitIcon = L.divIcon({
            className: '',
            html: `<div class=\"split-pin\">${idx + 1}</div>`,
            iconSize: [18, 18],
            iconAnchor: [9, 9],
          });
          L.marker(p, { icon: splitIcon }).addTo(map);
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
      document.getElementById('sessionInline').textContent = `session ${String(t.session_id ?? 1)}`;

      const cs = t.current_splits_sec || [];
      const ls = t.last_splits_sec || [];
      const currentLap = (typeof t.current_lap_sec === 'number' && isFinite(t.current_lap_sec)) ? t.current_lap_sec : null;
      const currentIdx = Number.isFinite(t.current_split_idx) ? t.current_split_idx : 0; // 1..3

      // Segment times (not cumulative) so current split can run live.
      const s1Current = (currentIdx === 1 && currentLap !== null) ? currentLap : cs[0];
      const s2Current = (currentIdx === 2 && currentLap !== null && cs[0] != null) ? (currentLap - cs[0]) : ((cs[1] != null && cs[0] != null) ? (cs[1] - cs[0]) : null);
      const s3Current = (currentIdx === 3 && currentLap !== null && cs[1] != null) ? (currentLap - cs[1]) : ((cs[2] != null && cs[1] != null) ? (cs[2] - cs[1]) : null);

      const s1Last = ls[0];
      const s2Last = (ls[1] != null && ls[0] != null) ? (ls[1] - ls[0]) : null;
      const s3Last = (ls[2] != null && ls[1] != null) ? (ls[2] - ls[1]) : null;

      document.getElementById('s1c').textContent = fmtSplit(s1Current);
      document.getElementById('s1l').textContent = fmtSplit(s1Last);
      document.getElementById('s2c').textContent = fmtSplit(s2Current);
      document.getElementById('s2l').textContent = fmtSplit(s2Last);
      document.getElementById('s3c').textContent = fmtSplit(s3Current);
      document.getElementById('s3l').textContent = fmtSplit(s3Last);

      const el = document.getElementById('splitDelta');
      const title = document.getElementById('splitDeltaTitle');
      const splitIdx = currentIdx;
      title.textContent = splitIdx > 0 ? `Current Split Delta (S${splitIdx})` : 'Current Split Delta';
      el.classList.remove('ahead', 'behind', 'neutral');

      // Compare against last lap segment, with startup deadband to avoid instant "behind".
      let currentSeg = null;
      let lastSeg = null;
      if (splitIdx === 1) { currentSeg = s1Current; lastSeg = s1Last; }
      else if (splitIdx === 2) { currentSeg = s2Current; lastSeg = s2Last; }
      else if (splitIdx === 3) { currentSeg = s3Current; lastSeg = s3Last; }
      const delta = (currentSeg != null && lastSeg != null && currentSeg >= 0.7) ? (currentSeg - lastSeg) : null;

      if (delta != null && isFinite(delta)) {
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
        document.getElementById('sub').textContent = `drv ${data.driver} | fix ${p?.fixq ?? '-'} | sats ${p?.sats ?? '-'}`;
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
            trail.setLatLngs(filterTrailTail(data.history));
          }
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
        self.best_lap = best_lap if isinstance(best_lap, (int, float)) and best_lap >= MIN_VALID_LAP_SEC else None
        self.best_splits = best_splits[:3] + [None] * max(0, 3 - len(best_splits))
        self.best_splits = self.best_splits[:3]
        self.best_split_segments = best_segments[:3] + [None] * max(0, 3 - len(best_segments))
        self.best_split_segments = self.best_split_segments[:3]

    def apply_recent(self, last_lap: Optional[float], last_splits: List[Optional[float]], lap_count: int):
        if isinstance(last_lap, (int, float)) and last_lap > 0:
            self.last_lap = float(last_lap)
        safe_splits: List[Optional[float]] = []
        for i in range(3):
            v = last_splits[i] if i < len(last_splits) else None
            if isinstance(v, (int, float)) and v > 0:
                safe_splits.append(float(v))
            else:
                safe_splits.append(None)
        self.last_splits = safe_splits
        self.lap_count = max(int(lap_count), 0)

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
            is_valid_lap = lap_time >= MIN_VALID_LAP_SEC
            if is_valid_lap and (self.best_lap is None or lap_time < self.best_lap):
                self.best_lap = lap_time
            self.lap_count += 1
            self.last_splits = self.current_splits[:]
            if is_valid_lap:
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
            if isinstance(lap_time, (int, float)) and lap_time >= MIN_VALID_LAP_SEC:
                if best_lap is None or lap_time < best_lap:
                    best_lap = float(lap_time)

            splits = lap.get("splits_sec")
            if isinstance(splits, list) and isinstance(lap_time, (int, float)) and lap_time >= MIN_VALID_LAP_SEC:
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

    def driver_recent(self, driver: str) -> Dict[str, Any]:
        self.ensure_driver(driver)
        driver_data = self.data["drivers"][driver]
        sessions = driver_data.get("sessions", {})
        current_id = str(self.current_session_id(driver))
        lap_count = 0
        latest_lap: Optional[Dict[str, Any]] = None
        latest_ts = -1.0

        if isinstance(sessions, dict):
            current = sessions.get(current_id, {})
            if isinstance(current, dict):
                current_rows = current.get("laps", [])
                if isinstance(current_rows, list):
                    lap_count = len([row for row in current_rows if isinstance(row, dict)])
                    if lap_count > 0 and isinstance(current_rows[-1], dict):
                        latest_lap = current_rows[-1]

            if latest_lap is None:
                for session in sessions.values():
                    if not isinstance(session, dict):
                        continue
                    rows = session.get("laps", [])
                    if not isinstance(rows, list):
                        continue
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        completed_at = row.get("completed_at_sec")
                        if isinstance(completed_at, (int, float)) and completed_at > latest_ts:
                            latest_ts = float(completed_at)
                            latest_lap = row

        if latest_lap is None:
            return {"last_lap_sec": None, "last_splits_sec": [None, None, None], "lap_count": lap_count}

        splits = latest_lap.get("splits_sec")
        safe_splits: List[Optional[float]] = [None, None, None]
        if isinstance(splits, list):
            for i in range(3):
                v = splits[i] if i < len(splits) else None
                if isinstance(v, (int, float)) and v > 0:
                    safe_splits[i] = float(v)

        last_lap = latest_lap.get("lap_time_sec")
        if not isinstance(last_lap, (int, float)) or last_lap <= 0:
            last_lap = None

        return {
            "last_lap_sec": float(last_lap) if isinstance(last_lap, (int, float)) else None,
            "last_splits_sec": safe_splits,
            "lap_count": lap_count,
        }


class InfluxWriter:
    """Write split/lap events to InfluxDB using line protocol over HTTP."""

    def __init__(
        self,
        enabled: bool,
        url: str,
        db: str,
        timeout_sec: float = 1.2,
        v2_bucket: str = "",
        v2_org: str = "",
        v2_token: str = "",
        v1_user: str = "",
        v1_password: str = "",
    ):
        self.enabled = enabled
        self.url = url.rstrip("/")
        self.db = db
        self.timeout_sec = timeout_sec
        self.v2_bucket = v2_bucket
        self.v2_org = v2_org
        self.v2_token = v2_token
        self.v1_user = v1_user
        self.v1_password = v1_password
        self._last_error_log = 0.0

    @staticmethod
    def _esc_tag(v: str) -> str:
        return str(v).replace("\\", "\\\\").replace(",", "\\,").replace(" ", "\\ ").replace("=", "\\=")

    @staticmethod
    def _esc_field_str(v: str) -> str:
        return str(v).replace("\\", "\\\\").replace('"', '\\"')

    def write(self, measurement: str, tags: Dict[str, Any], fields: Dict[str, Any], ts_ns: int):
        if not self.enabled:
            return

        tag_chunks = []
        for k, v in tags.items():
            if v is None:
                continue
            tag_chunks.append(f"{self._esc_tag(k)}={self._esc_tag(v)}")
        tag_part = "," + ",".join(tag_chunks) if tag_chunks else ""

        field_chunks = []
        for k, v in fields.items():
            if v is None:
                continue
            key = self._esc_tag(k)
            if isinstance(v, bool):
                field_chunks.append(f"{key}={'true' if v else 'false'}")
            elif isinstance(v, int):
                field_chunks.append(f"{key}={v}i")
            elif isinstance(v, float):
                field_chunks.append(f"{key}={v}")
            else:
                field_chunks.append(f'{key}="{self._esc_field_str(v)}"')
        if not field_chunks:
            return

        line = f"{self._esc_tag(measurement)}{tag_part} {','.join(field_chunks)} {int(ts_ns)}"
        body = line.encode("utf-8")

        headers = {"Content-Type": "text/plain; charset=utf-8"}
        if self.v2_bucket and self.v2_org:
            path = f"/api/v2/write?org={quote_plus(self.v2_org)}&bucket={quote_plus(self.v2_bucket)}&precision=ns"
            if self.v2_token:
                headers["Authorization"] = f"Token {self.v2_token}"
        else:
            path = f"/write?db={quote_plus(self.db)}&precision=ns"
            if self.v1_user:
                path += f"&u={quote_plus(self.v1_user)}"
            if self.v1_password:
                path += f"&p={quote_plus(self.v1_password)}"

        req = Request(self.url + path, data=body, headers=headers, method="POST")
        try:
            with urlopen(req, timeout=self.timeout_sec):
                pass
        except URLError as exc:
            now = time.time()
            if now - self._last_error_log > 10:
                print(f"Influx write warning: {exc}")
                self._last_error_log = now


class SharedState:
    def __init__(
        self,
        topic: str,
        history_size: int,
        track: TrackGeometry,
        records_file: str,
        mqtt_client: mqtt.Client,
        events_topic_base: str,
        influx: InfluxWriter,
    ):
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
        self.events_topic_base = events_topic_base.strip("/") or "drivers"
        self.mqtt_client = mqtt_client
        self.influx = influx

    def _new_timing_for_driver(self, driver: str) -> LapTiming:
        timing = LapTiming(self.track.total_len_m)
        bench = self.records.driver_benchmarks(driver)
        recent = self.records.driver_recent(driver)
        timing.apply_benchmarks(
            bench.get("best_lap_sec"),
            bench.get("best_splits_sec", [None, None, None]),
            bench.get("best_split_segments_sec", [None, None, None]),
        )
        timing.apply_recent(
            recent.get("last_lap_sec"),
            recent.get("last_splits_sec", [None, None, None]),
            int(recent.get("lap_count", 0)),
        )
        return timing

    def update(self, payload: Dict):
        lat = float(payload["lat"])
        lon = float(payload["lon"])
        s_m, self.last_seg_idx, err_m = self.track.project(lat, lon, self.last_seg_idx)

        # Reject obvious off-track GPS outliers so trail/timing do not spike.
        if err_m > MAX_TRACK_ERROR_M:
            return

        ts_ns = payload.get("ts_ns")
        if isinstance(ts_ns, int) and ts_ns > 0:
            ts_sec = ts_ns / 1_000_000_000.0
        else:
            ts_sec = time.time()

        with self.lock:
            driver = self.active_driver
            timing_state = self.timings[driver]
            prev_splits = timing_state.current_splits[:]
            timing, completed_lap = timing_state.update(ts_sec, s_m)
            session_id = self.records.current_session_id(driver)

            # Emit split events when each split is crossed.
            for idx in range(3):
                old_v = prev_splits[idx]
                new_v = timing_state.current_splits[idx]
                if old_v is None and new_v is not None:
                    if idx == 0:
                        seg_time = new_v
                    elif idx == 1:
                        seg_time = (timing_state.current_splits[1] - timing_state.current_splits[0]) if timing_state.current_splits[0] is not None else None
                    else:
                        seg_time = (timing_state.current_splits[2] - timing_state.current_splits[1]) if timing_state.current_splits[1] is not None else None
                    event = {
                        "event": "split",
                        "driver": driver,
                        "session_id": session_id,
                        "lap_number": timing_state.lap_count + 1,
                        "split_index": idx + 1,
                        "split_cumulative_sec": new_v,
                        "split_segment_sec": seg_time,
                        "ts_ns": int(ts_sec * 1_000_000_000),
                    }
                    self._publish_split_event(event)
                    self.influx.write(
                        measurement="driver_splits",
                        tags={"driver": driver, "session_id": session_id, "split_index": idx + 1},
                        fields={
                            "lap_number": timing_state.lap_count + 1,
                            "split_cumulative_sec": new_v,
                            "split_segment_sec": seg_time,
                        },
                        ts_ns=event["ts_ns"],
                    )

            if completed_lap:
                lap_entry = dict(completed_lap)
                lap_entry["driver"] = driver
                lap_entry["session_id"] = session_id
                self.records.add_lap(driver, lap_entry)
                self._publish_lap_event(lap_entry)
                self.influx.write(
                    measurement="driver_laps",
                    tags={"driver": driver, "session_id": session_id},
                    fields={
                        "lap_number": lap_entry.get("lap_number"),
                        "lap_time_sec": lap_entry.get("lap_time_sec"),
                        "split_1_sec": lap_entry.get("splits_sec", [None, None, None])[0],
                        "split_2_sec": lap_entry.get("splits_sec", [None, None, None])[1],
                        "split_3_sec": lap_entry.get("splits_sec", [None, None, None])[2],
                    },
                    ts_ns=int(ts_sec * 1_000_000_000),
                )

            row = dict(payload)
            row["track_s_m"] = s_m
            row["lap_distance_m"] = timing_state.lap_progress_m if timing_state.armed else None
            row["track_error_m"] = err_m
            row["driver"] = driver

            self.latest = row
            self.seq += 1
            self.history.append({"lat": lat, "lon": lon})
            self._timing_snapshot = timing

    def _publish_split_event(self, event: Dict[str, Any]):
        topic = f"{self.events_topic_base}/{event['driver']}/splits"
        payload = json.dumps(event, separators=(",", ":"), ensure_ascii=True)
        self.mqtt_client.publish(topic, payload, qos=0, retain=False)

    def _publish_lap_event(self, lap_entry: Dict[str, Any]):
        topic = f"{self.events_topic_base}/{lap_entry['driver']}/laps"
        payload = json.dumps(
            {
                "event": "lap",
                "driver": lap_entry["driver"],
                "session_id": lap_entry["session_id"],
                "lap_number": lap_entry.get("lap_number"),
                "lap_time_sec": lap_entry.get("lap_time_sec"),
                "splits_sec": lap_entry.get("splits_sec"),
                "completed_at_sec": lap_entry.get("completed_at_sec"),
            },
            separators=(",", ":"),
            ensure_ascii=True,
        )
        self.mqtt_client.publish(topic, payload, qos=0, retain=False)

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
    p.add_argument("--events-topic-base", default="drivers")
    p.add_argument("--influx-enabled", action="store_true")
    p.add_argument("--influx-url", default="http://127.0.0.1:8086")
    p.add_argument("--influx-db", default="subaru")
    p.add_argument("--influx-timeout-sec", type=float, default=1.2)
    p.add_argument("--influx-v2-org", default="")
    p.add_argument("--influx-v2-bucket", default="")
    p.add_argument("--influx-v2-token", default="")
    p.add_argument("--influx-v1-user", default="")
    p.add_argument("--influx-v1-password", default="")
    return p


def main() -> int:
    args = build_parser().parse_args()

    try:
        track = load_track(args.track_file)
    except Exception as exc:
        print(f"Failed to load track file {args.track_file}: {exc}")
        return 1

    influx = InfluxWriter(
        enabled=args.influx_enabled,
        url=args.influx_url,
        db=args.influx_db,
        timeout_sec=args.influx_timeout_sec,
        v2_bucket=args.influx_v2_bucket,
        v2_org=args.influx_v2_org,
        v2_token=args.influx_v2_token,
        v1_user=args.influx_v1_user,
        v1_password=args.influx_v1_password,
    )

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

    state = SharedState(
        args.mqtt_topic,
        args.history_size,
        track,
        args.records_file,
        client,
        args.events_topic_base,
        influx,
    )

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
        print(f"Driver events topic base: {args.events_topic_base}/<driver>/splits|laps")
        print(f"Influx enabled: {args.influx_enabled} ({args.influx_url})")
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
