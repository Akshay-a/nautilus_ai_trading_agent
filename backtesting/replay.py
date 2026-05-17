"""Helpers to extract deterministic replay decisions from historical strategy logs."""

from __future__ import annotations

import gzip
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


_SIGNAL_RE = re.compile(r"🤖 Signal:\s*(BUY|SELL|HOLD)\s*\|\s*Confidence:\s*(LOW|MEDIUM|HIGH)")


@dataclass(frozen=True)
class ReplayDecision:
    """Parsed decision record from a historical runtime log line."""

    ts_ns: int
    timestamp: str
    signal: str
    confidence: str
    source_file: str


def _parse_timestamp_to_ns(value: str) -> int:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return int(dt.timestamp() * 1_000_000_000)


def _open_text_file(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="ignore")
    return path.open("r", encoding="utf-8", errors="ignore")


def _iter_log_paths(log_patterns: Iterable[str]) -> list[Path]:
    paths: list[Path] = []
    for pattern in log_patterns:
        paths.extend(Path().glob(pattern))
    return sorted({p.resolve() for p in paths})


def extract_replay_decisions(
    log_patterns: list[str],
    start_time_ns: int | None = None,
    end_time_ns: int | None = None,
) -> list[ReplayDecision]:
    """
    Extract deterministic BUY/SELL/HOLD decisions from JSON log lines.

    Decisions are deduplicated by nanosecond timestamp, preserving the latest line
    for a given timestamp.
    """
    parsed: dict[int, ReplayDecision] = {}

    for path in _iter_log_paths(log_patterns):
        with _open_text_file(path) as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue

                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                message = str(event.get("message", ""))
                match = _SIGNAL_RE.search(message)
                if not match:
                    continue

                ts_raw = event.get("timestamp")
                if not ts_raw:
                    continue

                ts_ns = _parse_timestamp_to_ns(str(ts_raw))
                if start_time_ns is not None and ts_ns < start_time_ns:
                    continue
                if end_time_ns is not None and ts_ns > end_time_ns:
                    continue

                signal, confidence = match.group(1), match.group(2)
                parsed[ts_ns] = ReplayDecision(
                    ts_ns=ts_ns,
                    timestamp=str(ts_raw),
                    signal=signal,
                    confidence=confidence,
                    source_file=str(path),
                )

    return [parsed[key] for key in sorted(parsed)]


def write_replay_csv(decisions: list[ReplayDecision], output_path: Path) -> Path:
    """Write replay decisions to CSV for deterministic strategy consumption."""
    import csv

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["ts_ns", "timestamp", "signal", "confidence", "source_file"],
        )
        writer.writeheader()
        for item in decisions:
            writer.writerow(
                {
                    "ts_ns": item.ts_ns,
                    "timestamp": item.timestamp,
                    "signal": item.signal,
                    "confidence": item.confidence,
                    "source_file": item.source_file,
                },
            )

    return output_path
