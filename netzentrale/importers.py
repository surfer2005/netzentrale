"""Import von Gerätelisten aus anderen Programmen.

RealVNC Viewer: Adressbuch → Ablage/„RealVNC Viewer“-Menü → „Export Connections“
erzeugt je Verbindung eine .vnc-Datei (Schlüssel=Wert, u. a. Host=, FriendlyName=),
Unterordner entsprechen Gruppen. Unterstützt werden einzelne Dateien, ganze Ordner
und ZIP-Archive.

CSV: Spalten Name;Adresse;Gruppe;Port (Kopfzeile optional, ; oder , getrennt).
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path, PurePosixPath

from .storage import Device


def _decode(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def parse_vnc_host(value: str) -> tuple[str, int]:
    """RealVNC/VNC-Adressschreibweisen → (host, port).
    'pc'→5900 · 'pc:1'→5901 (Display) · 'pc:5905'→5905 · 'pc::5905'→5905 · '[::1]:5901'."""
    v = value.strip()
    if v.startswith("["):  # IPv6 in Klammern
        m = re.fullmatch(r"\[([^\]]+)\](?::(:?)(\d+))?", v)
        if m:
            host, dbl, num = m.group(1), m.group(2), m.group(3)
            if not num:
                return host, 5900
            n = int(num)
            return host, n if (dbl or n >= 100) else 5900 + n
    if "::" in v:
        host, port = v.rsplit("::", 1)
        return host, int(port) if port.isdigit() else 5900
    if v.count(":") == 1:
        host, num = v.split(":")
        if num.isdigit():
            n = int(num)
            return host, n if n >= 100 else 5900 + n
        return host, 5900
    return v, 5900


def parse_vnc_text(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";", "[")) or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip().lower()] = v.strip()
    return out


def device_from_vnc(text: str, filename: str, group: str, client: str) -> Device | None:
    cfg = parse_vnc_text(text)
    host_val = cfg.get("host") or cfg.get("server")
    if not host_val:
        return None
    host, port = parse_vnc_host(host_val)
    if not host:
        return None
    name = cfg.get("friendlyname") or Path(filename).stem or host
    return Device(name=name, host=host, group=group or "RealVNC-Import",
                  vnc={"port": port, "client": client})


def import_realvnc(paths: list[str], client: str) -> list[Device]:
    """Dateien (.vnc, .zip) oder Ordner einlesen; Unterordner → Gruppe."""
    found: list[Device] = []
    for p in map(Path, paths):
        if p.is_dir():
            for f in sorted(p.rglob("*.vnc")):
                rel = f.relative_to(p).parent
                group = rel.parts[0] if rel.parts else p.name
                if d := device_from_vnc(_decode(f.read_bytes()), f.name, group, client):
                    found.append(d)
        elif p.suffix.lower() == ".zip":
            with zipfile.ZipFile(p) as z:
                for info in z.infolist():
                    if info.is_dir() or not info.filename.lower().endswith(".vnc"):
                        continue
                    pp = PurePosixPath(info.filename)
                    group = pp.parts[-2] if len(pp.parts) > 1 else p.stem
                    if d := device_from_vnc(_decode(z.read(info)), pp.name, group, client):
                        found.append(d)
        elif p.suffix.lower() == ".vnc":
            if d := device_from_vnc(_decode(p.read_bytes()), p.name, "RealVNC-Import", client):
                found.append(d)
    return found


def import_csv(path: str, client: str) -> list[Device]:
    text = _decode(Path(path).read_bytes())
    dialect = csv.Sniffer().sniff(text[:2000], delimiters=";,\t") if text.strip() else csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    if not rows:
        return []
    head = [c.strip().lower() for c in rows[0]]
    has_header = any(h in ("name", "adresse", "host", "ip", "gruppe") for h in head)

    def col(*names, default):
        for n in names:
            if n in head:
                return head.index(n)
        return default

    if has_header:
        i_name, i_host = col("name", default=0), col("adresse", "host", "ip", default=1)
        i_group, i_port = col("gruppe", "group", default=2), col("port", "vnc-port", default=3)
        rows = rows[1:]
    else:
        i_name, i_host, i_group, i_port = 0, 1, 2, 3
    out = []
    for r in rows:
        get = lambda i: r[i].strip() if i < len(r) else ""  # noqa: E731
        host = get(i_host)
        if not host:
            continue
        port = int(get(i_port)) if get(i_port).isdigit() else 5900
        out.append(Device(name=get(i_name) or host, host=host, group=get(i_group) or "CSV-Import",
                          vnc={"port": port, "client": client}))
    return out


def merge(store, devices: list[Device]) -> tuple[int, int]:
    """Neue Geräte übernehmen, Dubletten (gleiche Adresse + VNC-Port) überspringen."""
    seen = {(d.host.lower(), (d.vnc or {}).get("port")) for d in store.devices}
    added = skipped = 0
    for d in devices:
        key = (d.host.lower(), (d.vnc or {}).get("port"))
        if key in seen:
            skipped += 1
            continue
        seen.add(key)
        store.devices.append(d)
        added += 1
    store.save()
    return added, skipped
