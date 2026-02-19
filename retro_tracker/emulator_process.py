from __future__ import annotations

import csv
import io
import subprocess


RA_EMULATOR_PROBE_DEFINITIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("retroarch", ("retroarch",)),
    ("pcsx2", ("pcsx2",)),
    ("duckstation", ("duckstation",)),
    ("ppsspp", ("ppsspp",)),
    ("dolphin", ("dolphin",)),
    ("flycast", ("flycast",)),
    ("bizhawk", ("bizhawk", "emuhawk")),
    ("ralibretro", ("ralibretro",)),
    ("rasnes9x", ("rasnes9x",)),
    ("ravba", ("ravba",)),
    ("rap64", ("rap64",)),
    ("ranes", ("ranes",)),
    ("skyemu", ("skyemu",)),
    ("project64", ("project64",)),
    ("firelight", ("firelight",)),
)

DEFAULT_RA_EMULATOR_PROCESS_HINTS = tuple(
    dict.fromkeys(hint for _, hints in RA_EMULATOR_PROBE_DEFINITIONS for hint in hints)
)


def _normalize_process_name(process_name: str) -> str:
    normalized = process_name.strip().casefold()
    if normalized.endswith(".exe"):
        normalized = normalized[:-4]
    return normalized


def process_matches_ra_emulator(
    process_name: str,
    hints: tuple[str, ...] = DEFAULT_RA_EMULATOR_PROCESS_HINTS,
) -> bool:
    normalized = _normalize_process_name(process_name)
    if not normalized:
        return False
    return any(hint in normalized for hint in hints)


def list_running_process_names(timeout_seconds: int = 3) -> list[str]:
    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=timeout_seconds,
            check=False,
            creationflags=create_no_window,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0 or not result.stdout:
        return []

    names: list[str] = []
    reader = csv.reader(io.StringIO(result.stdout))
    for row in reader:
        if not row:
            continue
        name = row[0].strip()
        if name:
            names.append(name)
    return names


def detect_ra_emulator_probe_matches(timeout_seconds: int = 3) -> dict[str, list[str]]:
    process_names = list_running_process_names(timeout_seconds=timeout_seconds)
    normalized_pairs = [(name, _normalize_process_name(name)) for name in process_names]
    probe_matches: dict[str, list[str]] = {}
    for probe_name, hints in RA_EMULATOR_PROBE_DEFINITIONS:
        matches: list[str] = []
        for raw_name, normalized in normalized_pairs:
            if not normalized:
                continue
            if any(hint in normalized for hint in hints):
                matches.append(raw_name)
        probe_matches[probe_name] = matches
    return probe_matches


def detect_ra_emulator_probe_states(timeout_seconds: int = 3) -> dict[str, bool]:
    matches = detect_ra_emulator_probe_matches(timeout_seconds=timeout_seconds)
    return {probe_name: bool(found) for probe_name, found in matches.items()}


def detect_ra_emulator_live(
    hints: tuple[str, ...] = DEFAULT_RA_EMULATOR_PROCESS_HINTS,
    timeout_seconds: int = 3,
) -> bool:
    if hints == DEFAULT_RA_EMULATOR_PROCESS_HINTS:
        states = detect_ra_emulator_probe_states(timeout_seconds=timeout_seconds)
        return any(states.values())
    process_names = list_running_process_names(timeout_seconds=timeout_seconds)
    return any(process_matches_ra_emulator(name, hints=hints) for name in process_names)
