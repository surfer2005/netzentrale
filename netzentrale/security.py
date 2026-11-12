"""Sicherheits-Check für eigene Systeme – nur beobachtend.

Was geprüft wird:
  * welche Dienste offen sind und wie riskant sie grundsätzlich sind
  * was Dienste freiwillig preisgeben (Banner, Versionen, HTTP-Header, Zertifikate)
  * ob ein Dienst OHNE Anmeldung nutzbar ist (VNC-Sicherheitstyp, anonymes MQTT)
Was NICHT passiert: keine Passwortversuche, keine Exploits, keine Änderungen am Zielsystem.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from . import net

HOCH, MITTEL, NIEDRIG, INFO, OK = "Hoch", "Mittel", "Niedrig", "Info", "OK"
LEVEL_ORDER = {HOCH: 0, MITTEL: 1, NIEDRIG: 2, INFO: 3, OK: 4}

CHECK_PORTS = sorted(set(net.COMMON_PORTS) | {
    21, 23, 69, 111, 135, 139, 445, 502, 1433, 1521, 1883, 2375, 2376, 3306, 3389, 4840, 5000, 5432,
    5900, 5901, 5902, 5903, 5985, 5986, 6379, 7547, 8080, 8443, 8883, 9000, 9200, 10000, 11211, 27017,
    47808, 20000, 44818, 102,
})

# Port → (Risiko lokal, Risiko öffentlich, Titel, Erklärung, Empfehlung)
PORT_RULES = {
    23: (HOCH, HOCH, "Telnet offen", "Telnet überträgt Benutzername und Passwort unverschlüsselt.",
         "Telnet abschalten und SSH verwenden."),
    21: (MITTEL, HOCH, "FTP offen", "FTP überträgt Zugangsdaten und Dateien unverschlüsselt.",
         "SFTP oder FTPS verwenden, FTP abschalten."),
    69: (MITTEL, HOCH, "TFTP möglich", "TFTP hat keinerlei Anmeldung.", "Nur bei Bedarf und nur intern aktivieren."),
    135: (NIEDRIG, HOCH, "Windows RPC offen", "Typischer Windows-Dienst, aus dem Internet ein Angriffsziel.",
          "Nur im internen Netz erreichbar machen."),
    139: (NIEDRIG, HOCH, "NetBIOS offen", "Alte Windows-Dateifreigabe.", "SMBv1/NetBIOS deaktivieren, nur intern."),
    445: (NIEDRIG, HOCH, "SMB-Dateifreigabe offen", "Dateifreigaben gehören nie ins Internet (WannaCry & Co.).",
          "Intern okay; aus dem Internet sperren, SMBv1 abschalten."),
    3389: (NIEDRIG, HOCH, "RDP offen", "Fernwartung für Windows – aus dem Internet ein Hauptziel für Angriffe.",
           "Nur über VPN erreichbar machen, Netzwerkebenen-Authentifizierung (NLA) aktivieren."),
    5985: (NIEDRIG, HOCH, "WinRM (HTTP) offen", "Windows-Fernverwaltung unverschlüsselt.", "Nur intern, besser 5986 (HTTPS)."),
    502: (MITTEL, HOCH, "Modbus TCP offen", "Modbus kennt keine Anmeldung – jeder im Netz kann Werte lesen und schreiben.",
          "Steuerungsnetz vom Büro-/Gästenetz trennen (eigenes VLAN/Firewall), nie ins Internet."),
    102: (MITTEL, HOCH, "Siemens S7 offen", "S7-Kommunikation ohne Anmeldung.", "Steuerungsnetz abschotten."),
    44818: (MITTEL, HOCH, "EtherNet/IP offen", "Industrieprotokoll ohne Anmeldung.", "Steuerungsnetz abschotten."),
    20000: (MITTEL, HOCH, "DNP3 offen", "Leittechnik-Protokoll.", "Steuerungsnetz abschotten."),
    4840: (NIEDRIG, MITTEL, "OPC UA offen", "Industrie-Schnittstelle.", "Sicherheitsmodus „Sign & Encrypt“ und Benutzeranmeldung nutzen."),
    2375: (HOCH, HOCH, "Docker-API ohne TLS offen", "Wer diesen Port erreicht, kann den Rechner vollständig übernehmen.",
           "Docker-API nicht über TCP freigeben oder nur mit TLS (2376) und Zertifikaten."),
    6379: (MITTEL, HOCH, "Redis offen", "Redis hat oft kein Passwort.", "An localhost binden oder Passwort/ACL setzen."),
    11211: (MITTEL, HOCH, "Memcached offen", "Keine Anmeldung, wurde für DDoS missbraucht.", "An localhost binden."),
    27017: (MITTEL, HOCH, "MongoDB offen", "Datenbanken gehören nicht offen ins Netz.", "Anmeldung aktivieren, nur intern."),
    9200: (MITTEL, HOCH, "Elasticsearch offen", "Oft ohne Anmeldung erreichbar.", "Security-Funktionen aktivieren, nur intern."),
    3306: (NIEDRIG, HOCH, "MySQL/MariaDB offen", "Datenbank direkt erreichbar.", "Nur für Anwendungsserver freigeben."),
    5432: (NIEDRIG, HOCH, "PostgreSQL offen", "Datenbank direkt erreichbar.", "Nur für Anwendungsserver freigeben."),
    1433: (NIEDRIG, HOCH, "MS SQL Server offen", "Datenbank direkt erreichbar.", "Nur für Anwendungsserver freigeben."),
    1521: (NIEDRIG, HOCH, "Oracle DB offen", "Datenbank direkt erreichbar.", "Nur für Anwendungsserver freigeben."),
    111: (NIEDRIG, MITTEL, "RPCbind offen", "Gibt Auskunft über weitere Dienste.", "Abschalten, wenn kein NFS genutzt wird."),
    7547: (NIEDRIG, HOCH, "TR-069 (Router-Fernwartung) offen", "Provider-Fernwartung, schon oft angegriffen.", "Prüfen, ob nötig."),
    10000: (NIEDRIG, MITTEL, "Webmin offen", "Server-Verwaltung im Browser.", "Nur intern/VPN, aktuell halten."),
    9100: (INFO, MITTEL, "Drucker RAW (9100) offen", "Jeder im Netz kann direkt drucken.", "Nur intern."),
    5000: (INFO, NIEDRIG, "Port 5000 offen", "Häufig NAS-Oberfläche (Synology) oder Entwicklungsserver.", "Prüfen, was dort läuft."),
}


@dataclass
class Finding:
    level: str
    title: str
    detail: str
    advice: str = ""
    port: int | None = None


# ---------------------------------------------------------------------------
def _recv(s, n=1024, timeout=3.0) -> bytes:
    s.settimeout(timeout)
    try:
        return s.recv(n)
    except OSError:
        return b""


def banner(ip: str, port: int) -> str:
    try:
        with socket.create_connection((ip, port), timeout=3) as s:
            return _recv(s, 512, 2.5).decode(errors="replace").strip()
    except OSError:
        return ""


def check_ssh(ip, port) -> list[Finding]:
    b = banner(ip, port)
    if not b.startswith("SSH-"):
        return [Finding(INFO, "SSH erreichbar", f"Port {port} offen, keine SSH-Kennung gelesen.", port=port)]
    out = [Finding(INFO, "SSH-Version", b.splitlines()[0], "Aktuell halten; Anmeldung per Schlüssel statt Passwort, "
                   "Root-Anmeldung abschalten.", port)]
    m = re.search(r"OpenSSH_(\d+)\.(\d+)", b)
    if m and (int(m.group(1)), int(m.group(2))) < (8, 0):
        out.append(Finding(MITTEL, "Sehr alte OpenSSH-Version", f"{b.splitlines()[0]} – Versionen vor 8.0 sind "
                           "seit Jahren ohne Sicherheitsupdates.", "Betriebssystem/OpenSSH aktualisieren.", port))
    if b.startswith("SSH-1."):
        out.append(Finding(HOCH, "SSH Protokoll 1", "Veraltetes, unsicheres Protokoll.", "SSH 2 erzwingen.", port))
    return out


def check_vnc(ip, port) -> list[Finding]:
    """Liest nur die Begrüßung und die angebotenen Sicherheitstypen (kein Login)."""
    try:
        with socket.create_connection((ip, port), timeout=3) as s:
            ver = _recv(s, 12)
            if not ver.startswith(b"RFB "):
                return [Finding(INFO, "Port wie VNC, aber keine VNC-Antwort", f"Port {port}", port=port)]
            v = ver.decode(errors="replace").strip()
            s.sendall(b"RFB 003.008\n")
            data = _recv(s, 64)
    except OSError as e:
        return [Finding(INFO, "VNC nicht prüfbar", str(e), port=port)]
    types = []
    if data:
        n = data[0]
        types = list(data[1:1 + n]) if n else []
    names = {1: "keine Anmeldung", 2: "VNC-Passwort", 5: "RA2 (RealVNC)", 6: "RA2ne (RealVNC)", 13: "RealVNC",
             16: "Tight", 18: "TLS", 19: "VeNCrypt", 30: "Apple (ARD)", 35: "Apple"}
    desc = ", ".join(names.get(t, str(t)) for t in types) or "unbekannt"
    if 1 in types:
        return [Finding(HOCH, "VNC ohne Passwort", f"{v}: Server bietet Zugang ohne Anmeldung an ({desc}).",
                        "Sofort ein Passwort setzen oder VNC abschalten.", port)]
    out = [Finding(INFO, "VNC erreichbar", f"{v}, Anmeldearten: {desc}", "Starkes Passwort, nur intern/VPN.", port)]
    if types == [2]:
        out.append(Finding(NIEDRIG, "VNC nur mit klassischem VNC-Passwort",
                           "Klassisches VNC-Passwort: max. 8 Zeichen, Übertragung nicht verschlüsselt.",
                           "Nur im internen Netz oder über VPN/SSH-Tunnel nutzen.", port))
    return out


def check_mqtt(ip, port) -> list[Finding]:
    """Prüft, ob der Broker eine Verbindung OHNE Benutzername akzeptiert (sofortiges Trennen)."""
    cid = b"nz-sec-" + str(int(time.time()) % 100000).encode()
    payload = b"\x00\x04MQTT\x04\x02\x00\x0a" + len(cid).to_bytes(2, "big") + cid
    pkt = b"\x10" + bytes([len(payload)]) + payload
    try:
        with socket.create_connection((ip, port), timeout=3) as s:
            s.sendall(pkt)
            r = _recv(s, 4)
            if len(r) >= 4 and r[0] == 0x20:
                if r[3] == 0:
                    s.sendall(b"\xe0\x00")   # DISCONNECT
                    return [Finding(HOCH if not ipaddress.ip_address(ip).is_private else MITTEL,
                                    "MQTT ohne Anmeldung nutzbar",
                                    "Der Broker akzeptiert Verbindungen ohne Benutzername – jeder im Netz kann "
                                    "mitlesen und Befehle senden.",
                                    "allow_anonymous false + Benutzer/Passwort; besser TLS (8883).", port)]
                return [Finding(OK, "MQTT verlangt Anmeldung", f"Anonyme Verbindung abgelehnt (Code {r[3]}).",
                                "Für Verbindungen über Netze hinweg TLS (8883) verwenden.", port)]
    except OSError as e:
        return [Finding(INFO, "MQTT nicht prüfbar", str(e), port=port)]
    return [Finding(INFO, "MQTT-Port offen", "Keine eindeutige Antwort des Brokers.", port=port)]


def _tls_version_accepted(ip, port, host, version) -> bool | None:
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.minimum_version = version
        ctx.maximum_version = version
        try:
            ctx.set_ciphers("ALL:@SECLEVEL=0")
        except ssl.SSLError:
            pass
        with socket.create_connection((ip, port), timeout=4) as raw:
            with ctx.wrap_socket(raw, server_hostname=host):
                return True
    except ssl.SSLError as e:
        # „no protocols available“ = unser System kann die alte Version gar nicht mehr → nicht prüfbar
        return None if "NO_PROTOCOLS" in str(e).upper() or "UNSUPPORTED" in str(e).upper() else False
    except (OSError, ValueError):
        return False


def check_https(ip, port, host) -> list[Finding]:
    out = []
    days, info = net.cert_days_left(host if not _is_ip(host) else ip, port)
    if days is None:
        out.append(Finding(MITTEL, "Zertifikat ungültig oder nicht lesbar", info,
                           "Gültiges Zertifikat einsetzen (z. B. Let's Encrypt oder interne CA).", port))
    elif "nicht vertrauenswürdig" in info:
        lvl = NIEDRIG if ipaddress.ip_address(ip).is_private else MITTEL
        out.append(Finding(lvl, "Selbst signiertes / nicht vertrauenswürdiges Zertifikat", f"Gültig bis {info}",
                           "Intern üblich; Browserwarnungen gewöhnen Nutzer ans Wegklicken – besser interne CA.", port))
        if days < 0:
            out.append(Finding(HOCH, "Zertifikat abgelaufen", info, "Zertifikat erneuern.", port))
        elif days < 21:
            out.append(Finding(MITTEL, "Zertifikat läuft bald ab", f"noch {days} Tage", "Rechtzeitig erneuern.", port))
    elif days < 0:
        out.append(Finding(HOCH, "Zertifikat abgelaufen", info, "Zertifikat erneuern.", port))
    elif days < 21:
        out.append(Finding(MITTEL, "Zertifikat läuft bald ab", f"noch {days} Tage ({info})", "Rechtzeitig erneuern.", port))
    else:
        out.append(Finding(OK, "Zertifikat gültig", f"noch {days} Tage ({info})", port=port))
    old = []
    for name, v in (("TLS 1.0", ssl.TLSVersion.TLSv1), ("TLS 1.1", ssl.TLSVersion.TLSv1_1)):
        r = _tls_version_accepted(ip, port, host, v)
        if r:
            old.append(name)
    if old:
        out.append(Finding(MITTEL, "Veraltete TLS-Versionen erlaubt", ", ".join(old) + " werden angenommen.",
                           "Nur TLS 1.2 und 1.3 zulassen.", port))
    return out


def _is_ip(h: str) -> bool:
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return False


def check_http(ip, port, host, https: bool) -> list[Finding]:
    import urllib.error
    import urllib.request
    scheme = "https" if https else "http"
    url = f"{scheme}://{host if not _is_ip(host) else ip}:{port}/"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=ctx))
    try:
        r = opener.open(urllib.request.Request(url, headers={"User-Agent": "Netzentrale-Sicherheitscheck"}), timeout=6)
        status, headers = r.status, r.headers
    except urllib.error.HTTPError as e:
        status, headers = e.code, e.headers
    except (urllib.error.URLError, OSError, ValueError) as e:
        return [Finding(INFO, f"Web ({scheme.upper()}) nicht abrufbar", str(getattr(e, "reason", e)), port=port)]
    out = []
    loc = headers.get("Location", "")
    if not https:
        if status in (301, 302, 307, 308) and loc.startswith("https://"):
            out.append(Finding(OK, "HTTP leitet auf HTTPS um", f"→ {loc}", port=port))
        else:
            out.append(Finding(MITTEL, "Weboberfläche unverschlüsselt (HTTP)",
                               f"HTTP {status} ohne Umleitung auf HTTPS – Anmeldedaten gehen im Klartext übers Netz.",
                               "HTTPS einrichten und HTTP darauf umleiten.", port))
    srv = headers.get("Server", "")
    if srv and re.search(r"\d", srv):
        out.append(Finding(NIEDRIG, "Server verrät Softwareversion", f"Server: {srv}",
                           "Versionsangabe ausblenden (z. B. server_tokens off).", port))
    if headers.get("X-Powered-By"):
        out.append(Finding(NIEDRIG, "X-Powered-By verrät Technik", f"X-Powered-By: {headers['X-Powered-By']}",
                           "Header entfernen.", port))
    if https and not headers.get("Strict-Transport-Security"):
        out.append(Finding(NIEDRIG, "HSTS fehlt", "Browser werden nicht verpflichtet, immer HTTPS zu nutzen.",
                           "Header Strict-Transport-Security setzen.", port))
    missing = [h for h in ("X-Frame-Options", "X-Content-Type-Options", "Content-Security-Policy")
               if not headers.get(h)]
    if len(missing) == 3 and status < 400:
        out.append(Finding(NIEDRIG, "Sicherheits-Header fehlen", ", ".join(missing),
                           "Bei Weboberflächen mit Anmeldung: Header ergänzen (Schutz vor Clickjacking u. a.).", port))
    if status in (401,) and headers.get("WWW-Authenticate", "").lower().startswith("basic") and not https:
        out.append(Finding(HOCH, "Basic-Anmeldung über HTTP", "Passwort wird praktisch im Klartext übertragen.",
                           "Nur über HTTPS anbieten.", port))
    return out


def check_banner_generic(ip, port, title) -> list[Finding]:
    b = banner(ip, port)
    return [Finding(INFO, title, b[:200], port=port)] if b else []


# ---------------------------------------------------------------------------
def run_check(target: str, progress=None, ports: list[int] | None = None) -> tuple[dict, list[Finding]]:
    """Kompletter Check. progress(text) wird laufend aufgerufen."""
    def say(t):
        if progress:
            progress(t)

    host, url_port = net.host_from_input(target)
    say(f"Löse {host} auf …")
    v4, v6 = net.resolve_all(host)
    if not v4:
        raise OSError(f"Keine IPv4-Adresse für {host}")
    ip = v4[0]
    public = not ipaddress.ip_address(ip).is_private
    meta = {"host": host, "ip": ip, "public": public, "reverse": net.reverse_name(ip), "open": []}
    findings: list[Finding] = []
    if public:
        findings.append(Finding(INFO, "Öffentliche IP-Adresse",
                                f"{ip} ist aus dem Internet erreichbar – offene Dienste werden strenger bewertet.",
                                "Nur prüfen, wenn das System euch gehört oder ihr beauftragt seid."))

    ports = sorted(set(ports or CHECK_PORTS) | ({url_port} if url_port else set()))
    say(f"Prüfe {len(ports)} Ports auf {ip} …")
    with ThreadPoolExecutor(max_workers=100) as ex:
        res = list(ex.map(lambda p: (p, net.tcp_check(ip, p, 1.0)), ports))
    open_ports = [p for p, rt in res if rt is not None]
    meta["open"] = open_ports
    if not open_ports:
        findings.append(Finding(OK, "Keine offenen Ports gefunden", f"{len(ports)} geprüfte Ports geschlossen oder gefiltert."))
        return meta, findings

    for p in open_ports:
        if p in PORT_RULES:
            lvl_local, lvl_pub, title, why, advice = PORT_RULES[p]
            findings.append(Finding(lvl_pub if public else lvl_local, title, why, advice, p))

    web_https = [p for p in open_ports if p in (443, 8443, 5001, 9443)]
    web_http = [p for p in open_ports if p in (80, 8080, 8000, 8008, 8081, 8123, 3000, 5000, 9000)]
    for p in open_ports:
        say(f"Untersuche Port {p} ({net.port_name(p) or 'unbekannt'}) …")
        if p == 22:
            findings += check_ssh(ip, p)
        elif p in (5900, 5901, 5902, 5903):
            findings += check_vnc(ip, p)
        elif p == 1883:
            findings += check_mqtt(ip, p)
        elif p == 8883:
            findings.append(Finding(OK, "MQTT mit TLS angeboten", "Verschlüsselter Broker-Port.", port=p))
        elif p in (21, 25, 110, 143):
            findings += check_banner_generic(ip, p, f"Kennung {net.port_name(p) or p}")
        if p in web_https:
            findings += check_https(ip, p, host)
            findings += check_http(ip, p, host, True)
        elif p in web_http:
            findings += check_http(ip, p, host, False)
    if any(p in open_ports for p in (80, 8080)) and not web_https:
        findings.append(Finding(NIEDRIG, "Kein HTTPS-Port gefunden", "Nur unverschlüsselte Weboberfläche gefunden.",
                                "HTTPS anbieten.", None))
    findings.sort(key=lambda f: (LEVEL_ORDER[f.level], f.port or 0))
    return meta, findings


def report_text(meta: dict, findings: list[Finding]) -> str:
    from datetime import datetime
    lines = [f"Sicherheits-Check {meta['host']} ({meta['ip']})", f"Erstellt: {datetime.now():%d.%m.%Y %H:%M} mit Netzentrale",
             f"Offene Ports: {', '.join(map(str, meta['open'])) or 'keine'}", ""]
    for f in findings:
        lines.append(f"[{f.level}] {f.title}" + (f" (Port {f.port})" if f.port else ""))
        lines.append(f"    {f.detail}")
        if f.advice:
            lines.append(f"    → {f.advice}")
    lines += ["", "Hinweis: Beobachtender Check (keine Angriffe, keine Passwortversuche). Ersetzt keinen Penetrationstest."]
    return "\n".join(lines)
