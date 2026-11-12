"""Netzwerk-Grundfunktionen: Ping, TCP-Prüfung, eigenes Netz, ARP, Portlisten."""
from __future__ import annotations

import ipaddress
import re
import socket
import subprocess
import time

from .osinfo import IS_MAC, IS_WIN, powershell_json, run_rc

# Wird von der App gesetzt (z. B. Taskleisten-Symbol unter Windows); sonst Rückfall unten
NOTIFIER = None

# Ports für Netzwerkscan und „häufige Ports“ im Portscan
SCAN_PORTS = [22, 80, 443, 445, 3389, 5900, 8080, 8123, 502, 1883, 8883, 9100]
COMMON_PORTS = [
    20, 21, 22, 23, 25, 53, 67, 80, 88, 110, 123, 135, 139, 143, 161, 389, 443, 445,
    465, 502, 548, 554, 587, 631, 993, 995, 1433, 1883, 1900, 2049, 3000, 3306, 3389,
    5000, 5001, 5060, 5353, 5432, 5555, 5900, 5901, 5938, 6379, 8000, 8008, 8080,
    8081, 8123, 8443, 8883, 9000, 9100, 10000, 11434, 32400,
]
PORT_NAMES = {
    22: "SSH", 23: "Telnet", 80: "HTTP", 443: "HTTPS", 445: "SMB", 502: "Modbus TCP",
    548: "AFP", 554: "RTSP (Kamera)", 631: "IPP Drucker", 1883: "MQTT", 3389: "RDP",
    5900: "VNC", 5901: "VNC :1", 5938: "TeamViewer", 8123: "Home Assistant",
    8080: "HTTP alt", 8443: "HTTPS alt", 9100: "Drucker RAW", 32400: "Plex",
    11434: "Ollama", 5555: "ADB / DALI-IoT", 1900: "UPnP", 5353: "mDNS", 8883: "MQTT (TLS)",
}
# Raspberry Pi Foundation / Raspberry Pi Ltd – MAC-Präfixe
VENDOR_PREFIX = {
    "b8:27:eb": "Raspberry Pi", "dc:a6:32": "Raspberry Pi", "e4:5f:01": "Raspberry Pi",
    "d8:3a:dd": "Raspberry Pi", "28:cd:c1": "Raspberry Pi", "2c:cf:67": "Raspberry Pi",
}


def host_from_input(text: str) -> tuple[str, int | None]:
    """'https://www.beispiel.de:8443/pfad?x' → ('www.beispiel.de', 8443); 'pi.local' → ('pi.local', None)."""
    from urllib.parse import urlparse
    t = text.strip()
    if not t:
        return "", None
    if "://" not in t:
        if t.count(":") == 1 and t.split(":")[1].isdigit():   # host:port
            h, p = t.split(":")
            return h, int(p)
        t = "//" + t                                         # urlparse braucht // für den Host
    u = urlparse(t)
    try:
        port = u.port
    except ValueError:
        port = None
    if port is None and u.scheme in ("http", "https"):
        port = 80 if u.scheme == "http" else 443
    return (u.hostname or text.strip()), port


def resolve_all(host: str) -> tuple[list[str], list[str]]:
    """Alle IPv4- und IPv6-Adressen eines Namens."""
    v4, v6 = [], []
    for fam, _, _, _, sa in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP):
        ip = sa[0]
        if fam == socket.AF_INET and ip not in v4:
            v4.append(ip)
        elif fam == socket.AF_INET6 and ip not in v6:
            v6.append(ip)
    return v4, v6


def port_name(port: int) -> str:
    if port in PORT_NAMES:
        return PORT_NAMES[port]
    try:
        return socket.getservbyport(port, "tcp")
    except OSError:
        return ""


def tcp_check(host: str, port: int, timeout: float = 0.8) -> float | None:
    """Antwortzeit in ms, wenn Port offen, sonst None."""
    t = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return (time.perf_counter() - t) * 1000
    except OSError:
        return None


def ping(host: str, timeout_ms: int = 1000) -> float | None:
    """Ein ICMP-Ping über das System-ping. Antwortzeit in ms oder None."""
    if IS_WIN:
        cmd = ["ping", "-n", "1", "-w", str(timeout_ms), host]
    elif IS_MAC:
        cmd = ["ping", "-c", "1", "-W", str(timeout_ms), host]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, timeout_ms // 1000)), host]
    rc, out = run_rc(cmd, timeout=timeout_ms / 1000 + 3)
    if rc != 0:
        return None
    if IS_WIN and "TTL=" not in out.upper():
        return None   # Windows meldet „Zielhost nicht erreichbar“ teils mit Rückgabewert 0
    m = re.search(r"(?:time|zeit)\s*[=<]\s*([\d.,]+)", out, re.I)
    return float(m.group(1).replace(",", ".")) if m else 0.0


def is_reachable(host: str, ports: list[int]) -> float | None:
    if not host:
        return None
    for p in ports:
        if (rt := tcp_check(host, p, 1.0)) is not None:
            return rt
    return ping(host, 1000)


def reverse_name(ip: str) -> str:
    try:
        return socket.gethostbyaddr(ip)[0]
    except OSError:
        return ""


VPN_PREFIXES = ("utun", "ppp", "ipsec", "tun", "tap", "wg", "gpd", "vpn", "zt", "tailscale")


WIN_VPN_WORDS = ("vpn", "tap-windows", "tap-", "wireguard", "wintun", "fortinet", "forticlient", "anyconnect",
                 "cisco", "pangp", "palo alto", "globalprotect", "openvpn", "sophos", "check point", "checkpoint",
                 "juniper", "pulse", "zscaler", "tailscale", "zerotier", "wan miniport", "ppp", "netextender",
                 "sonicwall", "barracuda", "nordlynx", "proton")


def _is_vpn_iface(name: str, description: str = "") -> bool:
    if IS_WIN:
        t = f"{name} {description}".lower()
        return any(w in t for w in WIN_VPN_WORDS)
    return name.lower().startswith(VPN_PREFIXES)


VIRTUAL_WORDS = ("vethernet", "hyper-v", "virtualbox", "vmware", "vmnet", "docker", "wsl", "npcap", "loopback",
                 "bridge", "awdl", "llw", "anpi", "parallels")


def _is_virtual(name: str, desc: str = "") -> bool:
    t = f"{name} {desc}".lower()
    return any(w in t for w in VIRTUAL_WORDS)


def _win_interfaces() -> list[dict]:
    rows = powershell_json(
        "Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | ForEach-Object { "
        "$a = Get-NetAdapter -InterfaceIndex $_.InterfaceIndex -IncludeHidden -ErrorAction SilentlyContinue; "
        "[pscustomobject]@{alias=$_.InterfaceAlias; ip=$_.IPAddress; prefix=[int]$_.PrefixLength; "
        "desc=$a.InterfaceDescription; media=[string]$a.PhysicalMediaType; status=[string]$a.Status} }")
    res = []
    for r in rows:
        if not r.get("ip") or (r.get("status") and r["status"] not in ("Up", "1")):
            continue
        name, desc = r.get("alias") or "", r.get("desc") or ""
        vpn = _is_vpn_iface(name, desc)
        media = (r.get("media") or "").lower()
        label = "VPN" if vpn else ("WLAN" if ("802.11" in media or "wi-fi" in name.lower()
                                             or "wlan" in name.lower()) else "LAN")
        res.append({"name": name, "ip": r["ip"], "prefix": int(r.get("prefix") or 24), "label": label,
                    "desc": desc, "_vpn": vpn})
    return res


def _mac_hardware_ports() -> dict[str, str]:
    """macOS: en0 → „WLAN“, en7 → „USB 10/100/1000 LAN“ …"""
    try:
        out = subprocess.run(["networksetup", "-listallhardwareports"], capture_output=True, text=True,
                             timeout=5).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    res, port = {}, None
    for line in out.splitlines():
        if line.startswith("Hardware Port:"):
            port = line.split(":", 1)[1].strip()
        elif line.startswith("Device:") and port:
            label = {"Wi-Fi": "WLAN", "AirPort": "WLAN"}.get(port, port)
            res[line.split(":", 1)[1].strip()] = label
    return res


def interfaces() -> list[dict]:
    """Alle IPv4-Schnittstellen mit Netz: {name, label, ip, network, vpn}.
    Lokale Netze zuerst, dann VPN; Loopback und Link-Local werden ausgelassen."""
    res = []
    try:
        if IS_WIN:
            res = _win_interfaces()
        elif IS_MAC:
            out = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=5).stdout
            hw = _mac_hardware_ports()
            name = None
            for line in out.splitlines():
                m = re.match(r"^(\S+?):\s+flags=", line)
                if m:
                    name = m.group(1)
                    continue
                m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)(?: --> (\S+))? netmask (0x[0-9a-f]+)", line)
                if m and name:
                    prefix = bin(int(m.group(3), 16)).count("1")
                    res.append({"name": name, "ip": m.group(1), "prefix": prefix, "peer": m.group(2) or "",
                                "label": hw.get(name, "VPN" if _is_vpn_iface(name) else name)})
        else:
            out = subprocess.run(["ip", "-o", "-4", "addr"], capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                m = re.search(r"^\d+:\s+(\S+)\s+inet (\d+\.\d+\.\d+\.\d+)/(\d+)", line)
                if m:
                    n = m.group(1)
                    lab = "VPN" if _is_vpn_iface(n) else ("WLAN" if n.startswith("wl") else "LAN")
                    res.append({"name": n, "ip": m.group(2), "prefix": int(m.group(3)), "label": lab})
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    out = []
    for r in res:
        ip = ipaddress.ip_address(r["ip"])
        if ip.is_loopback or ip.is_link_local:
            continue
        r["vpn"] = r.pop("_vpn") if "_vpn" in r else _is_vpn_iface(r["name"])
        r["virtual"] = not r["vpn"] and _is_virtual(r["name"], r.get("desc", ""))
        if r["virtual"]:
            r["label"] = "Virtuell"
        r["network"] = ipaddress.ip_network(f"{r['ip']}/{r['prefix']}", strict=False)
        out.append(r)
    # Reihenfolge: WLAN/LAN (WLAN zuerst), dann VPN, virtuelle Adapter (WSL, Hyper-V, VMs) zuletzt
    out.sort(key=lambda r: (r["virtual"], r["vpn"], r["label"] != "WLAN", r["name"]))
    return out


def _parse_mac_dest(dest: str) -> ipaddress.IPv4Network | None:
    """netstat-Kurzschreibweise: '10.20/16', '192.168.5' (=/24), '10' (=/8)."""
    if dest == "default" or dest.startswith("link#"):
        return None
    addr, _, pre = dest.partition("/")
    parts = addr.split(".")
    if not all(p.isdigit() for p in parts) or len(parts) > 4:
        return None
    prefix = int(pre) if pre else 8 * len(parts)
    try:
        return ipaddress.ip_network(".".join(parts + ["0"] * (4 - len(parts))) + f"/{prefix}", strict=False)
    except ValueError:
        return None


def vpn_routes() -> list[dict]:
    """Netze, die laut Routing-Tabelle über eine VPN-Schnittstelle laufen (Split-Tunnel)."""
    routes = []
    try:
        if IS_WIN:
            vpn_names = {i["name"] for i in interfaces() if i["vpn"]}
            for r in powershell_json("Get-NetRoute -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
                                     "Select-Object DestinationPrefix, InterfaceAlias"):
                if r.get("InterfaceAlias") in vpn_names and r.get("DestinationPrefix"):
                    try:
                        routes.append((ipaddress.ip_network(r["DestinationPrefix"], strict=False),
                                       r["InterfaceAlias"]))
                    except ValueError:
                        pass
        elif IS_MAC:
            out = subprocess.run(["netstat", "-rn", "-f", "inet"], capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                p = line.split()
                if len(p) < 4 or not _is_vpn_iface(p[3]):
                    continue
                n = _parse_mac_dest(p[0])
                if n:
                    routes.append((n, p[3]))
        else:
            out = subprocess.run(["ip", "-4", "route"], capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                m = re.match(r"^(\d+\.\d+\.\d+\.\d+(?:/\d+)?)\s.*\bdev (\S+)", line)
                if m and _is_vpn_iface(m.group(2)):
                    routes.append((ipaddress.ip_network(m.group(1), strict=False), m.group(2)))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    res, seen = [], set()
    for n, iface in routes:
        if (n.prefixlen < 8 or n.prefixlen >= 31 or n.is_multicast or n.is_loopback or n.is_link_local
                or n.network_address == ipaddress.ip_address("255.255.255.255") or n in seen):
            continue
        seen.add(n)
        res.append({"network": n, "iface": iface})
    # Netze, die schon als Schnittstellen-Netz auftauchen, nicht doppelt
    own = {i["network"] for i in interfaces()}
    return [r for r in res if r["network"] not in own]


def _mac_vpn_services() -> list[dict]:
    """macOS: verbundene VPN-Konfigurationen aus den Systemeinstellungen (IKEv2, L2TP, viele Firmen-Clients)."""
    from .osinfo import run
    out = run(["scutil", "--nc", "list"], timeout=5, encoding="utf-8")
    res = []
    for line in out.splitlines():
        if "(Connected)" not in line:
            continue
        m = re.search(r'"([^"]+)"', line)
        if not m:
            continue
        name = m.group(1)
        detail = run(["scutil", "--nc", "show", name], timeout=5, encoding="utf-8")
        srv = re.search(r"(?:RemoteAddress|ServerAddress)\s*:\s*(\S+)", detail)
        res.append({"name": name, "server": srv.group(1) if srv else ""})
    return res


def vpn_info() -> list[dict]:
    """Aktive VPN-Verbindungen: {name, server, iface, ip, peer, networks[]} – für die Anzeige „verbunden mit …“."""
    ifs = [i for i in interfaces() if i["vpn"]]
    if not ifs:
        return []
    routes = vpn_routes()
    services: list[dict] = []
    if IS_MAC:
        services = _mac_vpn_services()
    elif IS_WIN:
        services = [{"name": r.get("Name", ""), "server": r.get("ServerAddress", "")} for r in powershell_json(
            "@(Get-VpnConnection -ErrorAction SilentlyContinue; Get-VpnConnection -AllUserConnection "
            "-ErrorAction SilentlyContinue) | Where-Object { $_.ConnectionStatus -eq 'Connected' } | "
            "Select-Object Name, ServerAddress")]
    out = []
    for n, i in enumerate(ifs):
        svc = services[n] if n < len(services) else {}
        name = svc.get("name") or (i.get("desc") or "").replace(" Adapter", "") or i["name"]
        nets = [str(r["network"]) for r in routes if r["iface"] == i["name"]]
        if i["network"].prefixlen < 31:
            nets.insert(0, str(i["network"]))
        out.append({"name": name, "server": svc.get("server", ""), "iface": i["name"], "ip": i["ip"],
                    "peer": i.get("peer", ""), "networks": nets})
    return out


def vpn_summary() -> str:
    """Einzeilige Beschreibung für Statusanzeigen (leer = kein VPN)."""
    parts = []
    for v in vpn_info():
        t = f"{v['name']}"
        if v["server"]:
            t += f" (Server {v['server']})"
        t += f" · eigene VPN-Adresse {v['ip']}"
        if v["networks"]:
            t += " · erreichbar: " + ", ".join(v["networks"][:6]) + (" …" if len(v["networks"]) > 6 else "")
        parts.append(t)
    return "  |  ".join(parts)


def scan_choices() -> list[dict]:
    """Vorschläge für den Netzwerkscan: {label, cidr, vpn, too_big}."""
    out = []
    for i in interfaces():
        n, note = i["network"], ""
        if n.prefixlen < 22:     # großes Netz → auf das eigene /24 eingrenzen
            note = f"  · Ausschnitt aus /{n.prefixlen}"
            n = ipaddress.ip_network(f"{i['ip']}/24", strict=False)
        elif n.prefixlen >= 31:  # Punkt-zu-Punkt (typisch VPN) → umliegendes /24 als Annahme
            note = "  · angenommen /24"
            n = ipaddress.ip_network(f"{i['ip']}/24", strict=False)
        kind = f"VPN ({i['name']})" if i["vpn"] else f"{i['label']} ({i['name']})"
        out.append({"label": f"{kind} – {n}  · eigene IP {i['ip']}{note}", "cidr": str(n), "vpn": i["vpn"]})
    for r in vpn_routes():
        n = r["network"]
        big = n.num_addresses > 4096
        out.append({"label": f"über VPN ({r['iface']}) – {n}" + ("  · zu groß, bitte eingrenzen" if big else ""),
                    "cidr": str(n), "vpn": True, "too_big": big})
    return out


def local_ip() -> str:
    """IP im lokalen Netz – bevorzugt WLAN/LAN, nicht die VPN-Adresse."""
    for i in interfaces():
        if not i["vpn"] and not i["virtual"]:
            return i["ip"]
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # sendet nichts, wählt nur die Route
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def local_network() -> ipaddress.IPv4Network:
    """Lokales Netz (nicht VPN), für Scans auf max. 1024 Adressen begrenzt."""
    for i in interfaces():
        if not i["vpn"] and not i["virtual"]:
            prefix = max(i["prefix"], 22)
            return ipaddress.ip_network(f"{i['ip']}/{prefix}", strict=False)
    return ipaddress.ip_network(f"{local_ip()}/24", strict=False)


def arp_table() -> dict[str, str]:
    """IP → MAC aus der ARP-Tabelle des Systems (macOS/Linux „arp -an“, Windows „arp -a“)."""
    from .osinfo import run
    out = run(["arp", "-a"] if IS_WIN else ["arp", "-an"], timeout=8)
    if not out and not IS_WIN:
        out = run(["ip", "neigh"])
    res = {}
    for line in out.splitlines():
        ip = re.search(r"(\d+\.\d+\.\d+\.\d+)", line)
        mac = re.search(r"\b([0-9a-f]{1,2}(?:[:-][0-9a-f]{1,2}){5})\b", line, re.I)
        if ip and mac:
            # macOS kürzt führende Nullen (b8:27:eb:1:2:3), Windows nutzt Bindestriche → normalisieren
            parts = re.split(r"[:-]", mac.group(1).lower())
            res[ip.group(1)] = ":".join(p.zfill(2) for p in parts)
    return res


def vendor(mac: str) -> str:
    return VENDOR_PREFIX.get(mac[:8].lower(), "") if mac else ""


def parse_ports(spec: str) -> list[int]:
    """'22,80,8000-8100' → Liste. Leer/'häufig' → COMMON_PORTS."""
    spec = spec.strip().lower()
    if not spec or spec.startswith("häuf") or spec == "common":
        return list(COMMON_PORTS)
    ports: set[int] = set()
    for part in re.split(r"[,\s;]+", spec):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            a, b = int(a), int(b)
            if a > b:
                a, b = b, a
            ports.update(range(max(1, a), min(65535, b) + 1))
        else:
            p = int(part)
            if 1 <= p <= 65535:
                ports.add(p)
    return sorted(ports)


def parse_targets(spec: str) -> list[str]:
    """'192.168.1.0/24' oder '192.168.1.10-50' oder einzelne IP → Adressliste."""
    spec = spec.strip()
    m = re.fullmatch(r"(\d+\.\d+\.\d+\.)(\d+)\s*-\s*(\d+)", spec)
    if m:
        base, a, b = m.group(1), int(m.group(2)), int(m.group(3))
        return [f"{base}{i}" for i in range(min(a, b), max(a, b) + 1) if 0 < i < 255]
    net = ipaddress.ip_network(spec, strict=False)
    if net.num_addresses > 4096:
        raise ValueError("Bitte höchstens 4096 Adressen auf einmal scannen (z. B. /20).")
    hosts = list(net.hosts()) or [net.network_address]
    return [str(h) for h in hosts]


# ---------------------------------------------------------------------------
# Überwachung: einzelne Prüfungen
# ---------------------------------------------------------------------------
def cert_days_left(host: str, port: int = 443, timeout: float = 6.0) -> tuple[int | None, str]:
    """Tage bis Ablauf des TLS-Zertifikats (None + Fehlertext bei Problemen)."""
    import ssl
    from datetime import datetime, timezone
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=host) as s:
                cert = s.getpeercert()
        exp = datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), tz=timezone.utc)
        return (exp - datetime.now(timezone.utc)).days, exp.strftime("%d.%m.%Y")
    except ssl.SSLCertVerificationError as e:
        # Selbst signiert o. Ä.: Ablaufdatum trotzdem auslesen
        days, exp = _unverified_cert_days(host, port, timeout)
        why = "selbst signiert" if "self" in str(e.verify_message) else e.verify_message
        if days is not None:
            return days, f"{exp}, nicht vertrauenswürdig ({why})"
        return None, f"Zertifikat ungültig: {why}"
    except (OSError, ssl.SSLError) as e:
        return None, f"TLS-Fehler: {e}"


def _unverified_cert_days(host: str, port: int, timeout: float) -> tuple[int | None, str]:
    import os
    import ssl
    import tempfile
    from datetime import datetime, timezone
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=host) as s:
                der = s.getpeercert(binary_form=True)
        pem = ssl.DER_cert_to_PEM_cert(der)
        with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as f:
            f.write(pem)
        try:
            info = ssl._ssl._test_decode_cert(f.name)  # CPython-intern, liefert notAfter
        finally:
            os.unlink(f.name)
        exp = datetime.fromtimestamp(ssl.cert_time_to_seconds(info["notAfter"]), tz=timezone.utc)
        return (exp - datetime.now(timezone.utc)).days, exp.strftime("%d.%m.%Y")
    except Exception:  # noqa: BLE001
        return None, ""


def http_check(url: str, timeout: float = 8.0) -> dict:
    """HTTP(S)-Abruf: Status, Antwortzeit, bei https zusätzlich Zertifikat."""
    import ssl
    import urllib.error
    import urllib.request
    from urllib.parse import urlparse
    if "://" not in url:
        url = "http://" + url
    u = urlparse(url)
    res = {"ok": False, "ms": None, "detail": "", "cert_days": None, "cert_info": ""}
    t = time.perf_counter()
    req = urllib.request.Request(url, headers={"User-Agent": "Netzentrale-Monitor"})
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            res["ms"] = (time.perf_counter() - t) * 1000
            res["ok"] = r.status < 400
            res["detail"] = f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        res["ms"] = (time.perf_counter() - t) * 1000
        # 401/403 heißt: Dienst läuft, will aber Anmeldung → als erreichbar werten
        res["ok"] = e.code in (401, 403)
        res["detail"] = f"HTTP {e.code}"
    except urllib.error.URLError as e:
        reason = e.reason
        if isinstance(reason, ssl.SSLCertVerificationError):
            # Gerät läuft, Zertifikat ist nur nicht offiziell (typisch bei internen Geräten) → erneut ohne Prüfung
            ctx2 = ssl.create_default_context()
            ctx2.check_hostname = False
            ctx2.verify_mode = ssl.CERT_NONE
            try:
                t = time.perf_counter()
                with urllib.request.urlopen(req, timeout=timeout, context=ctx2) as r:
                    res["ms"] = (time.perf_counter() - t) * 1000
                    res["ok"] = r.status < 400
                    res["detail"] = f"HTTP {r.status} (Zertifikat nicht vertrauenswürdig)"
            except urllib.error.HTTPError as e2:
                res["ms"] = (time.perf_counter() - t) * 1000
                res["ok"] = e2.code in (401, 403)
                res["detail"] = f"HTTP {e2.code} (Zertifikat nicht vertrauenswürdig)"
            except (urllib.error.URLError, OSError) as e2:
                res["detail"] = str(getattr(e2, "reason", e2))
        else:
            res["detail"] = str(reason)
    except (OSError, ValueError) as e:
        res["detail"] = str(e)
    if u.scheme == "https" and u.hostname:
        days, info = cert_days_left(u.hostname, u.port or 443)
        res["cert_days"], res["cert_info"] = days, info
    return res


def is_lunatone_gateway(ip: str, port: int = 80) -> bool:
    """Erkennt ein Lunatone DALI-2 IoT Gateway an seiner REST-Antwort auf /info."""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://{ip}:{port}/info", timeout=1.2) as r:
            if r.status != 200:
                return False
            info = json.loads(r.read(4096))
        return isinstance(info, dict) and "descriptor" in info and "lines" in (info.get("descriptor") or {})
    except Exception:  # noqa: BLE001
        return False


def check_target(kind: str, target: str, port: int | None = None) -> dict:
    if kind == "web":
        return http_check(target)
    if kind == "tcp":
        ms = tcp_check(target, int(port or 80), 3.0)
        return {"ok": ms is not None, "ms": ms, "detail": f"Port {port} " + ("offen" if ms is not None else "nicht erreichbar"),
                "cert_days": None, "cert_info": ""}
    ms = ping(target, 2000)
    if ms is None:  # zweiter Versuch gegen kurzzeitige Paketverluste
        ms = ping(target, 2000)
    return {"ok": ms is not None, "ms": ms, "detail": "Ping " + ("ok" if ms is not None else "keine Antwort"),
            "cert_days": None, "cert_info": ""}


def notify(title: str, text: str):
    """Mitteilung: macOS-Mitteilungszentrale, Windows über das Taskleisten-Symbol der App."""
    if NOTIFIER:
        try:
            NOTIFIER(title, text)
            return
        except Exception:  # noqa: BLE001
            pass
    if IS_MAC:
        def esc(s):
            return s.replace("\\", "\\\\").replace('"', '\\"')
        try:
            subprocess.Popen(["osascript", "-e",
                              f'display notification "{esc(text)}" with title "{esc(title)}" sound name "Basso"'])
            return
        except OSError:
            pass
    print(f"[Mitteilung] {title}: {text}")
