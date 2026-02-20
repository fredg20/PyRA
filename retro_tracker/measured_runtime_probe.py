from __future__ import annotations

import os
import re
import time
from pathlib import Path


MAX_INITIAL_READ_BYTES = 256 * 1024

_RATIO_AFTER_KEYWORD_RE = re.compile(
    r"(?i)(?:measured|measure|progress)[^0-9]{0,32}([0-9]+(?:[.,][0-9]+)?)\s*/\s*([0-9]+(?:[.,][0-9]+)?)"
)
_PERCENT_AFTER_KEYWORD_RE = re.compile(
    r"(?i)(?:measured|measure|progress)[^0-9]{0,32}([0-9]{1,3}(?:[.,][0-9]+)?)\s*%"
)
_GENERIC_RATIO_RE = re.compile(r"([0-9]+(?:[.,][0-9]+)?)\s*/\s*([0-9]+(?:[.,][0-9]+)?)")
_GENERIC_PERCENT_RE = re.compile(r"([0-9]{1,3}(?:[.,][0-9]+)?)\s*%")
_ACHIEVEMENT_ID_RE = re.compile(r"(?i)(?:achievement|cheevo|id)[^0-9]{0,10}#?\s*([0-9]{1,9})")
_QUOTED_TITLE_RE = re.compile(r"[\"']([^\"']{2,120})[\"']")


def _safe_float(value: object) -> float | None:
    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _safe_int(value: object) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _format_numeric(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _line_looks_like_ra_measured(line: str) -> bool:
    lowered = line.casefold()
    if "measured" not in lowered and "measure" not in lowered and "progress" not in lowered:
        return False
    return any(
        marker in lowered
        for marker in (
            "retroach",
            "rcheev",
            "achievement",
            "cheevo",
            "[ra]",
            " retro ",
        )
    )


def _extract_title_from_line(line: str) -> str:
    quoted = _QUOTED_TITLE_RE.search(line)
    if quoted:
        return quoted.group(1).strip()
    if ":" in line:
        rhs = line.rsplit(":", 1)[-1].strip()
        if len(rhs) >= 3 and len(rhs) <= 120:
            return rhs
    return ""


def _parse_measured_line(line: str, emulator_name: str, source_path: Path) -> dict[str, str] | None:
    raw = line.strip()
    if not raw:
        return None
    if not _line_looks_like_ra_measured(raw):
        return None

    current_value: float | None = None
    total_value: float | None = None
    percent_value: float | None = None

    ratio_match = _RATIO_AFTER_KEYWORD_RE.search(raw) or _GENERIC_RATIO_RE.search(raw)
    if ratio_match:
        current_value = _safe_float(ratio_match.group(1))
        total_value = _safe_float(ratio_match.group(2))
        if current_value is not None and total_value is not None and total_value > 0:
            percent_value = max(0.0, min(100.0, (current_value / total_value) * 100.0))

    percent_match = _PERCENT_AFTER_KEYWORD_RE.search(raw) or _GENERIC_PERCENT_RE.search(raw)
    if percent_match:
        parsed_pct = _safe_float(percent_match.group(1))
        if parsed_pct is not None:
            percent_value = max(0.0, min(100.0, parsed_pct))

    if current_value is None and percent_value is None:
        return None

    achievement_id = _safe_int(_ACHIEVEMENT_ID_RE.search(raw).group(1)) if _ACHIEVEMENT_ID_RE.search(raw) else 0
    title = _extract_title_from_line(raw)

    measured_chunks: list[str] = []
    if current_value is not None and total_value is not None and total_value > 0:
        measured_chunks.append(f"{_format_numeric(current_value)}/{_format_numeric(total_value)}")
    elif current_value is not None:
        measured_chunks.append(_format_numeric(current_value))
    if percent_value is not None:
        measured_chunks.append(f"{_format_numeric(percent_value)}%")
    measured_text = " | ".join(measured_chunks) if measured_chunks else raw
    signature = "|".join(
        [
            emulator_name,
            str(achievement_id),
            _format_numeric(current_value) if current_value is not None else "-",
            _format_numeric(total_value) if total_value is not None else "-",
            _format_numeric(percent_value) if percent_value is not None else "-",
            title.casefold(),
        ]
    )
    return {
        "emulator": emulator_name,
        "achievement_id": str(achievement_id),
        "title": title,
        "measured_text": measured_text,
        "measured_percent": _format_numeric(percent_value) if percent_value is not None else "",
        "measured_current": _format_numeric(current_value) if current_value is not None else "",
        "measured_total": _format_numeric(total_value) if total_value is not None else "",
        "source": str(source_path),
        "raw_line": raw,
        "signature": signature,
    }


def _retroarch_log_candidates() -> list[Path]:
    candidates: list[Path] = []
    for env_key in ("PYRA_RETROARCH_LOG", "RETROARCH_LOG_PATH"):
        raw = os.getenv(env_key, "").strip()
        if raw:
            candidates.append(Path(raw))

    appdata = Path(os.getenv("APPDATA", "")).expanduser() if os.getenv("APPDATA") else Path.home()
    localappdata = Path(os.getenv("LOCALAPPDATA", "")).expanduser() if os.getenv("LOCALAPPDATA") else Path.home()
    home = Path.home()

    candidates.extend(
        [
            appdata / "RetroArch" / "logs" / "retroarch.log",
            appdata / "RetroArch" / "retroarch.log",
            localappdata / "RetroArch" / "logs" / "retroarch.log",
            localappdata / "RetroArch" / "retroarch.log",
            home / "AppData" / "Roaming" / "RetroArch" / "logs" / "retroarch.log",
            home / "AppData" / "Roaming" / "RetroArch" / "retroarch.log",
        ]
    )
    return candidates


def _generic_emulator_log_candidates(emulator_name: str) -> list[Path]:
    appdata = Path(os.getenv("APPDATA", "")).expanduser() if os.getenv("APPDATA") else Path.home()
    localappdata = Path(os.getenv("LOCALAPPDATA", "")).expanduser() if os.getenv("LOCALAPPDATA") else Path.home()
    home = Path.home()
    docs = home / "Documents"
    emu = emulator_name.strip()
    if not emu:
        return []
    emu_upper = emu.upper()
    return [
        appdata / emu / "logs" / f"{emu}.log",
        appdata / emu / "Logs" / f"{emu}.log",
        appdata / emu / f"{emu}.log",
        appdata / emu_upper / "logs" / f"{emu}.log",
        appdata / emu_upper / "Logs" / f"{emu}.log",
        appdata / emu_upper / f"{emu}.log",
        localappdata / emu / "logs" / f"{emu}.log",
        localappdata / emu / "Logs" / f"{emu}.log",
        localappdata / emu / f"{emu}.log",
        localappdata / emu_upper / "logs" / f"{emu}.log",
        localappdata / emu_upper / "Logs" / f"{emu}.log",
        localappdata / emu_upper / f"{emu}.log",
        docs / emu / "logs" / f"{emu}.log",
        docs / emu / "Logs" / f"{emu}.log",
        docs / emu / f"{emu}.log",
        docs / emu_upper / "logs" / f"{emu}.log",
        docs / emu_upper / "Logs" / f"{emu}.log",
        docs / emu_upper / f"{emu}.log",
    ]


def _emulator_log_candidates(emulator_name: str) -> list[Path]:
    env_key = f"PYRA_{emulator_name.upper()}_LOG"
    env_paths: list[Path] = []
    raw_env = os.getenv(env_key, "").strip()
    if raw_env:
        env_paths.append(Path(raw_env))
    raw_global_env = os.getenv("PYRA_EMULATOR_LOG", "").strip()
    if raw_global_env:
        env_paths.append(Path(raw_global_env))

    appdata = Path(os.getenv("APPDATA", "")).expanduser() if os.getenv("APPDATA") else Path.home()
    localappdata = Path(os.getenv("LOCALAPPDATA", "")).expanduser() if os.getenv("LOCALAPPDATA") else Path.home()
    home = Path.home()
    documents = home / "Documents"

    hints: dict[str, list[Path]] = {
        "retroarch": _retroarch_log_candidates(),
        "pcsx2": [
            appdata / "PCSX2" / "logs" / "emulog.txt",
            appdata / "PCSX2" / "logs" / "pcsx2.log",
            home / "Documents" / "PCSX2" / "logs" / "emulog.txt",
            localappdata / "PCSX2" / "logs" / "emulog.txt",
        ],
        "duckstation": [
            appdata / "DuckStation" / "logs" / "duckstation.log",
            localappdata / "DuckStation" / "logs" / "duckstation.log",
        ],
        "ppsspp": [
            appdata / "PPSSPP" / "PSP" / "SYSTEM" / "ppsspp.log",
            home / "Documents" / "PPSSPP" / "PSP" / "SYSTEM" / "ppsspp.log",
        ],
        "dolphin": [
            home / "Documents" / "Dolphin Emulator" / "Logs" / "dolphin.log",
            appdata / "Dolphin Emulator" / "Logs" / "dolphin.log",
            localappdata / "Dolphin Emulator" / "Logs" / "dolphin.log",
        ],
        "flycast": [
            appdata / "Flycast" / "logs" / "flycast.log",
            appdata / "flycast" / "logs" / "flycast.log",
            localappdata / "Flycast" / "logs" / "flycast.log",
            localappdata / "flycast" / "logs" / "flycast.log",
        ],
        "bizhawk": [
            appdata / "BizHawk" / "logs" / "emuhawk.log",
            home / "Documents" / "BizHawk" / "logs" / "emuhawk.log",
            localappdata / "BizHawk" / "logs" / "emuhawk.log",
        ],
        "project64": [
            appdata / "Project64" / "Logs" / "project64.log",
            localappdata / "Project64" / "Logs" / "project64.log",
        ],
        "ralibretro": [
            appdata / "RALibretro" / "logs" / "ralibretro.log",
            localappdata / "RALibretro" / "logs" / "ralibretro.log",
            documents / "RALibretro" / "logs" / "ralibretro.log",
        ],
        "rasnes9x": [
            appdata / "RASnes9x" / "logs" / "rasnes9x.log",
            localappdata / "RASnes9x" / "logs" / "rasnes9x.log",
            documents / "RASnes9x" / "logs" / "rasnes9x.log",
        ],
        "ravba": [
            appdata / "RAVBA" / "logs" / "ravba.log",
            localappdata / "RAVBA" / "logs" / "ravba.log",
            documents / "RAVBA" / "logs" / "ravba.log",
        ],
        "rap64": [
            appdata / "RAP64" / "logs" / "rap64.log",
            localappdata / "RAP64" / "logs" / "rap64.log",
            documents / "RAP64" / "logs" / "rap64.log",
        ],
        "ranes": [
            appdata / "RANes" / "logs" / "ranes.log",
            localappdata / "RANes" / "logs" / "ranes.log",
            documents / "RANes" / "logs" / "ranes.log",
        ],
        "skyemu": [
            appdata / "SkyEmu" / "logs" / "skyemu.log",
            localappdata / "SkyEmu" / "logs" / "skyemu.log",
            documents / "SkyEmu" / "logs" / "skyemu.log",
        ],
        "firelight": [
            appdata / "Firelight" / "logs" / "firelight.log",
            localappdata / "Firelight" / "logs" / "firelight.log",
            documents / "Firelight" / "logs" / "firelight.log",
        ],
    }

    candidates = list(env_paths)
    candidates.extend(hints.get(emulator_name, []))
    candidates.extend(_generic_emulator_log_candidates(emulator_name))

    deduped: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        norm = str(path).strip().casefold()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        deduped.append(path)
    return deduped


def _read_incremental_lines(path: Path, offsets: dict[str, int]) -> list[str]:
    try:
        resolved = str(path.resolve())
    except OSError:
        resolved = str(path)

    try:
        stat = path.stat()
    except OSError:
        return []
    file_size = int(stat.st_size)
    if file_size <= 0:
        offsets[resolved] = 0
        return []

    previous_offset = offsets.get(resolved, -1)
    if previous_offset < 0 or previous_offset > file_size:
        start_offset = max(0, file_size - MAX_INITIAL_READ_BYTES)
    else:
        start_offset = previous_offset

    if start_offset >= file_size:
        offsets[resolved] = file_size
        return []

    try:
        with path.open("rb") as handle:
            handle.seek(start_offset)
            raw_bytes = handle.read(file_size - start_offset)
    except OSError:
        return []

    offsets[resolved] = file_size
    if not raw_bytes:
        return []
    return raw_bytes.decode("utf-8", errors="ignore").splitlines()


def probe_runtime_measured_progress(
    probe_matches: dict[str, list[str]],
    state: dict[str, object] | None = None,
) -> tuple[dict[str, object], dict[str, str] | None]:
    next_state = dict(state) if isinstance(state, dict) else {}
    offsets_raw = next_state.get("offsets")
    offsets: dict[str, int] = offsets_raw if isinstance(offsets_raw, dict) else {}
    next_state["offsets"] = offsets

    active_emulators = sorted(name for name, matches in probe_matches.items() if bool(matches))
    if not active_emulators:
        next_state["last_event"] = {}
        next_state["last_emulator"] = ""
        return next_state, None

    # Avoid false attribution if multiple emulators are running at the same time.
    if len(active_emulators) != 1:
        return next_state, None

    emulator_name = active_emulators[0]
    latest_event: dict[str, str] | None = None
    for candidate_path in _emulator_log_candidates(emulator_name):
        lines = _read_incremental_lines(candidate_path, offsets)
        if not lines:
            continue
        for line in reversed(lines):
            parsed = _parse_measured_line(line, emulator_name=emulator_name, source_path=candidate_path)
            if parsed is not None:
                latest_event = parsed
                break
        if latest_event is not None:
            break

    if latest_event is not None:
        next_state["last_event"] = dict(latest_event)
        next_state["last_signature"] = latest_event.get("signature", "")
        next_state["last_event_monotonic"] = time.monotonic()
        next_state["last_emulator"] = emulator_name
        return next_state, latest_event

    cached = next_state.get("last_event")
    if isinstance(cached, dict) and str(cached.get("emulator", "")).strip().casefold() == emulator_name.casefold():
        return next_state, {str(k): str(v) for k, v in cached.items()}
    return next_state, None
