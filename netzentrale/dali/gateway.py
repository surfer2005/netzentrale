"""Schlanker REST-Client für das Lunatone DALI-2 IoT Gateway.

Nutzt nur die Standardbibliothek (urllib), damit keine weiteren
Abhängigkeiten nötig sind. Endpunkte laut Lunatone API-Doku M0023:

    GET  /info
    GET  /devices
    PUT  /device/{id}
    POST /device/{id}/control
    POST /group/{nr}/control      (nr = 0..15)
    POST /broadcast/control
    GET  /zones
    POST /zone/{id}/control
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any


class GatewayError(Exception):
    """Fehler bei der Kommunikation mit dem Gateway."""


@dataclass
class Device:
    id: int
    name: str
    address: int
    line: int
    groups: list[int] = field(default_factory=list)
    dali_types: list[int] = field(default_factory=list)
    features: dict[str, Any] = field(default_factory=dict)

    # --- abgeleitete Eigenschaften ---------------------------------------
    @property
    def is_switchable(self) -> bool:
        return "switchable" in self.features

    @property
    def is_dimmable(self) -> bool:
        return "dimmable" in self.features

    @property
    def supports_scenes(self) -> bool:
        return bool(self.features.get("scene"))

    @property
    def is_on(self) -> bool | None:
        f = self.features.get("switchable")
        return f.get("status") if isinstance(f, dict) else None

    @property
    def level(self) -> float | None:
        f = self.features.get("dimmable")
        return f.get("status") if isinstance(f, dict) else None

    @property
    def label(self) -> str:
        return self.name or f"DALI #{self.address}"

    @classmethod
    def from_json(cls, d: dict) -> "Device":
        return cls(
            id=d["id"],
            name=d.get("name") or "",
            address=d.get("address", -1),
            line=d.get("line", 0),
            groups=list(d.get("groups") or []),
            dali_types=list(d.get("daliTypes") or []),
            features=d.get("features") or {},
        )


@dataclass
class Zone:
    id: int
    name: str

    @classmethod
    def from_json(cls, d: dict) -> "Zone":
        return cls(id=d["id"], name=d.get("name") or f"Zone {d['id']}")


class DaliGateway:
    def __init__(self, host: str, timeout: float = 4.0):
        host = host.strip().rstrip("/")
        if not host.startswith(("http://", "https://")):
            host = "http://" + host
        self.base = host
        self.timeout = timeout

    # --- HTTP -----------------------------------------------------------
    def _request(self, method: str, path: str, body: dict | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            raise GatewayError(f"HTTP {e.code} bei {method} {path}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise GatewayError(f"Gateway {self.base} nicht erreichbar: {e}") from e
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw.decode(errors="replace")

    # --- Lesen ----------------------------------------------------------
    def info(self) -> dict:
        return self._request("GET", "/info")

    def devices(self) -> list[Device]:
        res = self._request("GET", "/devices") or {}
        devs = [Device.from_json(d) for d in res.get("devices", [])]
        return sorted(devs, key=lambda d: (d.line, d.address))

    def zones(self) -> list[Zone]:
        res = self._request("GET", "/zones") or {}
        return [Zone.from_json(z) for z in res.get("zones", [])]

    # --- Steuern --------------------------------------------------------
    def control_device(self, device_id: int, **ctrl) -> None:
        self._request("POST", f"/device/{device_id}/control", ctrl)

    def control_group(self, group: int, **ctrl) -> None:
        if not 0 <= group <= 15:
            raise ValueError("DALI-Gruppe muss zwischen 0 und 15 liegen")
        self._request("POST", f"/group/{group}/control", ctrl)

    def control_broadcast(self, **ctrl) -> None:
        self._request("POST", "/broadcast/control", ctrl)

    def control_zone(self, zone_id: int, **ctrl) -> None:
        self._request("POST", f"/zone/{zone_id}/control", ctrl)

    # --- Konfiguration --------------------------------------------------
    def rename_device(self, device_id: int, name: str) -> None:
        self._request("PUT", f"/device/{device_id}", {"name": name})


def control_payload(*, on: bool | None = None, level: int | None = None,
                    scene: int | None = None, last_active: bool = False) -> dict:
    """Baut einen ControlData-Body. level 0 schaltet aus."""
    p: dict[str, Any] = {}
    if on is not None:
        p["switchable"] = on
    if level is not None:
        p["dimmable"] = max(0, min(100, int(level)))
    if scene is not None:
        if not 0 <= scene <= 15:
            raise ValueError("Szene muss zwischen 0 und 15 liegen")
        p["scene"] = scene
    if last_active:
        p["gotoLastActive"] = True
    return p
