"""Hintergrund-Threads für Scans und Statusprüfung (blockieren nie die Oberfläche)."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import QThread, Signal

from . import net


class ScanWorker(QThread):
    """Netzwerkscan: Ping + Prüfung typischer Ports, danach ARP-Tabelle."""
    found = Signal(dict)          # {"ip","name","mac","vendor","ports":[...],"ms"}
    progress = Signal(int, int)
    done = Signal(int)            # Anzahl gefundener Geräte

    def __init__(self, targets: list[str], resolve_names=True, parent=None):
        super().__init__(parent)
        self.targets = targets
        self.resolve_names = resolve_names
        self._stop = False

    def stop(self):
        self._stop = True

    def _probe(self, ip: str) -> dict | None:
        if self._stop:
            return None
        ms = net.ping(ip, 800)
        open_ports = []
        for p in net.SCAN_PORTS:
            if self._stop:
                return None
            rt = net.tcp_check(ip, p, 0.35)
            if rt is not None:
                open_ports.append(p)
                if ms is None:
                    ms = rt
        if ms is None and not open_ports:
            return None
        dali = 80 in open_ports and net.is_lunatone_gateway(ip)
        return {"ip": ip, "ms": ms, "ports": open_ports, "dali": dali,
                "name": net.reverse_name(ip) if self.resolve_names else "", "mac": "",
                "vendor": "Lunatone DALI-2 IoT" if dali else ""}

    def run(self):
        total, n, hits = len(self.targets), 0, {}
        with ThreadPoolExecutor(max_workers=128) as ex:
            futs = [ex.submit(self._probe, ip) for ip in self.targets]
            for f in as_completed(futs):
                n += 1
                res = f.result()
                if res:
                    hits[res["ip"]] = res
                    self.found.emit(res)
                if n % 4 == 0 or n == total:
                    self.progress.emit(n, total)
                if self._stop:
                    ex.shutdown(cancel_futures=True)
                    break
        # ARP-Tabelle: liefert MAC-Adressen und Geräte, die Ping blocken
        arp = net.arp_table()
        targets = set(self.targets)
        own = net.local_ip()
        for ip, mac in arp.items():
            if ip not in targets or mac in ("ff:ff:ff:ff:ff:ff", "00:00:00:00:00:00"):
                continue
            res = hits.get(ip) or {"ip": ip, "ms": None, "ports": [], "mac": "", "vendor": "",
                                   "name": net.reverse_name(ip) if self.resolve_names else ""}
            res["mac"] = mac
            res["vendor"] = res.get("vendor") or net.vendor(mac)
            hits[ip] = res
            self.found.emit(res)
        if own in targets and own in hits:
            hits[own]["name"] = (hits[own]["name"] or "") + " (dieser Mac)"
            self.found.emit(hits[own])
        self.done.emit(len(hits))


class PortScanWorker(QThread):
    open_port = Signal(int, float)
    progress = Signal(int, int)
    done = Signal(int, float)     # Anzahl offen, Dauer s
    failed = Signal(str)
    resolved = Signal(str, str)   # gescannte IP, Beschreibung

    def __init__(self, host: str, ports: list[int], timeout: float = 0.6, parent=None):
        super().__init__(parent)
        self.host, self.ports, self.timeout = host, ports, timeout
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            v4, v6 = net.resolve_all(self.host)
        except OSError as e:
            self.failed.emit(f"Name „{self.host}“ nicht auflösbar: {e}")
            return
        if not v4:
            self.failed.emit(f"„{self.host}“ hat nur IPv6-Adressen ({', '.join(v6)}) – Portscan nutzt IPv4.")
            return
        ip = v4[0]
        rev = net.reverse_name(ip)
        info = f"{self.host} → {ip}"
        if rev and rev != self.host:
            info += f"  (Rückwärtsname: {rev})"
        if len(v4) > 1:
            info += f"  · weitere IPv4: {', '.join(v4[1:])}"
        if v6:
            info += f"  · IPv6: {', '.join(v6[:2])}"
        self.resolved.emit(ip, info)
        t0, n, found, total = time.time(), 0, 0, len(self.ports)

        def probe(p):
            return p, (None if self._stop else net.tcp_check(ip, p, self.timeout))

        with ThreadPoolExecutor(max_workers=200) as ex:
            for f in as_completed([ex.submit(probe, p) for p in self.ports]):
                n += 1
                p, rt = f.result()
                if rt is not None:
                    found += 1
                    self.open_port.emit(p, rt)
                if n % 25 == 0 or n == total:
                    self.progress.emit(n, total)
                if self._stop:
                    ex.shutdown(cancel_futures=True)
                    break
        self.done.emit(found, time.time() - t0)


class StatusWorker(QThread):
    """Prüft den Online-Status aller Geräte der Verbindungsliste."""
    status = Signal(str, object)   # device id, ms oder None

    def __init__(self, devices, parent=None):
        super().__init__(parent)
        self.devices = [d for d in devices if d.host]

    def run(self):
        def check(d):
            return d.id, net.is_reachable(d.host, d.probe_ports())
        with ThreadPoolExecutor(max_workers=32) as ex:
            for f in as_completed([ex.submit(check, d) for d in self.devices]):
                self.status.emit(*f.result())
