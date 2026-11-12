"""Werkzeuge wie im früheren macOS-Netzwerkdienstprogramm:
Info, Ping, Lookup, Traceroute, Whois, Netstat. Nutzt die System-Befehle und
zeigt deren Ausgabe live an."""
from __future__ import annotations

import shutil
import threading

from PySide6.QtCore import QProcess, QThread, Signal
from PySide6.QtGui import QFontDatabase, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QSpinBox, QVBoxLayout, QWidget,
)

from .osinfo import CONSOLE_ENCODING, CREATE_NO_WINDOW, IS_MAC, IS_WIN


class _PyRunner(QThread):
    """Führt eine Python-Funktion aus, die ihre Ausgabe zeilenweise über emit() liefert."""
    line = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn
        self.stop_event = threading.Event()

    def run(self):
        try:
            self.fn(self.line.emit)
        except Exception as e:  # noqa: BLE001
            self.line.emit(f"\nFehler: {e}\n")


class CommandPage(QWidget):
    """Basis: Eingabezeile + Start/Stopp + Live-Ausgabe eines Systembefehls."""
    title = ""
    needs_host = True
    host_hint = "Adresse oder Name, z. B. 192.168.1.1 oder beispiel.de"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc: QProcess | None = None
        self.py: _PyRunner | None = None
        lay = QVBoxLayout(self)
        self.bar = QHBoxLayout()
        if self.needs_host:
            self.bar.addWidget(QLabel("Adresse:"))
            self.host = QLineEdit()
            self.host.setPlaceholderText(self.host_hint)
            self.host.returnPressed.connect(self.toggle)
            self.bar.addWidget(self.host, 1)
        self.add_options(self.bar)
        if not self.needs_host:
            self.bar.addStretch(1)
        self.btn = QPushButton(self.start_label())
        self.btn.clicked.connect(self.toggle)
        self.bar.addWidget(self.btn)
        lay.addLayout(self.bar)

        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        f = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        f.setPointSize(max(f.pointSize(), 12))
        self.out.setFont(f)
        self.out.setLineWrapMode(QPlainTextEdit.NoWrap)
        lay.addWidget(self.out, 1)

    # --- von Unterklassen zu füllen ---
    def add_options(self, bar: QHBoxLayout):
        pass

    def command(self, host: str) -> list[str]:
        raise NotImplementedError

    def python_job(self, host: str):
        """Optional: Python-Funktion fn(emit) statt eines Systembefehls (z. B. unter Windows)."""
        return None

    def start_label(self) -> str:
        return "Start"

    # --- Ablauf ---
    def run_for(self, host: str):
        if self.needs_host:
            self.host.setText(host)
        if not self.running():
            self.toggle()

    def running(self) -> bool:
        return bool((self.proc and self.proc.state() != QProcess.NotRunning) or (self.py and self.py.isRunning()))

    def toggle(self):
        if self.running():
            if self.proc and self.proc.state() != QProcess.NotRunning:
                self.proc.kill()
            if self.py and self.py.isRunning():
                self.py.stop_event.set()
                self.out.appendPlainText("\n[wird abgebrochen …]")
            return
        host = self.host.text().strip() if self.needs_host else ""
        if host and ("://" in host or "/" in host):
            from .net import host_from_input
            host = host_from_input(host)[0]   # URL eingefügt → nur den Rechnernamen verwenden
        if self.needs_host and not host:
            return
        job = self.python_job(host)
        if job:
            self.out.clear()
            self.py = _PyRunner(job, self)
            self.py.line.connect(self._append)
            self.py.finished.connect(lambda: self._finished(0, None))
            self.py.start()
            self.btn.setText("Stopp")
            return
        cmd = self.command(host)
        if not shutil.which(cmd[0]):
            self.out.setPlainText(f"Befehl „{cmd[0]}“ ist auf diesem System nicht vorhanden.")
            return
        self.out.clear()
        self.out.appendPlainText("$ " + " ".join(cmd) + "\n")
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(self._finished)
        if IS_WIN:
            # kein schwarzes Konsolenfenster für ping, tracert & Co.
            def no_window(args):
                args.flags |= CREATE_NO_WINDOW
            self.proc.setCreateProcessArgumentsModifier(no_window)
        self.proc.start(cmd[0], cmd[1:])
        self.btn.setText("Stopp")

    def _read(self):
        self._append(bytes(self.proc.readAllStandardOutput()).decode(CONSOLE_ENCODING, errors="replace"))

    def _append(self, text: str):
        self.out.moveCursor(QTextCursor.End)
        self.out.insertPlainText(text)
        self.out.ensureCursorVisible()

    def _finished(self, code, _status):
        self.btn.setText(self.start_label())
        if code not in (0, None):
            self.out.appendPlainText(f"\n[beendet, Code {code}]")


class PingPage(CommandPage):
    def add_options(self, bar):
        bar.addWidget(QLabel("Anzahl:"))
        self.count = QSpinBox()
        self.count.setRange(0, 9999)
        self.count.setValue(10)
        self.count.setSpecialValueText("endlos")
        bar.addWidget(self.count)
        self.interval = QComboBox()
        self.interval.addItems(["1 s", "0.5 s", "2 s", "5 s"])
        if not IS_WIN:            # Windows-ping kennt kein Intervall
            bar.addWidget(QLabel("Intervall:"))
            bar.addWidget(self.interval)

    def command(self, host):
        if IS_WIN:
            return ["ping", "-t", host] if not self.count.value() else ["ping", "-n", str(self.count.value()), host]
        cmd = ["ping", "-i", self.interval.currentText().split()[0]]
        if self.count.value():
            cmd += ["-c", str(self.count.value())]
        return cmd + [host]


class LookupPage(CommandPage):
    def add_options(self, bar):
        bar.addWidget(QLabel("Typ:"))
        self.rtype = QComboBox()
        self.rtype.addItems(["A", "AAAA", "MX", "TXT", "NS", "CNAME", "SOA", "PTR (Rückwärts)", "ANY"])
        bar.addWidget(self.rtype)
        bar.addWidget(QLabel("DNS-Server:"))
        self.server = QLineEdit()
        self.server.setPlaceholderText("Standard")
        self.server.setMaximumWidth(140)
        bar.addWidget(self.server)

    def command(self, host):
        t = self.rtype.currentText().split()[0]
        if shutil.which("dig"):
            cmd = ["dig"]
            if self.server.text().strip():
                cmd.append("@" + self.server.text().strip())
            return cmd + (["-x", host] if t == "PTR" else [host, t])
        cmd = ["nslookup", f"-type={t}", host]
        if self.server.text().strip():
            cmd.append(self.server.text().strip())
        return cmd


class TraceroutePage(CommandPage):
    def add_options(self, bar):
        self.no_dns = QCheckBox("ohne Namensauflösung (schneller)")
        bar.addWidget(self.no_dns)

    def command(self, host):
        if IS_WIN:
            return ["tracert", "-w", "2000"] + (["-d"] if self.no_dns.isChecked() else []) + [host]
        cmd = ["traceroute", "-w", "2", "-q", "1"]
        if self.no_dns.isChecked():
            cmd.append("-n")
        return cmd + [host]


class WhoisPage(CommandPage):
    host_hint = "Domain oder IP, z. B. beispiel.de"

    def command(self, host):
        return ["whois", host]

    def python_job(self, host):
        if shutil.which("whois"):
            return None               # Mac/Linux: Systemprogramm
        from . import whois           # Windows: eingebauter Whois-Client
        return lambda emit: whois.lookup(host, emit)


class NetstatPage(CommandPage):
    needs_host = False

    def add_options(self, bar):
        bar.addWidget(QLabel("Anzeige:"))
        self.mode = QComboBox()
        self.mode.addItems(["Routing-Tabelle", "Aktive TCP-Verbindungen", "Lauschende Ports",
                            "Statistik je Protokoll", "Schnittstellen-Statistik"])
        bar.addWidget(self.mode)

    def command(self, _):
        i = self.mode.currentIndex()
        if IS_WIN:
            ps = ["powershell", "-NoProfile", "-Command"]
            return [["route", "print", "-4"], ["netstat", "-an", "-p", "TCP"],
                    ps + ["Get-NetTCPConnection -State Listen | Sort-Object LocalPort | "
                          "Select-Object LocalAddress, LocalPort, OwningProcess, "
                          "@{n='Programm';e={(Get-Process -Id $_.OwningProcess -EA SilentlyContinue).ProcessName}} | "
                          "Format-Table -AutoSize | Out-String -Width 200"],
                    ["netstat", "-s"], ["netstat", "-e"]][i]
        if i == 0:
            return ["netstat", "-rn"]
        if i == 1:
            return ["netstat", "-an", "-p", "tcp"] if IS_MAC else ["netstat", "-ant"]
        if i == 2:
            return ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"] if IS_MAC else ["netstat", "-ltn"]
        if i == 3:
            return ["netstat", "-s"]
        return ["netstat", "-i"]

    def start_label(self):
        return "Anzeigen"


class InfoPage(CommandPage):
    """Netzwerk-Übersicht: eigene Adressen, Router, DNS, Schnittstellen."""
    needs_host = False

    def add_options(self, bar):
        bar.addWidget(QLabel("Übersicht über Netzwerkschnittstellen, Router und DNS-Server dieses Rechners."))

    def start_label(self):
        return "Aktualisieren"

    @staticmethod
    def _networks_text() -> str:
        from . import net
        lines = []
        try:
            vs = net.vpn_summary()
            lines += ["== VPN ==", ("Verbunden: " + vs) if vs else "Kein VPN verbunden", ""]
        except Exception:  # noqa: BLE001
            pass
        lines.append("== Netze dieses Rechners ==")
        try:
            ifs = net.interfaces()
            for i in ifs:
                kind = "VPN " if i["vpn"] and i["label"] != "VPN" else ""
                lines.append(f"{kind}{i['label']:<24} {i['name']:<8} {i['ip']:<16} Netz {i['network']}")
            routes = net.vpn_routes()
            if routes:
                lines.append("")
                lines.append("Über VPN erreichbare Netze:")
                lines += [f"  {r['network']}  (über {r['iface']})" for r in routes]
            if not any(i["vpn"] for i in ifs):
                lines.append("(kein VPN aktiv)")
        except Exception as e:  # noqa: BLE001
            lines.append(f"(nicht ermittelbar: {e})")
        return "\n".join(lines) + "\n"

    def python_job(self, _):
        if not IS_WIN:
            return None
        head = self._networks_text()

        def job(emit):
            from .osinfo import run
            emit(head + "\n")
            ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
                  "'== Rechnername =='; $env:COMPUTERNAME; ''; "
                  "'== Netzwerkkonfiguration =='; Get-NetIPConfiguration | Format-List InterfaceAlias, "
                  "InterfaceDescription, IPv4Address, IPv4DefaultGateway, DNSServer | Out-String -Width 200; "
                  "'== WLAN =='; netsh wlan show interfaces | Out-String; "
                  "'== Öffentliche IP =='; try { (Invoke-RestMethod -Uri https://api.ipify.org -TimeoutSec 4) } "
                  "catch { '(nicht ermittelbar)' }; ''; "
                  "'== Adapter =='; Get-NetAdapter | Format-Table Name, InterfaceDescription, Status, "
                  "LinkSpeed, MacAddress -AutoSize | Out-String -Width 200")
            emit(run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps], timeout=40,
                     encoding="utf-8"))
        return job

    def command(self, _):
        import shlex
        head = f"printf '%s\\n' {shlex.quote(self._networks_text())}; "
        if IS_MAC:
            script = head + (
                'echo "== Hostname =="; scutil --get ComputerName; hostname; echo;'
                'echo "== Standard-Router =="; route -n get default 2>/dev/null | grep -E "gateway|interface"; echo;'
                'echo "== DNS-Server =="; scutil --dns | grep "nameserver\\[" | sort -u; echo;'
                'echo "== WLAN =="; networksetup -getairportnetwork en0 2>/dev/null; echo;'
                'echo "== Hardware-Anschlüsse =="; networksetup -listallhardwareports; echo;'
                'echo "== Öffentliche IP =="; curl -s --max-time 4 https://api.ipify.org || echo "(nicht ermittelbar)"; echo; echo;'
                'echo "== Schnittstellen (ifconfig) =="; ifconfig'
            )
        else:
            script = head + ('echo "== Hostname =="; hostname; echo; echo "== Router =="; ip route | grep default; echo;'
                      'echo "== DNS =="; grep nameserver /etc/resolv.conf; echo; echo "== Schnittstellen =="; ip addr')
        return ["/bin/sh", "-c", script]

    def showEvent(self, e):
        super().showEvent(e)
        if not self.out.toPlainText():
            self.toggle()


class SpeedtestPage(CommandPage):
    """Internet-Geschwindigkeit mit Apples networkQuality (macOS 12+).
    Ergebnisse werden mit Zeit und WLAN-/Netzname im Verlauf gespeichert."""
    needs_host = False
    MAX_HISTORY = 50

    def __init__(self, parent=None):
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QFrame, QGridLayout, QHeaderView, QTableWidget
        super().__init__(parent)
        self.settings = QSettings("Peter Bode", "Netzentrale")
        lay = self.layout()

        tiles = QGridLayout()
        self.tiles = {}
        for i, (key, label) in enumerate((("down", "Download"), ("up", "Upload"),
                                          ("lat", "Latenz (Ruhe)"), ("resp", "Reaktionsfähigkeit"))):
            fr = QFrame()
            fr.setStyleSheet("QFrame{border:1px solid palette(mid);border-radius:8px;}")
            v = QVBoxLayout(fr)
            t = QLabel(label)
            t.setStyleSheet("border:none;color:gray;")
            val = QLabel("–")
            val.setStyleSheet("border:none;font-size:22px;font-weight:600;")
            v.addWidget(t)
            v.addWidget(val)
            tiles.addWidget(fr, 0, i)
            self.tiles[key] = val
        lay.insertLayout(1, tiles)

        self.out.setMaximumHeight(130)
        lay.addWidget(QLabel("Verlauf:"))
        self.hist = QTableWidget(0, 6)
        self.hist.setHorizontalHeaderLabels(["Zeitpunkt", "Netz", "Download", "Upload", "Latenz", "Reaktion"])
        self.hist.setEditTriggers(QTableWidget.NoEditTriggers)
        self.hist.verticalHeader().setVisible(False)
        self.hist.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.hist.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        lay.addWidget(self.hist, 1)
        self._show_history()

    def add_options(self, bar):
        bar.addWidget(QLabel("Misst die Internetverbindung über Apples Messserver (ca. 20 s)." if IS_MAC else
                             "Misst die Internetverbindung über speed.cloudflare.com (ca. 20 s)."))
        self.sequential = QCheckBox("Download und Upload nacheinander messen")
        bar.addWidget(self.sequential)
        self.sequential.setVisible(IS_MAC)

    def start_label(self):
        return "Test starten"

    def command(self, _):
        return ["networkQuality", "-s"] if self.sequential.isChecked() else ["networkQuality"]

    def python_job(self, _):
        if IS_MAC and shutil.which("networkQuality"):
            return None
        from . import speedtest
        return lambda emit: speedtest.run(emit)

    def toggle(self):
        if not self.running():
            for v in self.tiles.values():
                v.setText("…")
        super().toggle()

    def _finished(self, code, status):
        super()._finished(code, status)
        import re
        txt = self.out.toPlainText()

        def num(pattern):
            m = re.search(pattern, txt, re.I)
            return float(m.group(1)) if m else None

        down = num(r"Downlink capacity:\s*([\d.]+)") or num(r"Download:\s*([\d.]+)")
        up = num(r"Uplink capacity:\s*([\d.]+)") or num(r"Upload:\s*([\d.]+)")
        lat = num(r"Idle Latency:\s*([\d.]+)") or num(r"Ping:\s*([\d.]+)")
        m = re.search(r"Responsiveness:\s*(\w+)", txt)
        resp = {"High": "hoch", "Medium": "mittel", "Low": "niedrig"}.get(m.group(1), m.group(1)) if m else None
        if down is None and up is None:
            for v in self.tiles.values():
                v.setText("–")
            return
        res = {"down": down, "up": up, "lat": lat, "resp": resp}
        self._show_tiles(res)
        self._save(res)

    def _show_tiles(self, r):
        self.tiles["down"].setText(f"{r['down']:.0f} Mbit/s" if r.get("down") is not None else "–")
        self.tiles["up"].setText(f"{r['up']:.0f} Mbit/s" if r.get("up") is not None else "–")
        self.tiles["lat"].setText(f"{r['lat']:.0f} ms" if r.get("lat") is not None else "–")
        self.tiles["resp"].setText(r.get("resp") or "–")

    @staticmethod
    def _network_name() -> str:
        import subprocess
        if IS_MAC:
            for dev in ("en0", "en1"):
                try:
                    out = subprocess.run(["networksetup", "-getairportnetwork", dev],
                                         capture_output=True, text=True, timeout=3).stdout
                except Exception:  # noqa: BLE001
                    continue
                if ":" in out and "not associated" not in out.lower():
                    return out.split(":", 1)[1].strip()
            return "Kabel / unbekannt"
        if IS_WIN:
            import re
            from .osinfo import run
            m = re.search(r"^\s*SSID\s*:\s*(.+)$", run(["netsh", "wlan", "show", "interfaces"]), re.M)
            return m.group(1).strip() if m else "Kabel / unbekannt"
        return ""

    def _history(self) -> list[dict]:
        import json
        try:
            return json.loads(self.settings.value("speedtest/history", "[]"))
        except (TypeError, ValueError):
            return []

    def _save(self, r):
        import json
        from datetime import datetime
        r = dict(r, ts=datetime.now().strftime("%d.%m.%Y %H:%M"), net=self._network_name())
        hist = [r] + self._history()
        self.settings.setValue("speedtest/history", json.dumps(hist[: self.MAX_HISTORY]))
        self._show_history()

    def _show_history(self):
        from PySide6.QtWidgets import QTableWidgetItem
        hist = self._history()
        self.hist.setRowCount(len(hist))
        for row, r in enumerate(hist):
            vals = [r.get("ts", ""), r.get("net", ""),
                    f"{r['down']:.0f} Mbit/s" if r.get("down") is not None else "–",
                    f"{r['up']:.0f} Mbit/s" if r.get("up") is not None else "–",
                    f"{r['lat']:.0f} ms" if r.get("lat") is not None else "–", r.get("resp") or "–"]
            for c, v in enumerate(vals):
                self.hist.setItem(row, c, QTableWidgetItem(v))
        if hist:
            self._show_tiles(hist[0])
