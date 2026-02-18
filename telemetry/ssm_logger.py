#!/usr/bin/env python3
"""Subaru SSM2 K-line logger with RomRaider parameter + robust MQTT publishing.

Reliability features:
- Auto-reconnect to ECU serial and MQTT broker.
- Exponential backoff on transient failures.
- On-disk JSONL spool queue when MQTT is unavailable.
- Runtime state file with counters and last-known status.
- Periodic status heartbeat topic.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import paho.mqtt.client as mqtt
import serial


def log(message: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def checksum(data: bytes) -> int:
    return sum(data) & 0xFF


def to_u16_be(msb: int, lsb: int) -> int:
    return (msb << 8) | lsb


def parse_addr(text: str) -> int:
    text = text.strip().lower()
    if text.startswith("0x"):
        return int(text, 16)
    if text.startswith("#"):
        return int(text[1:], 16)
    return int(text, 0)


def slugify_metric(name: str) -> str:
    topic = name.strip().lower()
    topic = topic.replace("air/fuel", "afr")
    topic = topic.replace("a/f", "af")
    topic = topic.replace("%", "pct")
    topic = topic.replace("#", "")
    topic = topic.replace("voltage", "v")
    topic = re.sub(r"[^a-z0-9]+", "_", topic)
    topic = re.sub(r"_+", "_", topic).strip("_")
    if topic == "battery_voltage":
        topic = "battery_v"
    return topic or "metric"


# High-value subset for fast profile. Names match slugified RomRaider parameter IDs.
FAST_PROFILE_TOPICS = {
    "engine_speed",
    "vehicle_speed",
    "manifold_absolute_pressure",
    "manifold_relative_pressure",
    "throttle_opening_angle",
    "accelerator_opening_angle",
    "mass_air_flow",
    "intake_air_temperature",
    "coolant_temperature",
    "ignition_timing",
    "knock_correction",
    "battery_v",
    "afr_sensor_1",
    "afr_correction_1",
    "afr_learning_1",
    "fuel_injector_1_pulse_width",
    "fuel_injector_2_pulse_width",
    "primary_wastegate_duty_cycle",
    "secondary_wastegate_duty_cycle",
    "fuel_pressure_high",
    "main_throttle_sensor",
    "main_accelerator_sensor",
}


def select_profile_params(params: Sequence[RRParam], profile: str) -> List[RRParam]:
    if profile != "fast":
        return list(params)

    selected: List[RRParam] = []
    for param in params:
        base_topic = re.sub(r"_\d+$", "", param.topic)
        if param.topic in FAST_PROFILE_TOPICS or base_topic in FAST_PROFILE_TOPICS:
            selected.append(param)

    # Fallback to full set if no fast topics are available for this ROM.
    return selected if selected else list(params)


def write_json_atomic(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


class JsonlSpool:
    """Simple line-delimited JSON spool with bounded size."""

    def __init__(self, path: Path, max_entries: int = 10000) -> None:
        self.path = path
        self.max_entries = max_entries
        self._append_counter = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, payload: Dict[str, object]) -> None:
        line = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.write("\n")
        self._append_counter += 1
        if self._append_counter >= 100:
            self._append_counter = 0
            self.trim()

    def peek_lines(self, max_lines: int) -> List[str]:
        if max_lines <= 0 or not self.path.exists():
            return []
        out: List[str] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                out.append(line)
                if len(out) >= max_lines:
                    break
        return out

    def drop_first_lines(self, count: int) -> None:
        if count <= 0 or not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        if count >= len(lines):
            self.path.unlink(missing_ok=True)
            return
        with self.path.open("w", encoding="utf-8") as f:
            f.writelines(lines[count:])

    def depth(self) -> int:
        if not self.path.exists():
            return 0
        with self.path.open("r", encoding="utf-8") as f:
            return sum(1 for _ in f)

    def trim(self) -> None:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) <= self.max_entries:
            return
        kept = lines[-self.max_entries :]
        with self.path.open("w", encoding="utf-8") as f:
            f.writelines(kept)


@dataclass(frozen=True)
class ParamDef:
    name: str
    addr: int
    size: int
    unit: str
    decoder: Callable[[Sequence[int]], float]


@dataclass(frozen=True)
class DtcEntry:
    curr_addr: int
    hist_addr: int
    bit: int
    code: str
    title: str


@dataclass(frozen=True)
class DtcHit:
    addr: int
    bit: int
    code: str
    title: str


DEFAULT_PARAMS: List[ParamDef] = [
    ParamDef("engine_rpm", 0x000E, 2, "rpm", lambda b: to_u16_be(b[0], b[1]) / 4.0),
    ParamDef("vehicle_speed_kph", 0x0010, 1, "kph", lambda b: float(b[0])),
    ParamDef("coolant_temp_c", 0x0008, 1, "C", lambda b: float(b[0] - 40)),
    ParamDef("intake_air_temp_c", 0x0012, 1, "C", lambda b: float(b[0] - 40)),
    ParamDef("throttle_open_pct", 0x0015, 1, "%", lambda b: (b[0] * 100.0) / 255.0),
    ParamDef("maf_g_s", 0x0013, 2, "g/s", lambda b: to_u16_be(b[0], b[1]) / 100.0),
    ParamDef("map_psi", 0x000D, 1, "psi", lambda b: (b[0] * 37.0) / 255.0),
    ParamDef("ign_timing_deg", 0x0011, 1, "deg", lambda b: (b[0] - 128.0) / 2.0),
    ParamDef("af_correction1_pct", 0x0009, 1, "%", lambda b: ((b[0] - 128.0) * 100.0) / 128.0),
    ParamDef("af_learning1_pct", 0x000A, 1, "%", lambda b: ((b[0] - 128.0) * 100.0) / 128.0),
]


@dataclass
class RRParam:
    name: str
    topic: str
    addr: int
    size: int
    storagetype: str
    kind: str
    bit: int
    decimals: int
    unit: str
    expr: str
    evaluator: Optional[Callable[[float, Dict[str, float]], float]]


def parse_raw_dtc_defs(defs_path: Path, symbol_name: str) -> List[DtcEntry]:
    text = defs_path.read_text(encoding="utf-8", errors="replace")
    marker = f"const QStringList SSMFlagbyteDefinitions_en::{symbol_name} ="
    start = text.find(marker)
    if start < 0:
        raise RuntimeError(f"Could not find {symbol_name} in {defs_path}")

    block = text[start:]
    end = block.find(";\n")
    if end > 0:
        block = block[: end + 2]

    entries: List[DtcEntry] = []
    pattern = re.compile(r'<<\s*"([0-9A-Fa-f]{6};[0-9A-Fa-f]{6};[1-8];[^"]*)"')
    for match in pattern.finditer(block):
        raw = match.group(1)
        parts = raw.split(";", 4)
        if len(parts) != 5:
            continue
        curr_hex, hist_hex, bit_text, code, title = parts
        entries.append(
            DtcEntry(
                curr_addr=int(curr_hex, 16),
                hist_addr=int(hist_hex, 16),
                bit=int(bit_text, 10),
                code=code.strip(),
                title=title.strip(),
            )
        )

    if not entries:
        raise RuntimeError(f"No entries parsed for {symbol_name} in {defs_path}")
    return entries


def flagbyte_bit(flagbytes: bytes, byte_index: int, bit_index: int) -> bool:
    if byte_index < 0 or byte_index >= len(flagbytes):
        return False
    if bit_index < 0 or bit_index > 7:
        return False
    return bool(flagbytes[byte_index] & (1 << bit_index))


def enumerate_supported_dtc_addr_pairs(flagbytes: bytes) -> Tuple[bool, List[Tuple[int, int]]]:
    fmt_obd2 = not flagbyte_bit(flagbytes, 29, 7)
    pairs: List[Tuple[int, int]] = []

    def add_range(start: int, end: int, hist_delta: int) -> None:
        for addr in range(start, end + 1):
            pairs.append((addr, addr + hist_delta))

    if not fmt_obd2:
        add_range(0x8E, 0x98, 22)
        return fmt_obd2, pairs

    if flagbyte_bit(flagbytes, 29, 4) or flagbyte_bit(flagbytes, 29, 6):
        add_range(0x8E, 0xAD, 32)
    if flagbyte_bit(flagbytes, 28, 0):
        add_range(0xF0, 0xF3, 4)

    if len(flagbytes) > 32:
        if flagbyte_bit(flagbytes, 39, 7):
            add_range(0x123, 0x12A, 8)
        if flagbyte_bit(flagbytes, 39, 6):
            add_range(0x150, 0x154, 5)
        if flagbyte_bit(flagbytes, 39, 5):
            add_range(0x160, 0x164, 5)
        if flagbyte_bit(flagbytes, 39, 4):
            add_range(0x174, 0x17A, 7)
        if len(flagbytes) > 48:
            if flagbyte_bit(flagbytes, 50, 6):
                add_range(0x1C1, 0x1C6, 6)
                add_range(0x20A, 0x20D, 4)
            if flagbyte_bit(flagbytes, 50, 5):
                add_range(0x263, 0x267, 5)

    if not pairs:
        add_range(0x8E, 0xAD, 32)

    return fmt_obd2, pairs


def decode_dtc_hits(
    pairs: Sequence[Tuple[int, int]],
    values: Dict[int, int],
    defs_by_key: Dict[Tuple[int, int, int], DtcEntry],
    use_hist: bool,
) -> List[DtcHit]:
    hits: List[DtcHit] = []
    for curr_addr, hist_addr in pairs:
        addr = hist_addr if use_hist else curr_addr
        if addr not in values:
            continue
        databyte = values[addr]
        for bit0 in range(8):
            if not (databyte & (1 << bit0)):
                continue
            bit = bit0 + 1
            entry = defs_by_key.get((curr_addr, hist_addr, bit))
            if entry is None:
                hits.append(
                    DtcHit(
                        addr=addr,
                        bit=bit,
                        code="???",
                        title=f"Unknown DTC bit (0x{curr_addr:04X}/0x{hist_addr:04X} bit {bit})",
                    )
                )
            elif entry.code == "" and entry.title == "":
                continue
            else:
                hits.append(
                    DtcHit(
                        addr=addr,
                        bit=bit,
                        code=entry.code or "???",
                        title=entry.title or "(no description)",
                    )
                )
    hits.sort(key=lambda h: (h.code, h.addr, h.bit, h.title))
    return hits


def read_dtc_snapshot(
    client: "SSM2Client",
    cu: Dict[str, object],
    pairs: Sequence[Tuple[int, int]],
    defs_by_key: Dict[Tuple[int, int, int], DtcEntry],
    fmt_name: str,
    chunk_size: int,
    retries: int,
    inter_delay: float,
) -> Dict[str, object]:
    addrs = sorted({addr for pair in pairs for addr in pair})
    values = read_chunked(
        client,
        addrs,
        chunk_size=chunk_size,
        retries=retries,
        inter_delay=inter_delay,
        best_effort=True,
    )
    current_hits = decode_dtc_hits(pairs, values, defs_by_key, use_hist=False)
    historic_hits = decode_dtc_hits(pairs, values, defs_by_key, use_hist=True)

    return {
        "sys_id": cu.get("sys_id_hex", ""),
        "rom_id": cu.get("rom_id_hex", ""),
        "format": fmt_name,
        "pairs_total": len(pairs),
        "bytes_read": len(values),
        "bytes_total": len(addrs),
        "count_current": len(current_hits),
        "count_historic": len(historic_hits),
        "current": [{"code": h.code, "title": h.title, "addr": f"0x{h.addr:04X}", "bit": h.bit} for h in current_hits],
        "historic": [{"code": h.code, "title": h.title, "addr": f"0x{h.addr:04X}", "bit": h.bit} for h in historic_hits],
    }


class SSM2Client:
    def __init__(self, ser: serial.Serial, ecu_addr: int = 0x10, pad_addr: int = 0x00):
        self.ser = ser
        self.ecu_addr = ecu_addr
        self.pad_addr = pad_addr

    def _build_frame(self, payload: bytes) -> bytes:
        if len(payload) > 0xFF:
            raise ValueError("SSM2 payload too long")
        head = bytes([0x80, self.ecu_addr, 0xF0, len(payload)])
        msg = head + payload
        return msg + bytes([checksum(msg)])

    @staticmethod
    def _parse_frames(buf: bytearray) -> List[bytes]:
        frames: List[bytes] = []
        i = 0
        while i + 5 <= len(buf):
            if buf[i] != 0x80:
                i += 1
                continue
            payload_len = buf[i + 3]
            end = i + 4 + payload_len + 1
            if end > len(buf):
                break
            frame = bytes(buf[i:end])
            if checksum(frame[:-1]) == frame[-1]:
                frames.append(frame)
                i = end
            else:
                i += 1
        if i:
            del buf[:i]
        return frames

    def request(self, payload: bytes, timeout_s: float = 1.0) -> bytes:
        tx = self._build_frame(payload)
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()
        self.ser.write(tx)

        deadline = time.monotonic() + timeout_s
        rxbuf = bytearray()

        while time.monotonic() < deadline:
            chunk = self.ser.read(self.ser.in_waiting or 1)
            if chunk:
                rxbuf.extend(chunk)
                for frame in self._parse_frames(rxbuf):
                    if frame[1] == self.ecu_addr and frame[2] == 0xF0:
                        # FTDI echo
                        continue
                    if frame[1] == 0xF0 and frame[2] == self.ecu_addr:
                        return frame[4:-1]
            else:
                time.sleep(0.005)

        raise TimeoutError("No SSM reply before timeout")

    def get_cu_data(self) -> Dict[str, object]:
        payload = self.request(bytes([0xBF]), timeout_s=1.2)
        if not payload or payload[0] != 0xFF:
            raise RuntimeError(f"Unexpected GET_CU_DATA reply: {payload.hex(' ')}")
        if len(payload) < 9:
            raise RuntimeError(f"GET_CU_DATA reply too short: {payload.hex(' ')}")

        sys_id = payload[1:4]
        rom_id = payload[4:9]
        flagbytes = payload[9:]
        return {
            "sys_id_hex": sys_id.hex().upper(),
            "rom_id_hex": rom_id.hex().upper(),
            "rom_id_ascii": "".join(chr(b) if 32 <= b <= 126 else "." for b in rom_id),
            "flagbytes_count": len(flagbytes),
            "flagbytes_hex": flagbytes.hex(),
            "raw_payload_hex": payload.hex(" "),
        }

    def read_multiple(self, addresses: Sequence[int]) -> Dict[int, int]:
        if not addresses:
            return {}
        if len(addresses) > 84:
            raise ValueError("ISO14230 SSM2 limit is 84 addresses per multi-read")

        req = bytearray([0xA8, self.pad_addr])
        for addr in addresses:
            if addr < 0 or addr > 0xFFFFFF:
                raise ValueError(f"Address out of range: 0x{addr:X}")
            req.extend(addr.to_bytes(3, byteorder="big"))

        payload = self.request(bytes(req), timeout_s=1.0)
        if not payload or payload[0] != 0xE8:
            raise RuntimeError(f"Unexpected READ_MULTIPLE reply: {payload.hex(' ')}")

        data = payload[1:]
        if len(data) != len(addresses):
            raise RuntimeError(
                f"READ_MULTIPLE length mismatch, requested {len(addresses)}, got {len(data)}"
            )

        return {addr: value for addr, value in zip(addresses, data)}


ALLOWED_AST_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
    ast.Not,
    ast.And,
    ast.Or,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
)

ALLOWED_EXPR_NAMES = {"value", "abs", "min", "max", "round", "pow", "getlogparam"}


def normalize_expr(expr: str) -> str:
    out = expr.strip()
    if not out:
        return ""
    if "?" in out:
        return ""
    out = out.replace("[value]", "value")
    out = out.replace("GetLogParam", "getlogparam")
    out = out.replace("&&", " and ")
    out = out.replace("||", " or ")
    out = re.sub(r"(?<![=!<>])!(?!=)", " not ", out)
    return out


def compile_expr(expr: str) -> Optional[Callable[[float, Dict[str, float]], float]]:
    expr_norm = normalize_expr(expr)
    if not expr_norm:
        return None
    try:
        tree = ast.parse(expr_norm, mode="eval")
        for node in ast.walk(tree):
            if not isinstance(node, ALLOWED_AST_NODES):
                return None
            if isinstance(node, ast.Name) and node.id not in ALLOWED_EXPR_NAMES:
                return None
            if isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name):
                    return None
                if node.func.id not in ALLOWED_EXPR_NAMES:
                    return None
        code = compile(tree, "<rr_expr>", "eval")
    except Exception:
        return None

    def _eval(value: float, resolved: Dict[str, float]) -> float:
        def _getlogparam(name: str) -> float:
            got = resolved.get(str(name), 0.0)
            try:
                return float(got)
            except Exception:
                return 0.0

        env = {
            "value": float(value),
            "abs": abs,
            "min": min,
            "max": max,
            "round": round,
            "pow": pow,
            "getlogparam": _getlogparam,
        }
        return float(eval(code, {"__builtins__": {}}, env))

    return _eval


class RomRaiderSSM:
    def __init__(self, xml_path: str):
        self.xml_path = Path(xml_path).expanduser()
        if not self.xml_path.exists():
            raise FileNotFoundError(f"RomRaider definition not found: {self.xml_path}")

        root = ET.parse(self.xml_path).getroot()
        ssm_protocol = None
        for logprotocol in root.findall("./logprotocols/logprotocol"):
            if logprotocol.attrib.get("type", "").upper() == "SSM":
                ssm_protocol = logprotocol
                break
        if ssm_protocol is None:
            raise RuntimeError("No <logprotocol type='SSM'> found in RomRaider log_defs.xml")

        self.ecu_nodes = list(ssm_protocol.findall("ecu"))
        self.ecu_by_type: Dict[str, ET.Element] = {}
        self.ecu_by_id: List[Tuple[str, ET.Element]] = []

        for ecu in self.ecu_nodes:
            ecu_type = (ecu.attrib.get("type") or "").strip()
            ecu_id = (ecu.attrib.get("id") or "").strip().upper()
            if ecu_type and ecu_type not in self.ecu_by_type:
                self.ecu_by_type[ecu_type] = ecu
            if ecu_id:
                self.ecu_by_id.append((ecu_id, ecu))

    @staticmethod
    def _id_matches(pattern: str, rom_id_hex: str) -> bool:
        p = pattern.upper()
        r = rom_id_hex.upper()
        if len(p) != len(r) or len(p) % 2 != 0:
            return False
        for i in range(0, len(p), 2):
            pb = p[i : i + 2]
            rb = r[i : i + 2]
            if pb != "FF" and pb != rb:
                return False
        return True

    def find_ecu(self, rom_id_hex: str) -> Optional[ET.Element]:
        rid = rom_id_hex.upper()

        for ecu_id, ecu in self.ecu_by_id:
            if ecu_id == rid:
                return ecu

        candidates: List[Tuple[int, ET.Element]] = []
        for ecu_id, ecu in self.ecu_by_id:
            if ecu_id in {"", "BASE"}:
                continue
            if self._id_matches(ecu_id, rid):
                wildcard_bytes = sum(
                    1 for i in range(0, len(ecu_id), 2) if ecu_id[i : i + 2] == "FF"
                )
                candidates.append((wildcard_bytes, ecu))

        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    @staticmethod
    def _storagetype_size(storagetype: str) -> int:
        sizes = {
            "uint8": 1,
            "int8": 1,
            "uint16": 2,
            "int16": 2,
            "uint32": 4,
            "int32": 4,
        }
        return sizes.get(storagetype.lower(), 0)

    @classmethod
    def _parse_parameter(cls, pnode: ET.Element) -> Optional[RRParam]:
        name = (pnode.attrib.get("id") or "").strip()
        storagetype = (pnode.attrib.get("storagetype") or "").strip().lower()
        offset = (pnode.attrib.get("offset") or "").strip()
        if not name or not storagetype or not offset:
            return None

        size = cls._storagetype_size(storagetype)
        if size == 0:
            return None

        try:
            addr = parse_addr(offset)
        except Exception:
            return None

        kind = (pnode.attrib.get("type") or "").strip().lower()
        bit = 0
        try:
            bit = int((pnode.attrib.get("bit") or "0").strip())
        except ValueError:
            bit = 0

        decimals = 3
        try:
            decimals = int((pnode.attrib.get("decimals") or "3").strip())
        except ValueError:
            decimals = 3

        expr = (pnode.attrib.get("expr") or "").strip()
        evaluator = compile_expr(expr)
        unit = (pnode.attrib.get("metric") or "").strip()

        return RRParam(
            name=name,
            topic=slugify_metric(name),
            addr=addr,
            size=size,
            storagetype=storagetype,
            kind=kind,
            bit=bit,
            decimals=decimals,
            unit=unit,
            expr=expr,
            evaluator=evaluator,
        )

    def params_for_rom(self, rom_id_hex: str) -> Tuple[List[RRParam], Dict[str, str], int]:
        ecu = self.find_ecu(rom_id_hex)
        if ecu is None:
            raise RuntimeError(f"No RomRaider ECU entry found for ROM ID {rom_id_hex}")

        visited_types: set[str] = set()
        params = OrderedDict()
        skipped = 0

        def _collect(node: ET.Element) -> None:
            include_str = (node.attrib.get("include") or "").strip()
            includes = [x.strip() for x in include_str.split(",") if x.strip()]
            for inc in includes:
                if inc in visited_types:
                    continue
                visited_types.add(inc)
                parent = self.ecu_by_type.get(inc)
                if parent is not None:
                    _collect(parent)

            nonlocal skipped
            for pnode in node.findall("parameter"):
                parsed = self._parse_parameter(pnode)
                if parsed is None:
                    skipped += 1
                    continue
                params[parsed.name] = parsed

        _collect(ecu)

        used_topics: Dict[str, int] = {}
        final: List[RRParam] = []
        for p in params.values():
            t = p.topic
            idx = used_topics.get(t, 0)
            used_topics[t] = idx + 1
            if idx > 0:
                t = f"{t}_{idx + 1}"
            final.append(
                RRParam(
                    name=p.name,
                    topic=t,
                    addr=p.addr,
                    size=p.size,
                    storagetype=p.storagetype,
                    kind=p.kind,
                    bit=p.bit,
                    decimals=p.decimals,
                    unit=p.unit,
                    expr=p.expr,
                    evaluator=p.evaluator,
                )
            )

        meta = {
            "ecu_name": ecu.attrib.get("name", ""),
            "ecu_type": ecu.attrib.get("type", ""),
            "ecu_id": ecu.attrib.get("id", ""),
        }
        return final, meta, skipped


def decode_raw_value(raw_bytes: Sequence[int], storagetype: str) -> int:
    signed = storagetype.startswith("int")
    return int.from_bytes(bytes(raw_bytes), byteorder="big", signed=signed)


def decode_rr_params(
    data_by_addr: Dict[int, int],
    params: Sequence[RRParam],
) -> Tuple[Dict[str, float], Dict[str, str], Dict[str, float]]:
    metrics: Dict[str, float] = {}
    units: Dict[str, str] = {}
    resolved_by_name: Dict[str, float] = {}

    for p in params:
        bytes_for_param = [data_by_addr.get(p.addr + i) for i in range(p.size)]
        if any(v is None for v in bytes_for_param):
            continue

        raw_value = float(decode_raw_value(bytes_for_param, p.storagetype))

        if (
            p.kind == "bool"
            and p.size == 1
            and 1 <= p.bit <= 8
            and normalize_expr(p.expr) in {"", "value"}
        ):
            raw_value = float((bytes_for_param[0] >> (p.bit - 1)) & 0x1)

        value = raw_value
        if p.evaluator is not None:
            try:
                value = p.evaluator(raw_value, resolved_by_name)
            except Exception:
                value = raw_value

        value = round(float(value), max(0, p.decimals))
        metrics[p.topic] = value
        units[p.topic] = p.unit
        resolved_by_name[p.name] = value

    return metrics, units, resolved_by_name


def bytes_for_params(params: Iterable[ParamDef]) -> List[int]:
    addrs: List[int] = []
    for p in params:
        for i in range(p.size):
            addrs.append(p.addr + i)
    return sorted(set(addrs))


def decode_params(data_by_addr: Dict[int, int], params: Sequence[ParamDef]) -> Dict[str, Tuple[float, str]]:
    decoded: Dict[str, Tuple[float, str]] = {}
    for p in params:
        raw = [data_by_addr[p.addr + i] for i in range(p.size)]
        decoded[p.name] = (p.decoder(raw), p.unit)
    return decoded


def read_chunked(
    client: SSM2Client,
    addresses: Sequence[int],
    chunk_size: int = 16,
    retries: int = 2,
    inter_delay: float = 0.02,
    best_effort: bool = False,
) -> Dict[int, int]:
    out: Dict[int, int] = {}
    i = 0
    while i < len(addresses):
        size = min(chunk_size, len(addresses) - i)
        chunk = list(addresses[i : i + size])

        last_error: Optional[Exception] = None
        ok = False
        for _ in range(max(1, retries)):
            try:
                out.update(client.read_multiple(chunk))
                ok = True
                break
            except Exception as exc:
                last_error = exc
                time.sleep(inter_delay)

        if ok:
            i += size
            continue

        if size > 1:
            chunk_size = max(1, size // 2)
            continue

        if best_effort:
            i += 1
            continue

        if last_error is not None:
            raise last_error
        raise RuntimeError("read_chunked failed")

    return out


def cmd_info(client: SSM2Client) -> int:
    info = client.get_cu_data()
    print(f"SYS_ID     : {info['sys_id_hex']}")
    print(f"ROM_ID_HEX : {info['rom_id_hex']}")
    print(f"ROM_ID_ASC : {info['rom_id_ascii']}")
    print(f"FLAGBYTES  : {info['flagbytes_count']}")
    return 0


def cmd_raw(client: SSM2Client, addresses: Sequence[int]) -> int:
    values = read_chunked(client, addresses)
    for addr in addresses:
        print(f"0x{addr:06X} = 0x{values[addr]:02X} ({values[addr]})")
    return 0


def cmd_stream(client: SSM2Client, hz: float, samples: int) -> int:
    params = DEFAULT_PARAMS
    addresses = bytes_for_params(params)
    period = 1.0 / hz
    count = 0

    while samples <= 0 or count < samples:
        start = time.monotonic()
        values = read_chunked(client, addresses)
        decoded = decode_params(values, params)
        ts = time.strftime("%H:%M:%S")

        line = [
            f"{ts}",
            f"rpm={decoded['engine_rpm'][0]:.0f}",
            f"spd={decoded['vehicle_speed_kph'][0]:.0f}kph",
            f"coolant={decoded['coolant_temp_c'][0]:.1f}C",
            f"iat={decoded['intake_air_temp_c'][0]:.1f}C",
            f"throttle={decoded['throttle_open_pct'][0]:.1f}%",
            f"maf={decoded['maf_g_s'][0]:.2f}g/s",
            f"map={decoded['map_psi'][0]:.2f}psi",
            f"timing={decoded['ign_timing_deg'][0]:.1f}deg",
        ]
        print(" | ".join(line), flush=True)

        count += 1
        sleep_for = period - (time.monotonic() - start)
        if sleep_for > 0:
            time.sleep(sleep_for)

    return 0


def mqtt_publish_checked(
    mclient: mqtt.Client,
    topic: str,
    payload: str,
    qos: int,
    retain: bool,
    timeout_sec: float = 5.0,
) -> None:
    info = mclient.publish(topic, payload, qos=qos, retain=retain)
    info.wait_for_publish(timeout=timeout_sec)
    if info.rc != mqtt.MQTT_ERR_SUCCESS:
        raise RuntimeError(f"MQTT publish failed rc={info.rc} topic={topic}")


def publish_payload_and_metrics(
    mclient: mqtt.Client,
    base_topic: str,
    payload_obj: Dict[str, object],
    qos: int,
    retain: bool,
) -> None:
    mqtt_publish_checked(
        mclient,
        f"{base_topic}/data",
        json.dumps(payload_obj, separators=(",", ":"), ensure_ascii=False),
        qos=qos,
        retain=retain,
    )

    metrics = payload_obj.get("metrics", {})
    if isinstance(metrics, dict):
        for metric_topic, metric_value in metrics.items():
            mqtt_publish_checked(
                mclient,
                f"{base_topic}/{metric_topic}",
                f"{metric_value}",
                qos=qos,
                retain=retain,
            )


def publish_status(
    mclient: mqtt.Client,
    status_topic: str,
    status: Dict[str, object],
    qos: int,
) -> None:
    mqtt_publish_checked(
        mclient,
        status_topic,
        json.dumps(status, separators=(",", ":"), ensure_ascii=False),
        qos=qos,
        retain=True,
        timeout_sec=3.0,
    )


def _reason_code_to_int(reason_code: object) -> int:
    try:
        return int(reason_code)  # type: ignore[arg-type]
    except Exception:
        pass
    for attr in ("value", "rc", "code"):
        try:
            return int(getattr(reason_code, attr))
        except Exception:
            continue
    return -1


def make_mqtt_client(args: argparse.Namespace, conn_state: Dict[str, bool]) -> mqtt.Client:
    client_id = args.mqtt_client_id or f"subaru-ssm-{int(time.time())}"
    mclient = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)

    if args.mqtt_user:
        mclient.username_pw_set(args.mqtt_user, args.mqtt_password)

    def _on_connect(_client, _userdata, _flags, reason_code, _properties=None):
        rc = _reason_code_to_int(reason_code)
        conn_state["connected"] = rc == 0
        if rc == 0:
            log("MQTT connected")
        else:
            log(f"MQTT connect returned rc={rc}")

    def _on_disconnect(_client, _userdata, disconnect_flags, reason_code, _properties=None):
        conn_state["connected"] = False
        rc = _reason_code_to_int(reason_code)
        log(f"MQTT disconnected rc={rc} flags={disconnect_flags}")

    mclient.on_connect = _on_connect
    mclient.on_disconnect = _on_disconnect
    mclient.reconnect_delay_set(min_delay=1, max_delay=max(2, int(args.backoff_max)))
    return mclient


def ensure_mqtt_connected(
    mclient: mqtt.Client,
    args: argparse.Namespace,
    conn_state: Dict[str, bool],
) -> bool:
    if conn_state.get("connected"):
        return True

    try:
        if not conn_state.get("loop_started"):
            mclient.connect(args.mqtt_host, args.mqtt_port, keepalive=60)
            mclient.loop_start()
            conn_state["loop_started"] = True
        else:
            mclient.reconnect()
    except Exception as exc:
        log(f"MQTT connect attempt failed: {exc}")
        return False

    deadline = time.monotonic() + args.connect_timeout
    while time.monotonic() < deadline:
        if conn_state.get("connected"):
            return True
        time.sleep(0.1)

    return conn_state.get("connected", False)


def flush_spool(
    spool: JsonlSpool,
    mclient: mqtt.Client,
    args: argparse.Namespace,
    base_topic: str,
) -> int:
    lines = spool.peek_lines(args.flush_per_loop)
    if not lines:
        return 0

    sent_lines = 0
    for line in lines:
        try:
            payload_obj = json.loads(line)
        except json.JSONDecodeError:
            sent_lines += 1
            continue

        try:
            publish_payload_and_metrics(
                mclient,
                base_topic,
                payload_obj,
                qos=args.mqtt_qos,
                retain=args.mqtt_retain,
            )
            sent_lines += 1
        except Exception:
            break

    if sent_lines:
        spool.drop_first_lines(sent_lines)

    if sent_lines < len(lines):
        raise RuntimeError("Spool flush interrupted by publish failure")

    return sent_lines


def open_serial_client(args: argparse.Namespace) -> Tuple[serial.Serial, SSM2Client]:
    ser = serial.Serial(
        port=args.port,
        baudrate=args.baud,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.05,
    )
    client = SSM2Client(ser, ecu_addr=args.ecu, pad_addr=args.pad)
    return ser, client


def close_serial(ser: Optional[serial.Serial]) -> None:
    if ser is None:
        return
    try:
        ser.close()
    except Exception:
        pass


def cmd_mqtt(args: argparse.Namespace) -> int:
    rr = RomRaiderSSM(args.romraider_defs)
    base = args.topic_base.rstrip("/")

    spool = JsonlSpool(Path(args.spool_file).expanduser(), max_entries=args.max_spool_entries)
    state_file = Path(args.state_file).expanduser()

    conn_state: Dict[str, bool] = {"connected": False, "loop_started": False}
    mclient = make_mqtt_client(args, conn_state)

    ser: Optional[serial.Serial] = None
    client: Optional[SSM2Client] = None

    cu: Optional[Dict[str, object]] = None
    rr_params: List[RRParam] = []
    rr_meta: Dict[str, str] = {"ecu_name": "", "ecu_type": "", "ecu_id": ""}
    addresses: List[int] = []
    dtc_defs_by_key: Dict[Tuple[int, int, int], DtcEntry] = {}
    dtc_pairs: List[Tuple[int, int]] = []
    dtc_format_name = ""
    dtc_enabled = args.dtc_interval > 0
    dtc_defs_path = Path(args.dtc_defs_file).expanduser()
    last_dtc_poll = 0.0
    last_dtc_ts = ""
    last_dtc_counts = {"current": 0, "historic": 0}

    stats: Dict[str, object] = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "samples_total": 0,
        "samples_ok": 0,
        "samples_spooled": 0,
        "samples_failed": 0,
        "serial_failures": 0,
        "mqtt_failures": 0,
        "dtc_polls_ok": 0,
        "dtc_polls_failed": 0,
        "last_error": "",
        "last_ok_ts": "",
    }

    last_status_pub = 0.0
    backoff = max(0.1, args.backoff_min)

    try:
        while args.samples <= 0 or int(stats["samples_total"]) < args.samples:
            loop_start = time.monotonic()

            try:
                if client is None:
                    try:
                        ser, client = open_serial_client(args)
                        cu = client.get_cu_data()
                        all_rr_params, rr_meta, skipped = rr.params_for_rom(str(cu["rom_id_hex"]))
                        rr_params = select_profile_params(all_rr_params, args.profile)
                        params_filtered = max(0, len(all_rr_params) - len(rr_params))
                        addresses = sorted({p.addr + i for p in rr_params for i in range(p.size)})
                        if not addresses:
                            raise RuntimeError("No supported RomRaider parameters found for this ROM")

                        dtc_defs_by_key = {}
                        dtc_pairs = []
                        dtc_format_name = ""
                        if dtc_enabled:
                            try:
                                flagbytes_hex = str(cu.get("flagbytes_hex", ""))
                                flagbytes = bytes.fromhex(flagbytes_hex) if flagbytes_hex else b""
                                fmt_obd2, dtc_pairs = enumerate_supported_dtc_addr_pairs(flagbytes)
                                defs_symbol = "_DTC_OBD_defs_en" if fmt_obd2 else "_DTC_SUBARU_defs_en"
                                dtc_entries = parse_raw_dtc_defs(dtc_defs_path, defs_symbol)
                                dtc_defs_by_key = {
                                    (e.curr_addr, e.hist_addr, e.bit): e for e in dtc_entries
                                }
                                dtc_format_name = "OBD2-style" if fmt_obd2 else "Subaru-native"
                                log(
                                    f"DTC polling ready: format={dtc_format_name}, "
                                    f"pairs={len(dtc_pairs)}, interval={args.dtc_interval:.0f}s, topic={args.dtc_topic}"
                                )
                            except Exception as exc:
                                stats["dtc_polls_failed"] = int(stats["dtc_polls_failed"]) + 1
                                stats["last_error"] = f"dtc_init: {exc}"
                                log(f"DTC polling disabled for this session: {exc}")

                        log(
                            "ECU ready: "
                            f"ROM {cu['rom_id_hex']} -> {rr_meta['ecu_name']} [{rr_meta['ecu_type']}], "
                            f"profile={args.profile}, params_supported={len(rr_params)}, "
                            f"params_filtered={params_filtered}, params_skipped={skipped}, addresses={len(addresses)}"
                        )
                        backoff = max(0.1, args.backoff_min)
                    except Exception as exc:
                        stats["serial_failures"] = int(stats["serial_failures"]) + 1
                        stats["samples_failed"] = int(stats["samples_failed"]) + 1
                        stats["last_error"] = f"serial_init: {exc}"
                        close_serial(ser)
                        ser = None
                        client = None

                        write_json_atomic(
                            state_file,
                            {
                                "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
                                "status": "degraded",
                                "mqtt_connected": conn_state.get("connected", False),
                                "spool_depth": spool.depth(),
                                "profile": args.profile,
                                "stats": stats,
                            },
                        )

                        delay = min(args.backoff_max, backoff)
                        log(f"Serial init failed, retrying in {delay:.1f}s: {exc}")
                        time.sleep(delay)
                        backoff = min(args.backoff_max, backoff * 1.5)
                        continue

                # We have a live client at this point.
                assert client is not None
                assert cu is not None

                values = read_chunked(
                    client,
                    addresses,
                    chunk_size=args.chunk_size,
                    retries=args.read_retries,
                    inter_delay=args.read_inter_delay,
                    best_effort=True,
                )
                metrics, units, _resolved = decode_rr_params(values, rr_params)

                stats["samples_total"] = int(stats["samples_total"]) + 1
                seq = int(stats["samples_total"])

                ts_epoch = time.time()
                ts_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts_epoch))
                payload_obj: Dict[str, object] = {
                    "seq": seq,
                    "ts": ts_iso,
                    "ts_epoch": ts_epoch,
                    "sys_id": cu["sys_id_hex"],
                    "rom_id": cu["rom_id_hex"],
                    "ecu_name": rr_meta.get("ecu_name", ""),
                    "ecu_type": rr_meta.get("ecu_type", ""),
                    "profile": args.profile,
                    "metrics": metrics,
                    "units": units,
                }

                published_now = False

                if ensure_mqtt_connected(mclient, args, conn_state):
                    try:
                        flushed = flush_spool(spool, mclient, args, base)
                        if flushed:
                            log(f"Flushed {flushed} queued sample(s) from spool")

                        publish_payload_and_metrics(
                            mclient,
                            base,
                            payload_obj,
                            qos=args.mqtt_qos,
                            retain=args.mqtt_retain,
                        )
                        published_now = True
                        stats["samples_ok"] = int(stats["samples_ok"]) + 1
                        stats["last_ok_ts"] = ts_iso
                        stats["last_error"] = ""
                        backoff = max(0.1, args.backoff_min)
                    except Exception as exc:
                        stats["mqtt_failures"] = int(stats["mqtt_failures"]) + 1
                        stats["last_error"] = f"mqtt_publish: {exc}"
                        spool.append(payload_obj)
                        stats["samples_spooled"] = int(stats["samples_spooled"]) + 1
                        conn_state["connected"] = False
                        log(f"MQTT publish failed, spooled seq={seq}: {exc}")
                else:
                    stats["mqtt_failures"] = int(stats["mqtt_failures"]) + 1
                    stats["last_error"] = "mqtt_unavailable"
                    spool.append(payload_obj)
                    stats["samples_spooled"] = int(stats["samples_spooled"]) + 1
                    log(f"MQTT unavailable, spooled seq={seq}")

                now_mono = time.monotonic()
                if dtc_enabled and dtc_pairs and dtc_defs_by_key and (now_mono - last_dtc_poll >= args.dtc_interval):
                    last_dtc_poll = now_mono
                    try:
                        dtc_payload = read_dtc_snapshot(
                            client,
                            cu,
                            dtc_pairs,
                            dtc_defs_by_key,
                            dtc_format_name,
                            chunk_size=args.chunk_size,
                            retries=args.read_retries,
                            inter_delay=args.read_inter_delay,
                        )
                        dtc_payload["ts"] = ts_iso
                        if ensure_mqtt_connected(mclient, args, conn_state):
                            mqtt_publish_checked(
                                mclient,
                                args.dtc_topic,
                                json.dumps(dtc_payload, separators=(",", ":"), ensure_ascii=False),
                                qos=min(args.mqtt_qos, 1),
                                retain=True,
                            )
                            stats["dtc_polls_ok"] = int(stats["dtc_polls_ok"]) + 1
                            last_dtc_ts = ts_iso
                            last_dtc_counts = {
                                "current": int(dtc_payload.get("count_current", 0)),
                                "historic": int(dtc_payload.get("count_historic", 0)),
                            }
                        else:
                            stats["mqtt_failures"] = int(stats["mqtt_failures"]) + 1
                            stats["last_error"] = "mqtt_unavailable_dtc"
                    except Exception as exc:
                        stats["dtc_polls_failed"] = int(stats["dtc_polls_failed"]) + 1
                        stats["last_error"] = f"dtc_poll: {exc}"
                        log(f"DTC polling failure: {exc}")

                status_payload: Dict[str, object] = {
                    "ts": ts_iso,
                    "state": "running" if published_now else "degraded",
                    "mqtt_connected": conn_state.get("connected", False),
                    "spool_depth": spool.depth(),
                    "profile": args.profile,
                    "params_active": len(rr_params),
                    "dtc": {
                        "enabled": bool(dtc_enabled and dtc_pairs and dtc_defs_by_key),
                        "topic": args.dtc_topic,
                        "interval_sec": args.dtc_interval,
                        "last_ts": last_dtc_ts,
                        "count_current": last_dtc_counts["current"],
                        "count_historic": last_dtc_counts["historic"],
                    },
                    "stats": stats,
                    "rom_id": cu.get("rom_id_hex", ""),
                }
                write_json_atomic(state_file, status_payload)

                if now_mono - last_status_pub >= args.status_interval:
                    last_status_pub = now_mono
                    if ensure_mqtt_connected(mclient, args, conn_state):
                        try:
                            publish_status(
                                mclient,
                                args.status_topic,
                                status_payload,
                                qos=min(args.mqtt_qos, 1),
                            )
                        except Exception as exc:
                            stats["mqtt_failures"] = int(stats["mqtt_failures"]) + 1
                            stats["last_error"] = f"status_publish: {exc}"
                            conn_state["connected"] = False

                period = 1.0 / args.hz if args.hz > 0 else 0.0
                sleep_for = period - (time.monotonic() - loop_start)
                if sleep_for > 0:
                    time.sleep(sleep_for)

            except KeyboardInterrupt:
                return 130
            except Exception as exc:
                stats["samples_failed"] = int(stats["samples_failed"]) + 1
                stats["last_error"] = f"loop_unhandled: {exc}"
                log(f"Unhandled loop error: {exc}")
                close_serial(ser)
                ser = None
                client = None
                write_json_atomic(
                    state_file,
                    {
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
                        "state": "degraded",
                        "mqtt_connected": conn_state.get("connected", False),
                        "spool_depth": spool.depth(),
                        "profile": args.profile,
                        "stats": stats,
                    },
                )
                delay = min(args.backoff_max, backoff)
                time.sleep(delay)
                backoff = min(args.backoff_max, backoff * 1.5)

    finally:
        close_serial(ser)
        try:
            if conn_state.get("loop_started"):
                mclient.loop_stop()
            mclient.disconnect()
        except Exception:
            pass

    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Read Subaru SSM2/K-line data")
    p.add_argument("--port", default="/dev/ttyUSB0", help="Serial port (default: /dev/ttyUSB0)")
    p.add_argument("--baud", type=int, default=4800, help="Baud rate (default: 4800)")
    p.add_argument("--ecu", type=parse_addr, default=0x10, help="ECU address (default: 0x10)")
    p.add_argument("--pad", type=parse_addr, default=0x00, help="Pad address (default: 0x00)")

    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("info", help="Read ECU SYS/ROM ID and flagbyte count")

    raw = sub.add_parser("raw", help="Read raw byte values from addresses")
    raw.add_argument("addresses", nargs="+", type=parse_addr, help="Byte addresses, e.g. 0x0008 0x0009")

    stream = sub.add_parser("stream", help="Stream decoded common parameters")
    stream.add_argument("--hz", type=float, default=5.0, help="Sample rate (default: 5 Hz)")
    stream.add_argument("--samples", type=int, default=0, help="Number of samples (0 = infinite)")

    mqtt_cmd = sub.add_parser("mqtt", help="Publish RomRaider params to MQTT")
    mqtt_cmd.add_argument(
        "--romraider-defs",
        default="/home/pi/subaru-telemetry/vendor/RomRaider/definitions/log_defs.xml",
        help="Path to RomRaider log_defs.xml",
    )
    mqtt_cmd.add_argument("--hz", type=float, default=2.0, help="Sample rate (default: 2 Hz)")
    mqtt_cmd.add_argument(
        "--profile",
        choices=["full", "fast"],
        default="full",
        help="Parameter profile: full or fast (default: full)",
    )
    mqtt_cmd.add_argument("--samples", type=int, default=0, help="Number of samples (0 = infinite)")
    mqtt_cmd.add_argument("--topic-base", default="subaru", help="MQTT topic base (default: subaru)")
    mqtt_cmd.add_argument("--status-topic", default="subaru/status", help="MQTT status topic")
    mqtt_cmd.add_argument("--mqtt-host", default="127.0.0.1", help="MQTT host")
    mqtt_cmd.add_argument("--mqtt-port", type=int, default=1883, help="MQTT port")
    mqtt_cmd.add_argument("--mqtt-user", default="", help="MQTT username")
    mqtt_cmd.add_argument("--mqtt-password", default="", help="MQTT password")
    mqtt_cmd.add_argument("--mqtt-client-id", default="", help="MQTT client id")
    mqtt_cmd.add_argument("--mqtt-qos", type=int, default=0, choices=[0, 1, 2], help="MQTT QoS")
    mqtt_cmd.add_argument("--mqtt-retain", action="store_true", help="Publish retained MQTT messages")

    mqtt_cmd.add_argument(
        "--spool-file",
        default="/home/pi/subaru-telemetry/runtime/mqtt_spool.jsonl",
        help="JSONL spool file used while MQTT is unavailable",
    )
    mqtt_cmd.add_argument(
        "--state-file",
        default="/home/pi/subaru-telemetry/runtime/state.json",
        help="Runtime state output JSON file",
    )
    mqtt_cmd.add_argument("--max-spool-entries", type=int, default=10000, help="Max queued samples")
    mqtt_cmd.add_argument("--flush-per-loop", type=int, default=50, help="Max queued samples flushed each loop")
    mqtt_cmd.add_argument("--status-interval", type=float, default=30.0, help="Seconds between status heartbeats")
    mqtt_cmd.add_argument("--connect-timeout", type=float, default=5.0, help="MQTT connect wait timeout")

    mqtt_cmd.add_argument("--chunk-size", type=int, default=16, help="Initial SSM address chunk size")
    mqtt_cmd.add_argument("--read-retries", type=int, default=3, help="Retries per SSM read chunk")
    mqtt_cmd.add_argument("--read-inter-delay", type=float, default=0.03, help="Delay between read retries")
    mqtt_cmd.add_argument(
        "--dtc-interval",
        type=float,
        default=300.0,
        help="Seconds between DTC polls (0 to disable, default: 300)",
    )
    mqtt_cmd.add_argument(
        "--dtc-topic",
        default="subaru/dtc",
        help="MQTT topic for DTC snapshot JSON (default: subaru/dtc)",
    )
    mqtt_cmd.add_argument(
        "--dtc-defs-file",
        default="/home/pi/subaru-telemetry/vendor/FreeSSM/src/SSMFlagbyteDefinitions_en.cpp",
        help="Path to FreeSSM DTC definitions source",
    )

    mqtt_cmd.add_argument("--backoff-min", type=float, default=1.0, help="Minimum failure backoff seconds")
    mqtt_cmd.add_argument("--backoff-max", type=float, default=30.0, help="Maximum failure backoff seconds")

    return p


def main() -> int:
    args = build_arg_parser().parse_args()

    if args.cmd == "mqtt":
        try:
            return cmd_mqtt(args)
        except KeyboardInterrupt:
            return 130
        except Exception as exc:
            print(f"SSM communication error: {exc}", file=sys.stderr)
            return 1

    # Non-daemon commands open serial once and fail fast.
    try:
        ser = serial.Serial(
            port=args.port,
            baudrate=args.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.05,
        )
    except Exception as exc:
        print(f"Failed to open serial port {args.port}: {exc}", file=sys.stderr)
        return 2

    with ser:
        client = SSM2Client(ser, ecu_addr=args.ecu, pad_addr=args.pad)
        try:
            if args.cmd == "info":
                return cmd_info(client)
            if args.cmd == "raw":
                return cmd_raw(client, args.addresses)
            if args.cmd == "stream":
                return cmd_stream(client, hz=args.hz, samples=args.samples)
            print(f"Unknown command: {args.cmd}", file=sys.stderr)
            return 2
        except KeyboardInterrupt:
            return 130
        except Exception as exc:
            print(f"SSM communication error: {exc}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
