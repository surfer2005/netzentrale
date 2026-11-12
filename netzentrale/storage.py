"""Geräteliste: Datenmodell und Speicherung als JSON."""
from __future__ import annotations

import json
import os
import sys
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_VNC_CLIENT = "tigervnc"
VNC_CLIENTS = {
    "tigervnc": "TigerVNC",
    "realvnc": "RealVNC Viewer",
    "apple": "Apple Bildschirmfreigabe",
}
SERVICE_ORDER = ("vnc", "ssh", "web", "rdp", "rustdesk", "files", "dali")
SERVICE_LABEL = {"vnc": "VNC", "ssh": "SSH", "web": "Web", "rdp": "RDP", "rustdesk": "RustDesk",
                 "files": "Dateien", "dali": "DALI"}
FILE_PROTOCOLS = {"smb": "SMB (Windows/NAS-Freigabe)", "sftp": "SFTP (über SSH)", "afp": "AFP (ältere Macs)"}


def data_dir() -> Path:
    if sys.platform == "darwin":
        p = Path.home() / "Library/Application Support/Netzentrale"
    elif sys.platform.startswith("win"):
        p = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "Netzentrale"
    else:
        p = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "netzentrale"
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class Device:
    name: str
    host: str
    group: str = "Allgemein"
    note: str = ""
    # Dienste – None bedeutet "nicht aktiv"
    vnc: dict | None = None   # {"port": 5900, "client": "tigervnc"}
    ssh: dict | None = None   # {"port": 22, "user": "pi"}
    web: dict | None = None   # {"url": "http://host:8123"}
    rdp: dict | None = None   # {"port": 3389, "user": ""}
    rustdesk: dict | None = None  # {"id": "123456789"} – Fernhilfe übers Internet
    files: dict | None = None     # {"proto": "smb", "share": "daten", "user": ""}
    dali: dict | None = None      # {"enabled": True} – Lunatone DALI-2 IoT Gateway
    mac: str = ""                 # für Wake-on-LAN
    links: list = field(default_factory=list)  # weitere Weblinks [{"name": .., "url": ..}]
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def services(self) -> list[str]:
        return [s for s in SERVICE_ORDER if getattr(self, s)]

    def default_service(self) -> str | None:
        if self.dali:
            return "dali"   # Gateways: Doppelklick öffnet die Lichtsteuerung
        s = self.services()
        return s[0] if s else None

    def probe_ports(self) -> list[int]:
        """Ports, an denen der Online-Status geprüft wird."""
        ports: list[int] = []
        if not self.host:
            return ports
        if self.vnc: ports.append(int(self.vnc.get("port", 5900)))
        if self.ssh: ports.append(int(self.ssh.get("port", 22)))
        if self.rdp: ports.append(int(self.rdp.get("port", 3389)))
        if self.dali: ports.append(80)
        if self.web:
            from urllib.parse import urlparse
            u = urlparse(self.web.get("url", ""))
            ports.append(u.port or (443 if u.scheme == "https" else 80))
        return ports

    @classmethod
    def from_dict(cls, d: dict) -> "Device":
        known = {k: d.get(k) for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


class DeviceStore:
    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "geraete.json"
        self.devices: list[Device] = []
        self.load()

    def load(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.devices = [Device.from_dict(d) for d in raw.get("devices", [])]
        except FileNotFoundError:
            self.devices = []
        except (json.JSONDecodeError, TypeError) as e:
            backup = self.path.with_suffix(".defekt.json")
            self.path.replace(backup)
            print(f"Geräteliste beschädigt, gesichert als {backup}: {e}")
            self.devices = []

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "devices": [asdict(d) for d in self.devices]},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def groups(self) -> list[str]:
        return sorted({d.group or "Allgemein" for d in self.devices}, key=str.lower)

    def upsert(self, dev: Device):
        for i, d in enumerate(self.devices):
            if d.id == dev.id:
                self.devices[i] = dev
                break
        else:
            self.devices.append(dev)
        self.save()

    def remove(self, dev_id: str):
        self.devices = [d for d in self.devices if d.id != dev_id]
        self.save()

    def export_to(self, path: str):
        Path(path).write_text(json.dumps({"version": 1, "devices": [asdict(d) for d in self.devices]},
                                         ensure_ascii=False, indent=2), encoding="utf-8")

    def import_from(self, path: str) -> int:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        existing = {(d.host, d.name) for d in self.devices}
        n = 0
        for d in raw.get("devices", []):
            dev = Device.from_dict(d)
            if (dev.host, dev.name) not in existing:
                dev.id = uuid.uuid4().hex[:12]
                self.devices.append(dev)
                n += 1
        self.save()
        return n
