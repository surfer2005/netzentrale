"""Einfacher Whois-Client (Port 43) – Windows hat kein whois-Programm.
Fragt zuerst whois.iana.org und folgt dem Verweis auf den zuständigen Server."""
from __future__ import annotations

import ipaddress
import re
import socket

SPECIAL = {"de": "whois.denic.eu", "com": "whois.verisign-grs.com", "net": "whois.verisign-grs.com",
           "org": "whois.pir.org", "eu": "whois.eu", "ch": "whois.nic.ch", "at": "whois.nic.at"}


def _query(server: str, q: str, timeout: float = 8.0) -> str:
    if server == "whois.denic.eu":
        q = f"-T dn,ace {q}"      # DENIC liefert so die übliche Ausgabe
    with socket.create_connection((server, 43), timeout=timeout) as s:
        s.sendall((q + "\r\n").encode("idna" if not q.isascii() else "ascii", errors="ignore"))
        data = b""
        while True:
            chunk = s.recv(8192)
            if not chunk:
                break
            data += chunk
            if len(data) > 512_000:
                break
    for enc in ("utf-8", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def lookup(query: str, emit=print) -> None:
    q = query.strip().lower().rstrip(".")
    is_ip = True
    try:
        ipaddress.ip_address(q)
    except ValueError:
        is_ip = False
    if not is_ip:
        parts = q.split(".")
        # Subdomain abschneiden (www.beispiel.de → beispiel.de); Sonderfälle wie co.uk bleiben unberücksichtigt
        if len(parts) > 2:
            q = ".".join(parts[-2:])
    tld = q.rsplit(".", 1)[-1]
    server = None if is_ip else SPECIAL.get(tld)
    if not server:
        emit(f"Frage whois.iana.org nach dem zuständigen Server für {q} …\n")
        iana = _query("whois.iana.org", q)
        m = re.search(r"^(?:refer|whois):\s*(\S+)", iana, re.M | re.I)
        if not m:
            emit(iana)
            return
        server = m.group(1)
    emit(f"Server: {server}\n\n")
    text = _query(server, q)
    emit(text)
    # Registrar-Whois (z. B. bei .com) – einem weiteren Verweis folgen
    m = re.search(r"Registrar WHOIS Server:\s*(\S+)", text, re.I)
    if m and m.group(1).lower() != server.lower():
        emit(f"\n\n==== Registrar: {m.group(1)} ====\n\n")
        try:
            emit(_query(m.group(1), q))
        except OSError as e:
            emit(f"(nicht erreichbar: {e})")
