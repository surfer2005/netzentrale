"""Betriebssystem-Unterschiede an einer Stelle (macOS, Windows, Linux)."""
from __future__ import annotations

import json
import os
import subprocess
import sys

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")

# Windows: Kommandozeilenprogramme ohne aufblitzendes Konsolenfenster starten
CREATE_NO_WINDOW = 0x08000000
# Windows-Konsolenprogramme (ping, ipconfig …) schreiben in der OEM-Codepage (DE: cp850)
CONSOLE_ENCODING = "oem" if IS_WIN else "utf-8"


def popen_kwargs() -> dict:
    return {"creationflags": CREATE_NO_WINDOW} if IS_WIN else {}


def run(cmd: list[str], timeout: float = 10, encoding: str | None = None) -> str:
    """Befehl ausführen, stdout als Text. Fehler → leerer Text."""
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, **popen_kwargs())
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    return (r.stdout or b"").decode(encoding or CONSOLE_ENCODING, errors="replace")


def run_rc(cmd: list[str], timeout: float = 10) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, **popen_kwargs())
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return -1, ""
    return r.returncode, (r.stdout or b"").decode(CONSOLE_ENCODING, errors="replace")


def powershell_json(script: str, timeout: float = 15) -> list[dict]:
    """PowerShell-Befehl ausführen, Ergebnis per ConvertTo-Json als Liste von Objekten."""
    if not IS_WIN:
        return []
    out = run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
               "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + script + " | ConvertTo-Json -Depth 3"],
              timeout=timeout, encoding="utf-8")
    out = out.strip().lstrip("\ufeff")
    if not out:
        return []
    try:
        data = json.loads(out)
    except ValueError:
        return []
    return data if isinstance(data, list) else [data]


def install_no_console_patch():
    """Windows: alle subprocess-Aufrufe der App ohne Konsolenfenster (auch aus Bibliotheken)."""
    if not IS_WIN:
        return
    orig = subprocess.Popen.__init__

    def patched(self, *a, **k):
        k.setdefault("creationflags", CREATE_NO_WINDOW)
        orig(self, *a, **k)
    subprocess.Popen.__init__ = patched


def open_path(target: str):
    """Datei/URL mit dem Standardprogramm öffnen."""
    if IS_WIN:
        os.startfile(target)  # noqa: S606 – gewolltes Öffnen
        return
    cmd = ["open", target] if IS_MAC else ["xdg-open", target]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise OSError((r.stderr or r.stdout or "Start fehlgeschlagen").strip())


def program_files() -> list[str]:
    return [p for p in {os.environ.get("ProgramFiles", r"C:\Program Files"),
                        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")} if p]
