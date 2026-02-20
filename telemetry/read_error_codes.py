#!/usr/bin/env python3
"""Read Subaru SSM2 diagnostic trouble codes (current + memorized).

This script uses the same serial/SSM framing as ssm_logger.py and decodes
DTC bitfields using FreeSSM's built-in DTC definition table.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

import serial

from ssm_logger import SSM2Client, parse_addr, read_chunked

TELEMETRY_DIR = Path(__file__).resolve().parent
DEFAULT_DTC_DEFS_FILE = TELEMETRY_DIR / "vendor" / "FreeSSM" / "src" / "SSMFlagbyteDefinitions_en.cpp"


@dataclass(frozen=True)
class DtcEntry:
    curr_addr: int
    hist_addr: int
    bit: int  # 1..8
    code: str
    title: str


@dataclass(frozen=True)
class DtcHit:
    addr: int
    bit: int
    code: str
    title: str


def parse_raw_dtc_defs(defs_path: Path, symbol_name: str) -> List[DtcEntry]:
    text = defs_path.read_text(encoding="utf-8", errors="replace")
    marker = f"const QStringList SSMFlagbyteDefinitions_en::{symbol_name} ="
    start = text.find(marker)
    if start < 0:
        raise RuntimeError(f"Could not find {symbol_name} in {defs_path}")

    # Consume from marker until the next ';' that closes the initializer block.
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
    """Mirror FreeSSM's setupDiagnosticCodes() address selection logic.

    Returns:
      (fmt_obd2, [(curr_addr, hist_addr), ...])
    """
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

    # Conservative fallback: old OBD2 base window.
    if not pairs:
        add_range(0x8E, 0xAD, 32)

    return fmt_obd2, pairs


def parse_cu_data(payload: bytes) -> Tuple[str, str, bytes]:
    if not payload or payload[0] != 0xFF:
        raise RuntimeError(f"Unexpected GET_CU_DATA reply: {payload.hex(' ')}")
    if len(payload) < 9:
        raise RuntimeError(f"GET_CU_DATA reply too short: {payload.hex(' ')}")

    sys_id = payload[1:4].hex().upper()
    rom_id = payload[4:9].hex().upper()
    flagbytes = payload[9:]
    return sys_id, rom_id, flagbytes


def decode_hits(
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
                # Explicitly ignored entry in definitions.
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

    # Stable display ordering.
    hits.sort(key=lambda h: (h.code, h.addr, h.bit, h.title))
    return hits


def print_hits(label: str, hits: Sequence[DtcHit]) -> None:
    print(f"\n{label} ({len(hits)}):")
    if not hits:
        print("  - none")
        return
    for h in hits:
        print(f"  - {h.code:<7} {h.title}  [addr=0x{h.addr:04X} bit={h.bit}]")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Read Subaru SSM2 error codes (DTCs)")
    p.add_argument("--port", default="/dev/ttyUSB0", help="Serial port (default: /dev/ttyUSB0)")
    p.add_argument("--baud", type=int, default=4800, help="Baud rate (default: 4800)")
    p.add_argument("--ecu", type=parse_addr, default=0x10, help="ECU address (default: 0x10)")
    p.add_argument("--pad", type=parse_addr, default=0x00, help="Pad address (default: 0x00)")
    p.add_argument(
        "--defs-file",
        default=str(DEFAULT_DTC_DEFS_FILE),
        help="Path to FreeSSM English DTC definitions source",
    )
    p.add_argument("--chunk-size", type=int, default=64, help="Initial SSM address chunk size")
    p.add_argument("--read-retries", type=int, default=3, help="Retries per SSM read chunk")
    p.add_argument("--read-inter-delay", type=float, default=0.03, help="Delay between read retries")
    p.add_argument("--show-empty", action="store_true", help="Always print both sections even when empty")
    return p


def main() -> int:
    args = build_arg_parser().parse_args()

    defs_path = Path(args.defs_file).expanduser()
    if not defs_path.exists():
        print(f"Definitions file not found: {defs_path}", file=sys.stderr)
        return 2

    try:
        with serial.Serial(
            port=args.port,
            baudrate=args.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.05,
        ) as ser:
            client = SSM2Client(ser, ecu_addr=args.ecu, pad_addr=args.pad)

            payload = client.request(bytes([0xBF]), timeout_s=1.2)
            sys_id, rom_id, flagbytes = parse_cu_data(payload)

            fmt_obd2, pairs = enumerate_supported_dtc_addr_pairs(flagbytes)
            if not pairs:
                print("No DTC address ranges are defined for this ECU/flagbyte set.", file=sys.stderr)
                return 3

            defs_symbol = "_DTC_OBD_defs_en" if fmt_obd2 else "_DTC_SUBARU_defs_en"
            entries = parse_raw_dtc_defs(defs_path, defs_symbol)
            defs_by_key = {(e.curr_addr, e.hist_addr, e.bit): e for e in entries}

            addrs = sorted({a for pair in pairs for a in pair})
            values = read_chunked(
                client,
                addrs,
                chunk_size=args.chunk_size,
                retries=args.read_retries,
                inter_delay=args.read_inter_delay,
                best_effort=True,
            )

            current_hits = decode_hits(pairs, values, defs_by_key, use_hist=False)
            historic_hits = decode_hits(pairs, values, defs_by_key, use_hist=True)

            fmt_name = "OBD2-style" if fmt_obd2 else "Subaru-native"
            print(f"ECU SYS_ID={sys_id} ROM_ID={rom_id} format={fmt_name}")
            print(f"Flagbytes={len(flagbytes)}  AddressPairs={len(pairs)}  BytesRead={len(values)}/{len(addrs)}")

            if args.show_empty or current_hits or historic_hits:
                print_hits("Current/Temporary DTCs", current_hits)
                print_hits("Historic/Memorized DTCs", historic_hits)

            if not current_hits and not historic_hits:
                print("\nNo trouble codes currently set.")

            return 0

    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"Failed to read DTCs: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
