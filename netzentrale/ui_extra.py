"""Werkzeuge: Wake-on-LAN, Bonjour-Suche, WLAN-Übersicht, Subnetz-Rechner."""
from __future__ import annotations

import ipaddress
import json
import re
import socket
import subprocess
import time

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QPlainTextEdit, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from . import net

from .osinfo import IS_MAC, IS_WIN


def _table(cols, stretch=None) -> QTableWidget:
    t = QTableWidget(0, len(cols))
    t.setHorizontalHeaderLabels(cols)
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.setSelectionBehavior(QTableWidget.SelectRows)
    t.setSortingEnabled(True)
    t.verticalHeader().setVisible(False)
    h = t.horizontalHeader()
    for c in range(len(cols)):
        h.setSectionResizeMode(c, QHeaderView.ResizeToContents)
    if stretch is not None:
        h.setSectionResizeMode(stretch, QHeaderView.Stretch)
    return t


# ===========================================================================
# Wake-on-LAN
# ===========================================================================
def normalize_mac(mac: str) -> str | None:
    hexs = re.sub(r"[^0-9a-fA-F]", "", mac)
    if len(hexs) != 12:
        return None
    return ":".join(hexs[i:i + 2] for i in range(0, 12, 2)).lower()


def send_wol(mac: str, broadcast: str = "255.255.255.255", port: int = 9) -> None:
    m = normalize_mac(mac)
    if not m:
        raise ValueError(f"„{mac}“ ist keine gültige MAC-Adresse.")
    packet = b"\xff" * 6 + bytes.fromhex(m.replace(":", "")) * 16
    targets = {broadcast}
    try:
        targets.add(str(net.local_network().broadcast_address))
    except Exception:  # noqa: BLE001
        pass
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for t in targets:
            for p in {port, 7}:
                s.sendto(packet, (t, p))


class WolPage(QWidget):
    message = Signal(str)

    def __init__(self, device_store, parent=None):
        super().__init__(parent)
        self.device_store = device_store
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Schaltet Rechner per „Magic Packet“ ein. Voraussetzung: Wake-on-LAN ist im BIOS/"
                             "Betriebssystem des Zielgeräts aktiviert und es hängt per Kabel im selben Netz."))
        f = QFormLayout()
        self.mac = QLineEdit()
        self.mac.setPlaceholderText("z. B. 3C:22:FB:12:34:56")
        self.bcast = QLineEdit("255.255.255.255")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(9)
        f.addRow("MAC-Adresse:", self.mac)
        f.addRow("Broadcast-Adresse:", self.bcast)
        f.addRow("Port:", self.port)
        lay.addLayout(f)
        b = QPushButton("Aufwecken")
        b.clicked.connect(self.wake)
        lay.addWidget(b)
        lay.addWidget(QLabel("Geräte mit hinterlegter MAC-Adresse (Doppelklick weckt auf):"))
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self._wake_item)
        lay.addWidget(self.list, 1)

    def showEvent(self, e):
        super().showEvent(e)
        self.list.clear()
        for d in self.device_store.devices:
            if getattr(d, "mac", ""):
                self.list.addItem(f"{d.name}  –  {d.mac}")

    def _wake_item(self, it):
        self.mac.setText(it.text().rsplit("–", 1)[1].strip())
        self.wake()

    def wake(self):
        try:
            send_wol(self.mac.text(), self.bcast.text().strip() or "255.255.255.255", self.port.value())
            self.message.emit(f"Magic Packet an {normalize_mac(self.mac.text())} gesendet")
        except (ValueError, OSError) as e:
            self.message.emit(f"Wake-on-LAN fehlgeschlagen: {e}")


# ===========================================================================
# Bonjour / mDNS
# ===========================================================================
SERVICE_NAMES = {
    "_http._tcp.local.": "Web (HTTP)", "_https._tcp.local.": "Web (HTTPS)", "_ssh._tcp.local.": "SSH",
    "_sftp-ssh._tcp.local.": "SFTP", "_rfb._tcp.local.": "VNC / Bildschirmfreigabe", "_smb._tcp.local.": "Dateifreigabe (SMB)",
    "_afpovertcp._tcp.local.": "Dateifreigabe (AFP)", "_ipp._tcp.local.": "Drucker (IPP)", "_ipps._tcp.local.": "Drucker (IPPS)",
    "_printer._tcp.local.": "Drucker (LPD)", "_pdl-datastream._tcp.local.": "Drucker (RAW)",
    "_airplay._tcp.local.": "AirPlay", "_raop._tcp.local.": "AirPlay-Audio", "_googlecast._tcp.local.": "Chromecast",
    "_home-assistant._tcp.local.": "Home Assistant", "_hap._tcp.local.": "HomeKit", "_mqtt._tcp.local.": "MQTT-Broker",
    "_esphomelib._tcp.local.": "ESPHome", "_workstation._tcp.local.": "Arbeitsplatz", "_device-info._tcp.local.": "Geräteinfo",
    "_companion-link._tcp.local.": "Apple-Gerät", "_scanner._tcp.local.": "Scanner", "_uscan._tcp.local.": "Scanner (eSCL)",
    "_spotify-connect._tcp.local.": "Spotify Connect", "_sonos._tcp.local.": "Sonos", "_rdp._tcp.local.": "RDP",
}


class BonjourWorker(QThread):
    found = Signal(dict)
    done = Signal(int)

    def __init__(self, seconds: int = 8, parent=None):
        super().__init__(parent)
        self.seconds = seconds
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            from zeroconf import ServiceBrowser, ServiceStateChange, Zeroconf, ZeroconfServiceTypes
        except ImportError:
            self.found.emit({"error": "Python-Paket „zeroconf“ fehlt."})
            self.done.emit(0)
            return
        zc = Zeroconf()
        seen = set()

        def handler(zeroconf, service_type, name, state_change):
            if state_change is not ServiceStateChange.Added or (service_type, name) in seen:
                return
            seen.add((service_type, name))
            info = zeroconf.get_service_info(service_type, name, timeout=2500)
            addrs = info.parsed_addresses() if info else []
            v4 = [a for a in addrs if ":" not in a]
            txt = {}
            if info and info.properties:
                for k, v in info.properties.items():
                    try:
                        txt[k.decode()] = (v or b"").decode(errors="replace")
                    except AttributeError:
                        pass
            self.found.emit({
                "name": name.replace("." + service_type, ""),
                "type": service_type, "label": SERVICE_NAMES.get(service_type, service_type.split(".")[0].lstrip("_")),
                "host": (info.server or "").rstrip(".") if info else "",
                "ip": (v4 or addrs or [""])[0], "port": info.port if info else 0,
                "txt": ", ".join(f"{k}={v}" for k, v in list(txt.items())[:6]),
            })

        try:
            types = set(ZeroconfServiceTypes.find(zc=zc, timeout=3)) | set(SERVICE_NAMES)
            browsers = [ServiceBrowser(zc, t, handlers=[handler]) for t in sorted(types)]
            end = time.time() + self.seconds
            while time.time() < end and not self._stop:
                time.sleep(0.2)
            for b in browsers:
                b.cancel()
        finally:
            zc.close()
        self.done.emit(len(seen))


class BonjourPage(QWidget):
    message = Signal(str)
    add_device = Signal(object)
    COLS = ["Name", "Dienst", "Hostname", "IP-Adresse", "Port", "Details"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker: BonjourWorker | None = None
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Findet Geräte, die sich per Bonjour/mDNS melden (Drucker, Macs, Pis mit .local, "
                             "Home Assistant, AirPlay …) – auch wenn sie Ping blocken."))
        bar.addStretch(1)
        bar.addWidget(QLabel("Dauer:"))
        self.secs = QSpinBox()
        self.secs.setRange(3, 60)
        self.secs.setValue(8)
        self.secs.setSuffix(" s")
        bar.addWidget(self.secs)
        self.btn = QPushButton("Suche starten")
        self.btn.clicked.connect(self.toggle)
        bar.addWidget(self.btn)
        lay.addLayout(bar)
        self.table = _table(self.COLS, stretch=5)
        self.table.itemDoubleClicked.connect(lambda _: self._add())
        lay.addWidget(self.table, 1)
        act = QHBoxLayout()
        b = QPushButton("Als Gerät übernehmen")
        b.clicked.connect(self._add)
        act.addWidget(b)
        act.addStretch(1)
        self.summary = QLabel("")
        act.addWidget(self.summary)
        lay.addLayout(act)

    def toggle(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            return
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.worker = BonjourWorker(self.secs.value(), self)
        self.worker.found.connect(self._found)
        self.worker.done.connect(self._done)
        self.worker.start()
        self.btn.setText("Stopp")
        self.summary.setText("suche …")

    def _found(self, d: dict):
        if "error" in d:
            self.summary.setText(d["error"])
            return
        r = self.table.rowCount()
        self.table.insertRow(r)
        for c, v in enumerate([d["name"], d["label"], d["host"], d["ip"], str(d["port"] or ""), d["txt"]]):
            it = QTableWidgetItem(v)
            if c == 0:
                it.setData(256, d)
            self.table.setItem(r, c, it)

    def _done(self, n: int):
        self.btn.setText("Suche starten")
        self.table.setSortingEnabled(True)
        self.table.sortItems(3)
        self.summary.setText(f"{n} Dienste gefunden")
        self.message.emit(f"Bonjour-Suche fertig: {n} Dienste")

    def _add(self):
        r = self.table.currentRow()
        if r < 0:
            return
        d = self.table.item(r, 0).data(256)
        from .storage import DEFAULT_VNC_CLIENT, Device
        host = d["host"] or d["ip"]
        dev = Device(name=d["name"], host=host)
        t, port = d["type"], d["port"]
        if t.startswith("_rfb."):
            dev.vnc = {"port": port or 5900, "client": DEFAULT_VNC_CLIENT}
        elif t.startswith(("_ssh.", "_sftp-ssh.")):
            dev.ssh = {"port": port or 22, "user": ""}
        elif t.startswith("_https."):
            dev.web = {"url": f"https://{host}" + (f":{port}" if port and port != 443 else "")}
        elif t.startswith(("_http.", "_home-assistant.", "_esphomelib.")):
            dev.web = {"url": f"http://{host}" + (f":{port}" if port and port != 80 else "")}
        self.add_device.emit(dev)


# ===========================================================================
# WLAN-Übersicht
# ===========================================================================
def _band(ch: int) -> str:
    return "2,4 GHz" if ch <= 14 else ("6 GHz" if ch > 177 else "5 GHz")


_WIN_KEYS = {  # deutsch / englisch
    "auth": ("authentifizierung", "authentication"), "signal": ("signal",),
    "channel": ("kanal", "channel"), "radio": ("funktyp", "radio type"), "band": ("band",),
    "rate": ("empfangsrate (mbit/s)", "receive rate (mbps)"),
}


def _kv(line: str) -> tuple[str, str] | None:
    if ":" not in line:
        return None
    k, v = line.split(":", 1)
    return k.strip().lower(), v.strip()


def _key_is(k: str, name: str) -> bool:
    return any(k == w or k.startswith(w + " ") for w in _WIN_KEYS[name])


def _pct_to_dbm(v: str) -> int | None:
    m = re.match(r"(\d+)\s*%", v)
    return int(m.group(1)) // 2 - 100 if m else None   # Windows-Näherung: 100 % ≈ -50 dBm, 0 % ≈ -100 dBm


def parse_netsh_networks(out: str) -> list[dict]:
    nets, cur, seen_bssid = [], None, False
    for line in out.splitlines():
        m = re.match(r"^SSID \d+\s*:\s*(.*)$", line.strip())
        if m:
            cur = {"ssid": m.group(1).strip(), "channel": 0, "channel_raw": "", "band": "", "signal": None,
                   "noise": None, "security": "", "phy": "", "rate": ""}
            nets.append(cur)
            seen_bssid = False
            continue
        kv = _kv(line)
        if not kv or cur is None:
            continue
        k, v = kv
        if k.startswith("bssid"):
            if seen_bssid:      # weiterer Access Point desselben Netzes → eigene Zeile
                cur = dict(cur, signal=None, channel=0, channel_raw="")
                nets.append(cur)
            seen_bssid = True
        elif _key_is(k, "auth"):
            cur["security"] = v
        elif _key_is(k, "signal"):
            cur["signal"] = _pct_to_dbm(v)
        elif _key_is(k, "channel") and v.split()[0].isdigit():
            cur["channel"] = int(v.split()[0])
            cur["channel_raw"] = v
            cur["band"] = cur["band"] or _band(cur["channel"])
        elif _key_is(k, "radio"):
            cur["phy"] = v
        elif _key_is(k, "band"):
            cur["band"] = v.replace("GHz", " GHz").replace("  ", " ").replace(".", ",")
    return nets


def parse_netsh_interface(out: str) -> dict:
    cur = {}
    for line in out.splitlines():
        kv = _kv(line)
        if not kv:
            continue
        k, v = kv
        if k == "ssid":
            cur["ssid"] = v
        elif _key_is(k, "channel") and v.split()[0].isdigit():
            cur["channel"] = int(v.split()[0])
            cur["channel_raw"] = v
            cur["band"] = _band(cur["channel"])
        elif _key_is(k, "signal"):
            cur["signal"] = _pct_to_dbm(v)
        elif _key_is(k, "radio"):
            cur["phy"] = v
        elif _key_is(k, "rate"):
            cur["rate"] = v
    if cur:
        cur.setdefault("noise", None)
        cur.setdefault("signal", None)
        cur.setdefault("channel_raw", "")
        cur.setdefault("band", "")
        cur.setdefault("phy", "")
        cur.setdefault("rate", "")
        cur.setdefault("ssid", "")
    return cur


def _wifi_windows() -> tuple[dict, list[dict], str]:
    from .osinfo import run
    out_if = run(["netsh", "wlan", "show", "interfaces"], timeout=10)
    out_net = run(["netsh", "wlan", "show", "networks", "mode=bssid"], timeout=20)
    hint = ""
    if re.search(r"location|standort", out_if + out_net, re.I) and "SSID" not in out_net:
        hint = ("Windows verlangt für WLAN-Informationen die Standortfreigabe: Einstellungen → Datenschutz & "
                "Sicherheit → Standort → „Standortdienste“ und „Desktop-Apps Zugriff auf Ihren Standort "
                "erlauben“ einschalten, dann erneut scannen.")
    elif not out_if.strip():
        hint = "Kein WLAN-Adapter gefunden (oder WLAN-Dienst nicht aktiv)."
    return parse_netsh_interface(out_if), parse_netsh_networks(out_net), hint


def wifi_scan() -> tuple[dict, list[dict], str]:
    """Aktuelles WLAN + Umgebung. Rückgabe: (aktuell, liste, hinweis)."""
    if IS_WIN:
        return _wifi_windows()
    if IS_MAC:
        out = subprocess.run(["system_profiler", "SPAirPortDataType", "-json"], capture_output=True,
                             text=True, timeout=60).stdout
        data = json.loads(out or "{}")
        cur, nets = {}, []
        for block in data.get("SPAirPortDataType", []):
            for iface in block.get("spairport_airport_interfaces", []):
                def conv(n):
                    ch_raw = str(n.get("spairport_network_channel", ""))
                    m = re.match(r"(\d+)", ch_raw)
                    ch = int(m.group(1)) if m else 0
                    sn = str(n.get("spairport_signal_noise", ""))
                    sig = re.findall(r"-?\d+", sn)
                    return {"ssid": n.get("_name", ""), "channel": ch, "channel_raw": ch_raw,
                            "band": _band(ch) if ch else "",
                            "signal": int(sig[0]) if sig else None, "noise": int(sig[1]) if len(sig) > 1 else None,
                            "security": str(n.get("spairport_security_mode", "")).replace("spairport_security_mode_", ""),
                            "phy": n.get("spairport_network_phymode", ""), "rate": n.get("spairport_network_rate", "")}
                if "spairport_current_network_information" in iface:
                    cur = conv(iface["spairport_current_network_information"])
                for n in iface.get("spairport_airport_other_local_wireless_networks", []):
                    nets.append(conv(n))
        redacted = lambda n: (not n) or "redacted" in n.lower()   # noqa: E731
        if cur and redacted(cur.get("ssid", "")):
            cur["ssid"] = _current_ssid_fallback()
        hidden = sum(1 for n in nets if redacted(n["ssid"]))
        for n in nets:
            if redacted(n["ssid"]):
                n["ssid"] = ""
        hint = ""
        if hidden or (cur and not cur.get("ssid")):
            hint = ("Warum fehlen WLAN-Namen? Seit macOS 14 gibt Apple Netznamen nur an Apps heraus, die von einem "
                    "registrierten Apple-Entwickler signiert sind und Zugriff auf die Ortungsdienste haben. Die "
                    "Netzentrale ist (noch) nicht signiert. Kanäle, Signalstärken und Sicherheit stimmen trotzdem.")
        return cur, nets, hint
    try:
        out = subprocess.run(["nmcli", "-t", "-f", "IN-USE,SSID,CHAN,SIGNAL,SECURITY", "dev", "wifi"],
                             capture_output=True, text=True, timeout=30).stdout
    except FileNotFoundError:
        return {}, [], "WLAN-Übersicht ist nur auf dem Mac (oder Linux mit nmcli) verfügbar."
    cur, nets = {}, []
    for line in out.splitlines():
        p = line.split(":")
        if len(p) < 5:
            continue
        ch = int(p[2]) if p[2].isdigit() else 0
        n = {"ssid": p[1], "channel": ch, "channel_raw": p[2], "band": _band(ch), "signal": int(p[3]) - 100 if p[3].isdigit() else None,
             "noise": None, "security": p[4], "phy": "", "rate": ""}
        (cur.update(n) if p[0] == "*" else nets.append(n))
    return cur, nets, ""


def _current_ssid_fallback() -> str:
    """Name des verbundenen WLANs auf anderen Wegen (je nach macOS-Version ebenfalls ausgeblendet)."""
    for cmd in (["ipconfig", "getsummary", "en0"], ["networksetup", "-getairportnetwork", "en0"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        m = re.search(r"^\s*SSID\s*:\s*(.+)$", out, re.M) or re.search(r"Current Wi-Fi Network:\s*(.+)$", out, re.M)
        if m and "redacted" not in m.group(1).lower():
            return m.group(1).strip()
    return ""


class WifiWorker(QThread):
    done = Signal(object)

    def run(self):
        try:
            self.done.emit(wifi_scan())
        except Exception as e:  # noqa: BLE001
            self.done.emit(({}, [], f"Fehler: {e}"))


class WifiPage(QWidget):
    COLS = ["WLAN-Name", "Kanal", "Band", "Signal", "Rauschen", "Sicherheit", "Standard"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker: WifiWorker | None = None
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.cur = QLabel("Aktuelles WLAN: –")
        self.cur.setWordWrap(True)
        bar.addWidget(self.cur, 1)
        self.btn = QPushButton("Umgebung scannen")
        self.btn.clicked.connect(self.scan)
        bar.addWidget(self.btn)
        lay.addLayout(bar)
        self.table = _table(self.COLS, stretch=0)
        lay.addWidget(self.table, 1)
        self.channels = QLabel("")
        self.channels.setWordWrap(True)
        lay.addWidget(self.channels)
        self.hint = QLabel("")
        self.hint.setStyleSheet("color: gray; font-size: 11px;")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)

    def scan(self):
        if self.worker and self.worker.isRunning():
            return
        self.btn.setText("scanne … (ca. 10 s)")
        self.btn.setEnabled(False)
        self.worker = WifiWorker(self)
        self.worker.done.connect(self._show)
        self.worker.start()

    @staticmethod
    def _quality(sig):
        if sig is None:
            return ""
        return "sehr gut" if sig >= -55 else "gut" if sig >= -67 else "mäßig" if sig >= -75 else "schwach"

    def _show(self, res):
        cur, nets, hint = res
        self.btn.setText("Umgebung scannen")
        self.btn.setEnabled(True)
        self.hint.setText(hint)
        if cur:
            snr = f", Störabstand {cur['signal'] - cur['noise']} dB" if cur.get("signal") is not None and cur.get("noise") is not None else ""
            self.cur.setText(f"<b>Aktuelles WLAN:</b> {cur['ssid'] or '(Name von macOS ausgeblendet)'} · Kanal {cur['channel_raw']} "
                             f"({cur['band']}) · Signal {cur['signal']} dBm – {self._quality(cur['signal'])}{snr} · "
                             f"{cur['phy']} · {cur['rate']} Mbit/s")
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(nets))
        for r, n in enumerate(nets):
            sig = f"{n['signal']} dBm ({self._quality(n['signal'])})" if n["signal"] is not None else ""
            vals = [n["ssid"] or "(ausgeblendet)", str(n["channel"] or ""), n["band"], sig,
                    f"{n['noise']} dBm" if n["noise"] is not None else "", n["security"], n["phy"]]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 1:
                    it.setData(0, n["channel"])
                self.table.setItem(r, c, it)
        self.table.setSortingEnabled(True)
        # Kanalbelegung 2,4 GHz: 1/6/11 sind die überlappungsfreien Kanäle
        use = {}
        for n in nets + ([cur] if cur else []):
            if n.get("channel"):
                use[n["channel"]] = use.get(n["channel"], 0) + 1
        g24 = {c: sum(v for k, v in use.items() if abs(k - c) < 5 and k <= 14) for c in (1, 6, 11)}
        best = min(g24, key=g24.get) if use else None
        txt = "Belegung: " + ", ".join(f"Kanal {k}: {v}" for k, v in sorted(use.items()))
        if best:
            txt += f"<br><b>Empfehlung 2,4 GHz:</b> Kanal {best} (am wenigsten überlappende Netze)"
        self.channels.setText(txt)


# ===========================================================================
# Subnetz-Rechner
# ===========================================================================
def subnet_info(text: str) -> str:
    text = text.strip()
    m = re.fullmatch(r"([\d.]+)\s+([\d.]+)", text)  # „IP Maske“
    if m:
        text = f"{m.group(1)}/{m.group(2)}"
    if "/" not in text:
        text += "/24"
    iface = ipaddress.ip_interface(text)
    n = iface.network
    hosts = n.num_addresses - 2 if n.prefixlen < 31 else n.num_addresses
    first = n.network_address + (1 if n.prefixlen < 31 else 0)
    last = n.broadcast_address - (1 if n.prefixlen < 31 else 0)

    def b(ip):
        return ".".join(f"{int(x):08b}" for x in str(ip).split("."))

    kind = ("privat (RFC 1918)" if n.is_private and not n.is_loopback and not n.is_link_local else
            "Loopback" if n.is_loopback else "Link-Local (APIPA)" if n.is_link_local else "öffentlich")
    rows = [
        ("Adresse", f"{iface.ip}"), ("Netz", f"{n.network_address}/{n.prefixlen}"),
        ("Subnetzmaske", f"{n.netmask}"), ("Wildcard", f"{n.hostmask}"),
        ("Broadcast", f"{n.broadcast_address}"), ("Erste Host-Adresse", f"{first}"),
        ("Letzte Host-Adresse", f"{last}"), ("Anzahl Hosts", f"{hosts:,}".replace(",", ".")),
        ("Art", kind), ("", ""), ("Adresse binär", b(iface.ip)), ("Maske binär", b(n.netmask)),
    ]
    return "\n".join(f"{k:<22}{v}" for k, v in rows)


def subnet_split(text: str, new_prefix: int) -> str:
    if "/" not in text:
        text += "/24"
    n = ipaddress.ip_interface(text.strip()).network
    if new_prefix <= n.prefixlen:
        return f"Neues Präfix muss größer als /{n.prefixlen} sein."
    subs = list(n.subnets(new_prefix=new_prefix))
    lines = [f"{len(subs)} Teilnetze /{new_prefix} (je {subs[0].num_addresses - 2 if new_prefix < 31 else subs[0].num_addresses} Hosts):", ""]
    for s in subs[:256]:
        h = list(s.hosts())
        lines.append(f"{str(s):<20} {str(h[0]) if h else '':<16} – {str(h[-1]) if h else '':<16} Broadcast {s.broadcast_address}")
    if len(subs) > 256:
        lines.append(f"… und {len(subs) - 256} weitere")
    return "\n".join(lines)


class SubnetPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Adresse:"))
        self.inp = QLineEdit()
        self.inp.setPlaceholderText("192.168.10.25/24  oder  192.168.10.25 255.255.255.0")
        try:
            self.inp.setText(f"{net.local_ip()}/{net.local_network().prefixlen}")
        except Exception:  # noqa: BLE001
            pass
        self.inp.textChanged.connect(self.calc)
        bar.addWidget(self.inp, 1)
        bar.addWidget(QLabel("Aufteilen in /"))
        self.split = QSpinBox()
        self.split.setRange(1, 32)
        self.split.setValue(26)
        bar.addWidget(self.split)
        b = QPushButton("Teilnetze berechnen")
        b.clicked.connect(self.do_split)
        bar.addWidget(b)
        lay.addLayout(bar)
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        lay.addWidget(self.out, 1)
        self.calc()

    def calc(self):
        try:
            self.out.setPlainText(subnet_info(self.inp.text()))
        except ValueError as e:
            self.out.setPlainText(f"Eingabe unvollständig oder ungültig: {e}")

    def do_split(self):
        try:
            self.out.setPlainText(subnet_info(self.inp.text()) + "\n\n" + subnet_split(self.inp.text(), self.split.value()))
        except ValueError as e:
            self.out.setPlainText(str(e))
