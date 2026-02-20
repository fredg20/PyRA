from __future__ import annotations

import ctypes
import csv
import io
import os
import re
import subprocess
from ctypes import wintypes


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

COMMON_ROM_FILE_EXTENSIONS: tuple[str, ...] = (
    "7z",
    "a26",
    "a52",
    "a78",
    "bin",
    "chd",
    "cia",
    "ciso",
    "cso",
    "cue",
    "dol",
    "elf",
    "fds",
    "gb",
    "gba",
    "gbc",
    "gcm",
    "gen",
    "gg",
    "iso",
    "j64",
    "lnx",
    "m3u",
    "md",
    "nds",
    "nes",
    "ngc",
    "ngp",
    "nsp",
    "n64",
    "pbp",
    "pce",
    "rom",
    "sfc",
    "smc",
    "sms",
    "sg",
    "sgx",
    "v64",
    "wad",
    "wbfs",
    "ws",
    "wsc",
    "xci",
    "z64",
)

NO_GAME_MARKERS: tuple[str, ...] = (
    "no game",
    "no game loaded",
    "game not loaded",
    "no disc",
    "disc not inserted",
    "no rom",
    "rom not loaded",
    "no content",
    "nothing loaded",
    "main menu",
    "menu principal",
    "aucun jeu",
    "pas de jeu",
    "bios menu",
)

GENERIC_TITLE_TOKENS: set[str] = {
    "alpha",
    "amd64",
    "appimage",
    "avx",
    "avx2",
    "beta",
    "build",
    "canary",
    "debug",
    "dev",
    "experimental",
    "git",
    "gtk",
    "headless",
    "master",
    "nightly",
    "portable",
    "preview",
    "qt",
    "release",
    "rev",
    "r",
    "sse2",
    "sse3",
    "sse4",
    "stable",
    "test",
    "ui",
    "v",
    "version",
    "wx",
    "x64",
    "x86",
}

CORE_NAME_KEYWORDS: set[str] = {
    "beetle",
    "bsnes",
    "desmume",
    "dinothawr",
    "dolphin",
    "dosbox",
    "fbneo",
    "fceumm",
    "flycast",
    "gambatte",
    "genesis",
    "genesisplusgx",
    "gpsp",
    "mgba",
    "mednafen",
    "melon",
    "melonDS",
    "mesen",
    "mupen",
    "mupen64plus",
    "nestopia",
    "parallel",
    "pcsx",
    "pcsxrearmed",
    "picodrive",
    "ppsspp",
    "puae",
    "sameboy",
    "snes9x",
    "stella",
    "swanstation",
    "vba",
    "vbam",
    "vice",
    "yabause",
    "yabasanshiro",
}

BARE_GAME_TITLE_PROBES: set[str] = {"pcsx2"}

UI_WINDOW_TOKENS: set[str] = {
    "about",
    "audio",
    "bios",
    "browse",
    "browser",
    "choose",
    "choose",
    "dialog",
    "dossier",
    "config",
    "configuration",
    "controller",
    "controllers",
    "debug",
    "display",
    "file",
    "files",
    "folder",
    "graphics",
    "input",
    "lancer",
    "load",
    "open",
    "ouvrir",
    "memory",
    "open",
    "plugin",
    "plugins",
    "preference",
    "preferences",
    "selection",
    "selectionner",
    "select",
    "selector",
    "settings",
    "video",
    "wizard",
    "fichier",
}

UI_TITLE_PHRASES: tuple[str, ...] = (
    "lancer un fichier",
    "ouvrir un fichier",
    "open file",
    "choose file",
    "select file",
    "file browser",
)

UI_CONNECTOR_TOKENS: set[str] = {
    "a",
    "an",
    "de",
    "des",
    "du",
    "l",
    "la",
    "le",
    "les",
    "the",
    "to",
    "un",
    "une",
}

SEPARATOR_PATTERNS: tuple[str, ...] = (" | ", " - ", " : ", " — ", " – ")

TITLE_ALIASES_BY_PROBE: dict[str, tuple[str, ...]] = {
    "retroarch": ("retroarch",),
    "pcsx2": ("pcsx2",),
    "duckstation": ("duckstation",),
    "ppsspp": ("ppsspp",),
    "dolphin": ("dolphin",),
    "flycast": ("flycast",),
    "bizhawk": ("bizhawk", "emuhawk"),
    "ralibretro": ("ralibretro", "ra libretro"),
    "rasnes9x": ("rasnes9x",),
    "ravba": ("ravba",),
    "rap64": ("rap64",),
    "ranes": ("ranes",),
    "skyemu": ("skyemu",),
    "project64": ("project64",),
    "firelight": ("firelight",),
}

NO_GAME_TITLE_PATTERNS_BY_PROBE: dict[str, tuple[re.Pattern[str], ...]] = {
    "ralibretro": (
        re.compile(
            r"^ralibretro\s*-\s*v?\d+(?:[.\-_]\d+)*(?:\s*-\s*[a-z0-9_]{2,24})?$",
            flags=re.IGNORECASE,
        ),
    ),
}

ROM_HINT_RE = re.compile(
    r"\.(?:"
    + "|".join(re.escape(ext) for ext in COMMON_ROM_FILE_EXTENSIONS)
    + r")\b",
    flags=re.IGNORECASE,
)

VERSION_ONLY_RE = re.compile(r"^v?\d+(?:[.\-_][a-z0-9]+)*$", flags=re.IGNORECASE)
USER_HANDLE_RE = re.compile(r"^[a-z0-9_]{2,24}$", flags=re.IGNORECASE)


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


def _safe_int(value: object) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _list_running_process_rows(timeout_seconds: int = 3) -> list[list[str]]:
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
    return [row for row in csv.reader(io.StringIO(result.stdout)) if row]


def list_running_process_entries(timeout_seconds: int = 3) -> list[tuple[str, int]]:
    entries: list[tuple[str, int]] = []
    for row in _list_running_process_rows(timeout_seconds=timeout_seconds):
        name = row[0].strip() if row else ""
        pid = _safe_int(row[1]) if len(row) > 1 else 0
        if name:
            entries.append((name, pid))
    return entries


def list_running_process_names(timeout_seconds: int = 3) -> list[str]:
    return [name for name, _ in list_running_process_entries(timeout_seconds=timeout_seconds)]


def detect_ra_emulator_probe_matches(timeout_seconds: int = 3) -> dict[str, list[str]]:
    process_entries = list_running_process_entries(timeout_seconds=timeout_seconds)
    normalized_pairs = [(name, _normalize_process_name(name)) for name, _ in process_entries]
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


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        key = value.strip()
        if not key:
            continue
        lowered = key.casefold()
        if lowered in seen:
            continue
        seen.add(lowered)
        ordered.append(key)
    return ordered


def _enumerate_visible_window_titles_by_pid() -> dict[int, list[str]]:
    if os.name != "nt":
        return {}
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
    except OSError:
        return {}

    enum_windows = user32.EnumWindows
    enum_windows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
    enum_windows.restype = wintypes.BOOL
    is_window_visible = user32.IsWindowVisible
    is_window_visible.argtypes = [wintypes.HWND]
    is_window_visible.restype = wintypes.BOOL
    get_window_text_length = user32.GetWindowTextLengthW
    get_window_text_length.argtypes = [wintypes.HWND]
    get_window_text_length.restype = ctypes.c_int
    get_window_text = user32.GetWindowTextW
    get_window_text.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    get_window_text.restype = ctypes.c_int
    get_window_thread_process_id = user32.GetWindowThreadProcessId
    get_window_thread_process_id.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    get_window_thread_process_id.restype = wintypes.DWORD

    titles_by_pid: dict[int, list[str]] = {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd: wintypes.HWND, _lparam: wintypes.LPARAM) -> wintypes.BOOL:
        try:
            if not is_window_visible(hwnd):
                return True
            text_len = int(get_window_text_length(hwnd))
            if text_len <= 0:
                return True
            text_buffer = ctypes.create_unicode_buffer(text_len + 1)
            copied = int(get_window_text(hwnd, text_buffer, text_len + 1))
            if copied <= 0:
                return True
            title = text_buffer.value.strip()
            if not title:
                return True
            pid_ref = wintypes.DWORD(0)
            get_window_thread_process_id(hwnd, ctypes.byref(pid_ref))
            pid_value = int(pid_ref.value)
            if pid_value <= 0:
                return True
            title_list = titles_by_pid.setdefault(pid_value, [])
            title_list.append(title)
        except Exception:
            return True
        return True

    try:
        enum_windows(callback, 0)
    except Exception:
        return {}
    return {pid: _dedupe_preserve_order(titles) for pid, titles in titles_by_pid.items() if titles}


def _retroarch_title_indicates_game_loaded(title: str) -> bool:
    normalized = " ".join(title.split()).casefold()
    if not normalized:
        return False
    if normalized in {"retroarch", "retroarch menu", "retroarch - menu"}:
        return False
    if normalized.startswith("retroarch "):
        trailing = normalized[len("retroarch ") :].strip(" -")
        return bool(trailing)
    if normalized.startswith("retroarch - "):
        trailing = normalized[len("retroarch - ") :].strip()
        return bool(trailing) and trailing not in {"menu", "main menu"}
    return False


def _normalize_title(title: str) -> str:
    return " ".join(str(title).split()).strip().casefold()


def _title_has_rom_hint(normalized_title: str) -> bool:
    if not normalized_title:
        return False
    return bool(ROM_HINT_RE.search(normalized_title))


def _looks_like_version_only(text: str) -> bool:
    normalized = _normalize_title(text)
    if not normalized:
        return True
    if VERSION_ONLY_RE.fullmatch(normalized):
        return True
    tokens = [tok for tok in re.findall(r"[a-z0-9]+", normalized) if tok]
    if not tokens:
        return True
    has_letters = any(any(ch.isalpha() for ch in tok) for tok in tokens)
    has_digits = any(any(ch.isdigit() for ch in tok) for tok in tokens)
    if has_digits and all((tok in GENERIC_TITLE_TOKENS) or tok.isdigit() for tok in tokens):
        return True
    if not has_letters and has_digits:
        return True
    return False


def _segment_is_game_like(segment: str, aliases: tuple[str, ...]) -> bool:
    normalized = _normalize_title(segment).strip(" -|:()[]")
    if not normalized:
        return False
    if _looks_like_ui_window_title(normalized):
        return False
    if any(marker in normalized for marker in NO_GAME_MARKERS):
        return False
    if _looks_like_version_only(normalized):
        return False
    if _contains_core_keyword(normalized):
        return False
    tokens = [tok for tok in re.findall(r"[a-z0-9]+", normalized) if tok]
    if not tokens:
        return False
    if any(alias in normalized for alias in aliases):
        return False
    meaningful_words = [tok for tok in tokens if len(tok) >= 3 and tok not in GENERIC_TITLE_TOKENS]
    if not meaningful_words:
        return False
    return True


def _contains_core_keyword(text: str) -> bool:
    normalized = _normalize_title(text)
    if not normalized:
        return False
    tokens = [tok for tok in re.findall(r"[a-z0-9]+", normalized) if tok]
    if not tokens:
        return False
    compact = "".join(tokens)
    if compact in CORE_NAME_KEYWORDS:
        return True
    return any(tok in CORE_NAME_KEYWORDS for tok in tokens)


def _looks_like_ui_window_title(text: str) -> bool:
    normalized = _normalize_title(text)
    if not normalized:
        return True
    if any(phrase in normalized for phrase in UI_TITLE_PHRASES):
        return True
    tokens = [
        tok
        for tok in re.findall(r"[a-z0-9]+", normalized)
        if tok and tok not in UI_CONNECTOR_TOKENS and len(tok) >= 2
    ]
    if not tokens:
        return True
    return all(tok in UI_WINDOW_TOKENS for tok in tokens)


def _looks_like_user_handle(text: str) -> bool:
    normalized = _normalize_title(text)
    if not normalized:
        return False
    if not USER_HANDLE_RE.fullmatch(normalized):
        return False
    if _looks_like_version_only(normalized):
        return False
    if _contains_core_keyword(normalized):
        return False
    return True


def _is_multicore_core_only_title(probe_name: str, normalized_title: str, aliases: tuple[str, ...]) -> bool:
    if probe_name.casefold() not in {"retroarch", "ralibretro"}:
        return False
    if not normalized_title or _title_has_rom_hint(normalized_title):
        return False

    for alias in aliases:
        if normalized_title.startswith(alias + " - "):
            trailing = normalized_title[len(alias) + 3 :].strip()
            break
        if normalized_title.startswith(alias + " "):
            trailing = normalized_title[len(alias) :].strip(" -")
            break
        if normalized_title == alias:
            return True
    else:
        trailing = normalized_title

    if not trailing:
        return True

    split_pattern = r"\s+\|\s+|\s+-\s+|\s+:\s+|\s+—\s+|\s+–\s+"
    segments = [segment.strip() for segment in re.split(split_pattern, trailing) if segment and segment.strip()]
    if not segments:
        return True

    if len(segments) == 1 and _contains_core_keyword(segments[0]):
        return True

    return all(
        _looks_like_version_only(segment)
        or _contains_core_keyword(segment)
        or _looks_like_user_handle(segment)
        for segment in segments
    )


def _generic_title_indicates_game_loaded(title: str, aliases: tuple[str, ...]) -> bool:
    normalized = _normalize_title(title)
    if not normalized:
        return False
    if any(marker in normalized for marker in NO_GAME_MARKERS):
        return False
    if _title_has_rom_hint(normalized):
        return True
    if normalized in aliases:
        return False

    for alias in aliases:
        if normalized.startswith(alias + " "):
            trailing = normalized[len(alias) :].strip(" -|:")
            if trailing:
                split_pattern = r"\s+\|\s+|\s+-\s+|\s+:\s+|\s+—\s+|\s+–\s+"
                trailing_segments = [segment for segment in re.split(split_pattern, trailing) if segment]
                if any(_segment_is_game_like(segment, aliases) for segment in trailing_segments):
                    return True
                if _segment_is_game_like(trailing, aliases):
                    return True
                if _looks_like_version_only(trailing):
                    continue
        if normalized.startswith(alias + " - "):
            trailing = normalized[len(alias) + 3 :].strip()
            split_pattern = r"\s+\|\s+|\s+-\s+|\s+:\s+|\s+—\s+|\s+–\s+"
            trailing_segments = [segment for segment in re.split(split_pattern, trailing) if segment]
            if any(_segment_is_game_like(segment, aliases) for segment in trailing_segments):
                return True
            if _segment_is_game_like(trailing, aliases):
                return True
            continue
        for sep in SEPARATOR_PATTERNS:
            suffix = f"{sep}{alias}"
            if normalized.endswith(suffix):
                leading = normalized[: -len(suffix)].strip()
                if _segment_is_game_like(leading, aliases):
                    return True
                continue

    split_pattern = r"\s+\|\s+|\s+-\s+|\s+:\s+|\s+—\s+|\s+–\s+"
    segments = [segment for segment in re.split(split_pattern, normalized) if segment]
    if len(segments) >= 2 and any(_segment_is_game_like(segment, aliases) for segment in segments):
        return True
    return False


def _title_indicates_game_loaded(
    probe_name: str,
    title: str,
    peer_titles: tuple[str, ...] = (),
) -> bool:
    normalized = _normalize_title(title)
    if not normalized:
        return False
    for deny_pattern in NO_GAME_TITLE_PATTERNS_BY_PROBE.get(probe_name.casefold(), ()):
        if deny_pattern.search(normalized):
            return False

    aliases = TITLE_ALIASES_BY_PROBE.get(probe_name, (probe_name.casefold(),))
    if _is_multicore_core_only_title(probe_name, normalized, aliases):
        return False
    if probe_name.casefold() == "retroarch":
        return _retroarch_title_indicates_game_loaded(title)
    detected = _generic_title_indicates_game_loaded(title, aliases)
    if detected:
        return True
    if probe_name.casefold() in BARE_GAME_TITLE_PROBES:
        if probe_name.casefold() == "pcsx2":
            normalized_peers = [_normalize_title(candidate) for candidate in peer_titles if candidate]
            if any(_looks_like_ui_window_title(candidate) for candidate in normalized_peers):
                return False
        if _contains_core_keyword(normalized):
            return False
        if _looks_like_version_only(normalized):
            return False
        if _looks_like_ui_window_title(normalized):
            return False
        return _segment_is_game_like(normalized, aliases)
    return False


def collect_ra_emulator_window_titles_by_probe(
    probe_matches: dict[str, list[str]] | None = None,
    timeout_seconds: int = 3,
) -> dict[str, list[str]]:
    titles_by_probe: dict[str, list[str]] = {probe_name: [] for probe_name, _ in RA_EMULATOR_PROBE_DEFINITIONS}
    effective_matches = probe_matches if isinstance(probe_matches, dict) else detect_ra_emulator_probe_matches(
        timeout_seconds=timeout_seconds
    )
    if not any(bool(matches) for matches in effective_matches.values()):
        return titles_by_probe

    process_entries = list_running_process_entries(timeout_seconds=timeout_seconds)
    if not process_entries:
        return titles_by_probe

    titles_by_pid = _enumerate_visible_window_titles_by_pid()
    if not titles_by_pid:
        return titles_by_probe

    normalized_entries = [
        (name, pid, _normalize_process_name(name))
        for name, pid in process_entries
        if name and pid > 0
    ]

    for probe_name, hints in RA_EMULATOR_PROBE_DEFINITIONS:
        if not effective_matches.get(probe_name):
            continue
        found_titles: list[str] = []
        for _raw_name, pid, normalized in normalized_entries:
            if not normalized or not any(hint in normalized for hint in hints):
                continue
            pid_titles = titles_by_pid.get(pid, [])
            if not pid_titles:
                continue
            found_titles.extend(pid_titles)
        titles_by_probe[probe_name] = _dedupe_preserve_order(found_titles)

    return titles_by_probe


def detect_ra_emulator_game_probe_states(
    probe_matches: dict[str, list[str]] | None = None,
    timeout_seconds: int = 3,
    window_titles_by_probe: dict[str, list[str]] | None = None,
) -> dict[str, bool]:
    states: dict[str, bool] = {probe_name: False for probe_name, _ in RA_EMULATOR_PROBE_DEFINITIONS}
    effective_matches = probe_matches if isinstance(probe_matches, dict) else detect_ra_emulator_probe_matches(
        timeout_seconds=timeout_seconds
    )
    if not any(bool(matches) for matches in effective_matches.values()):
        return states
    titles_by_probe = (
        window_titles_by_probe
        if isinstance(window_titles_by_probe, dict)
        else collect_ra_emulator_window_titles_by_probe(
            probe_matches=effective_matches,
            timeout_seconds=timeout_seconds,
        )
    )

    for probe_name, hints in RA_EMULATOR_PROBE_DEFINITIONS:
        _ = hints
        if not effective_matches.get(probe_name):
            continue
        titles = [title for title in titles_by_probe.get(probe_name, []) if str(title).strip()]
        states[probe_name] = any(
            _title_indicates_game_loaded(probe_name, title, tuple(titles))
            for title in titles
        )

    return states
