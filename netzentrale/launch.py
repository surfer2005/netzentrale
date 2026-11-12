"""Startet externe Clients (VNC, SSH, Browser, RDP, Dateien, RustDesk) – macOS, Windows, Linux."""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote

from .osinfo import IS_MAC, IS_WIN, open_path, popen_kwargs, program_files
from .storage import DEFAULT_VNC_CLIENT, Device, data_dir


class LaunchError(Exception):
    pass


def _open(target: str):
    """Datei/URL mit dem Standardprogramm öffnen."""
    try:
        open_path(target)
    except OSError as e:
        raise LaunchError(str(e) or "Start fehlgeschlagen") from e


def _find_app(mac_patterns: list[str], win_patterns: list[str] = (), linux_cmds: list[str] = ()) -> str | None:
    """Installiertes Programm finden: macOS .app, Windows .exe, Linux Befehl."""
    if IS_MAC:
        for base in ("/Applications", str(Path.home() / "Applications")):
            for p in mac_patterns:
                hits = sorted(glob.glob(f"{base}/{p}"))
                if hits:
                    return hits[-1]  # neueste Version
        return None
    if IS_WIN:
        for base in program_files():
            for p in win_patterns:
                hits = sorted(glob.glob(os.path.join(base, p)))
                if hits:
                    return hits[-1]
        for p in win_patterns:   # auch im PATH (z. B. per winget/scoop installiert)
            exe = shutil.which(Path(p).name)
            if exe:
                return exe
        return None
    for c in linux_cmds:
        if shutil.which(c):
            return shutil.which(c)
    return None


def _spawn(cmd: list[str]):
    try:
        subprocess.Popen(cmd, **popen_kwargs())
    except OSError as e:
        raise LaunchError(f"{cmd[0]} konnte nicht gestartet werden: {e}") from e


def _session_file(name: str, suffix: str, content: str) -> str:
    d = data_dir() / "sitzungen"
    d.mkdir(exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:40] or "verbindung"
    path = d / f"{safe}{suffix}"
    path.write_text(content, encoding="utf-8")
    return str(path)


# --- VNC -------------------------------------------------------------------
def realvnc_installed() -> str | None:
    return _find_app(["VNC Viewer.app", "RealVNC Viewer.app", "RealVNC*/VNC Viewer.app"],
                     [r"RealVNC\VNC Viewer\vncviewer.exe", r"RealVNC\*\vncviewer.exe"], ["vncviewer"])


def tigervnc_installed() -> str | None:
    return _find_app(["TigerVNC*.app"], [r"TigerVNC\vncviewer*.exe", r"TigerVNC*\vncviewer*.exe"],
                     ["xtigervncviewer", "vncviewer"])


def open_vnc(dev: Device):
    cfg = dev.vnc or {}
    port = int(cfg.get("port", 5900))
    client = cfg.get("client", DEFAULT_VNC_CLIENT)
    host = dev.host

    # Geräteliste vom Mac unter Windows/Linux: Apple-Client gibt es dort nicht → TigerVNC
    if client == "apple" and not IS_MAC:
        client = "tigervnc"
    # gewählter Client fehlt, der andere ist da → einfach den vorhandenen nehmen
    if client == "realvnc" and not realvnc_installed() and tigervnc_installed():
        client = "tigervnc"
    elif client == "tigervnc" and not tigervnc_installed() and realvnc_installed():
        client = "realvnc"

    if client == "apple":
        _open(f"vnc://{host}:{port}")
        return

    if client == "tigervnc":
        app = tigervnc_installed()
        if not app:
            raise LaunchError("TigerVNC ist nicht installiert (tigervnc.org → „TigerVNC Viewer“).")
        if IS_MAC:
            _spawn(["open", "-n", "-a", app, "--args", f"{host}::{port}"])
        else:
            _spawn([app, f"{host}::{port}"])
        return

    # RealVNC: Verbindungsdatei (.vnc) erzeugen und mit dem Viewer öffnen
    app = realvnc_installed()
    if not app:
        raise LaunchError("RealVNC Viewer ist nicht installiert (realvnc.com → „RealVNC Viewer“).")
    path = _session_file(dev.name, ".vnc", f"[Connection]\nHost={host}:{port}\n")
    if IS_MAC:
        _spawn(["open", "-a", app, path])
    else:
        _spawn([app, "-config", path])


# --- SSH -------------------------------------------------------------------
def open_ssh(dev: Device):
    cfg = dev.ssh or {}
    port = int(cfg.get("port", 22))
    user = (cfg.get("user") or "").strip()
    if IS_WIN:
        # Windows 10/11 bringen den OpenSSH-Client mit; bevorzugt im Windows-Terminal öffnen
        target = f"{user}@{dev.host}" if user else dev.host
        ssh = ["ssh", "-p", str(port), target]
        if shutil.which("wt"):
            _spawn(["wt", "new-tab", "--title", dev.name, *ssh])
        else:
            _spawn(["cmd", "/c", "start", f"SSH {dev.name}", *ssh])
        return
    target = f"{quote(user)}@{dev.host}" if user else dev.host
    # Terminal.app (und iTerm) verarbeiten ssh://-Links direkt
    _open(f"ssh://{target}:{port}" if port != 22 else f"ssh://{target}")


# --- Web -------------------------------------------------------------------
def open_web(dev: Device):
    url = (dev.web or {}).get("url") or f"http://{dev.host}"
    if "://" not in url:
        url = "http://" + url
    _open(url)


# --- RDP -------------------------------------------------------------------
def open_rdp(dev: Device):
    cfg = dev.rdp or {}
    port = int(cfg.get("port", 3389))
    lines = [f"full address:s:{dev.host}:{port}", "screen mode id:i:1", "prompt for credentials on client:i:1"]
    if cfg.get("user"):
        lines.append(f"username:s:{cfg['user']}")
    path = _session_file(dev.name, ".rdp", "\n".join(lines) + "\n")
    if IS_WIN:
        _spawn(["mstsc", path])          # Remotedesktopverbindung ist bei Windows dabei
        return
    if IS_MAC and not _find_app(["Windows App.app", "Microsoft Remote Desktop.app"]):
        raise LaunchError("Für RDP wird die kostenlose „Windows App“ von Microsoft (App Store) benötigt.")
    _open(path)


# --- RustDesk (Fernhilfe übers Internet) -------------------------------------
RUSTDESK_MODES = {"connect": "Fernsteuern", "file-transfer": "Dateien übertragen", "view-camera": "Kamera ansehen"}


def rustdesk_installed() -> str | None:
    return _find_app(["RustDesk.app"], [r"RustDesk\rustdesk.exe"], ["rustdesk"])


def clean_rustdesk_id(raw: str) -> str:
    """'123 456 789' → '123456789' (Leerzeichen, wie RustDesk sie anzeigt, entfernen)."""
    return "".join(raw.split())


def open_rustdesk_id(rid: str, mode: str = "connect", password: str | None = None):
    rid = clean_rustdesk_id(rid)
    if not rid:
        raise LaunchError("Bitte eine RustDesk-ID eingeben.")
    if mode not in RUSTDESK_MODES:
        mode = "connect"
    app = rustdesk_installed()
    if not app:
        raise LaunchError("RustDesk ist nicht installiert (rustdesk.com oder github.com/rustdesk/rustdesk/releases).")
    if IS_MAC:
        # RustDesk wertet rustdesk://connect/<id> aus (siehe flutter/lib/common.dart, urlLinkToCmdArgs)
        url = f"rustdesk://{mode}/{quote(rid)}"
        if password:
            url += "?password=" + quote(password, safe="")
        _open(url)
        return
    cmd = [app, f"--{mode}", rid]
    if password:
        cmd += ["--password", password]
    _spawn(cmd)


def open_rustdesk(dev: Device):
    from . import vault
    open_rustdesk_id((dev.rustdesk or {}).get("id", ""), password=vault.get(dev.id, "rustdesk"))


# --- Dateizugriff ---------------------------------------------------------------
def open_files(dev: Device):
    cfg = dev.files or {}
    proto = cfg.get("proto", "smb")
    share = (cfg.get("share") or "").strip("/\\")
    user = (cfg.get("user") or "").strip()
    if proto == "sftp":
        # SFTP öffnet ein installiertes Programm (WinSCP, Cyberduck, Transmit, FileZilla …), sonst sftp im Terminal
        who = f"{quote(user)}@" if user else ""
        try:
            _open(f"sftp://{who}{dev.host}/{share}")
            return
        except LaunchError:
            target = f"{user}@{dev.host}" if user else dev.host
            if IS_MAC:
                _spawn(["osascript", "-e", f'tell application "Terminal" to do script "sftp {target}"',
                        "-e", 'tell application "Terminal" to activate'])
            elif IS_WIN:
                _spawn(["cmd", "/c", "start", f"SFTP {dev.name}", "sftp", target])
            else:
                raise
        return
    if IS_WIN:
        # Explorer: \\server\freigabe – Anmeldung fragt Windows selbst (Anmeldeinformationen speicherbar)
        _open(f"\\\\{dev.host}\\{share.replace('/', chr(92))}" if share else f"\\\\{dev.host}")
        return
    who = f"{quote(user)}@" if user else ""
    _open(f"{proto}://{who}{dev.host}/{share}")   # Finder bietet „Im Schlüsselbund sichern“ an


def open_link(url: str):
    if "://" not in url:
        url = "http://" + url
    _open(url)


OPENERS = {"vnc": open_vnc, "ssh": open_ssh, "web": open_web, "rdp": open_rdp, "rustdesk": open_rustdesk,
           "files": open_files}


def connect(dev: Device, service: str | None = None):
    service = service or dev.default_service()
    if not service:
        raise LaunchError(f"Für „{dev.name}“ ist kein Dienst eingerichtet.")
    if service not in OPENERS:
        raise LaunchError(f"„{service}“ wird in der Netzentrale selbst geöffnet.")
    OPENERS[service](dev)
