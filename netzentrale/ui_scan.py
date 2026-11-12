"""Seiten „Netzwerkscan“ und „Portscan“."""
from __future__ import annotations

import ipaddress

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMenu, QMessageBox, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from . import net
from .storage import DEFAULT_VNC_CLIENT, Device
from .workers import PortScanWorker, ScanWorker


class NumItem(QTableWidgetItem):
    """Sortiert numerisch (IP-Adressen, Ports, Zeiten)."""
    def __init__(self, text: str, key):
        super().__init__(text)
        self.key = key

    def __lt__(self, other):
        return self.key < getattr(other, "key", 0)


def device_from_ports(ip: str, name: str, ports: list[int], dali: bool = False) -> Device:
    d = Device(name=(name or ip).split(".")[0] if name else ("DALI-Gateway " + ip if dali else ip), host=ip)
    if dali:
        d.dali = {"enabled": True}
        d.group = "DALI-Gateways"
    if 5900 in ports or 5901 in ports:
        d.vnc = {"port": 5900 if 5900 in ports else 5901, "client": DEFAULT_VNC_CLIENT}
    if 22 in ports:
        d.ssh = {"port": 22, "user": ""}
    for p, scheme in ((8123, "http"), (443, "https"), (80, "http"), (8080, "http"), (8443, "https")):
        if p in ports:
            default = (scheme == "http" and p == 80) or (scheme == "https" and p == 443)
            d.web = {"url": f"{scheme}://{ip}" + ("" if default else f":{p}")}
            break
    if 3389 in ports:
        d.rdp = {"port": 3389, "user": ""}
    return d


# ---------------------------------------------------------------------------
class ScanPage(QWidget):
    add_device = Signal(object)       # Device
    request_portscan = Signal(str)
    message = Signal(str)

    COLS = ["IP-Adresse", "Name", "MAC-Adresse", "Hersteller", "Offene Ports", "Antwort"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker: ScanWorker | None = None
        self.rows: dict[str, int] = {}
        self.data: dict[str, dict] = {}
        self._initial = True

        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Netz:"))
        # Auswahlliste aller erkannten Netze (WLAN/LAN, VPN, über VPN erreichbar) – frei editierbar
        self.net_choice = QComboBox()
        self.net_choice.setEditable(True)
        self.net_choice.setInsertPolicy(QComboBox.NoInsert)
        self.net_choice.setMinimumWidth(420)
        self.target = self.net_choice.lineEdit()   # Text = zu scannender Bereich
        self.target.setPlaceholderText("z. B. 192.168.1.0/24 oder 192.168.1.10-80")
        self.target.returnPressed.connect(self.toggle)
        self.net_choice.activated.connect(self._choice_selected)
        bar.addWidget(self.net_choice, 1)
        b = QPushButton("↻")
        b.setToolTip("Netze neu erkennen (z. B. nach VPN-Verbindung)")
        b.setFixedWidth(36)
        b.clicked.connect(self.reload_networks)
        bar.addWidget(b)
        self.names = QCheckBox("Namen auflösen")
        self.names.setChecked(True)
        bar.addWidget(self.names)
        self.btn = QPushButton("Scan starten")
        self.btn.clicked.connect(self.toggle)
        bar.addWidget(self.btn)
        lay.addLayout(bar)

        self.vpn_banner = QLabel("")
        self.vpn_banner.setWordWrap(True)
        self.vpn_banner.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.vpn_banner)
        self.nets_info = QLabel("")
        self.nets_info.setWordWrap(True)
        self.nets_info.setStyleSheet("color: gray; font-size: 11px;")
        lay.addWidget(self.nets_info)

        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setFormat("%v / %m Adressen")
        lay.addWidget(self.progress)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self.table.itemDoubleClicked.connect(lambda _: self._add_selected())
        lay.addWidget(self.table, 1)

        act = QHBoxLayout()
        b1 = QPushButton("Als Gerät übernehmen")
        b1.clicked.connect(self._add_selected)
        b2 = QPushButton("Portscan")
        b2.clicked.connect(lambda: (ip := self._sel_ip()) and self.request_portscan.emit(ip))
        act.addWidget(b1)
        act.addWidget(b2)
        act.addStretch(1)
        self.summary = QLabel("")
        act.addWidget(self.summary)
        lay.addLayout(act)

    def reload_networks(self):
        """Erkennt alle Netze dieses Macs – lokales Netz bleibt auch bei aktivem VPN wählbar."""
        keep = self.target.text().strip()
        try:
            choices = net.scan_choices()
        except Exception:  # noqa: BLE001
            choices = []
        self.net_choice.blockSignals(True)
        self.net_choice.clear()
        for c in choices:
            self.net_choice.addItem(c["label"], c["cidr"])
        self.net_choice.blockSignals(False)
        if keep and not self._initial:
            self.target.setText(keep)
        elif choices:
            self.net_choice.setCurrentIndex(0)
            self.target.setText(choices[0]["cidr"])
        else:
            try:
                self.target.setText(str(net.local_network()))
            except Exception:  # noqa: BLE001
                pass
        self._initial = False
        local = [c for c in choices if not c["vpn"]]
        vpn = [c for c in choices if c["vpn"]]
        txt = f"Erkannt: {len(local)} lokale Netze"
        if vpn:
            txt += (f", {len(vpn)} Netze über VPN. Hinweis: Über VPN gibt es keine MAC-Adressen, "
                    "und manche VPNs blocken Ping – offene Ports werden trotzdem gefunden.")
        else:
            txt += ", kein VPN aktiv."
        self.nets_info.setText(txt)
        self._show_vpn()

    def _show_vpn(self):
        """Deutlich sichtbar: Ist ein VPN verbunden – und womit?"""
        try:
            vpns = net.vpn_info()
        except Exception:  # noqa: BLE001
            vpns = []
        if not vpns:
            local = next((i for i in net.interfaces() if not i["vpn"] and not i.get("virtual")), None)
            where = f" – nur lokales Netz {local['network']} ({local['label']})" if local else ""
            self.vpn_banner.setText(f"🔓 <b>Kein VPN verbunden</b>{where}")
            self.vpn_banner.setStyleSheet("padding:6px 10px; border-radius:6px; background:#eceff3; color:#333;")
            return
        rows = []
        for v in vpns:
            line = f"🔒 <b>VPN verbunden: {v['name']}</b>"
            if v["server"]:
                line += f" &nbsp;·&nbsp; Server <b>{v['server']}</b>"
            line += f" &nbsp;·&nbsp; eigene VPN-Adresse {v['ip']}"
            if v["peer"]:
                line += f" (Gegenstelle {v['peer']})"
            if v["networks"]:
                line += "<br>&nbsp;&nbsp;&nbsp;&nbsp;Über dieses VPN erreichbar: " + ", ".join(v["networks"])
            rows.append(line)
        self.vpn_banner.setText("<br>".join(rows))
        self.vpn_banner.setStyleSheet("padding:6px 10px; border-radius:6px; background:#e3f1e6; color:#1d4d2a;")

    def _choice_selected(self, idx: int):
        cidr = self.net_choice.itemData(idx)
        if not cidr:
            return
        n = ipaddress.ip_network(cidr, strict=False)
        if n.num_addresses > 4096:
            # zu groß für einen Scan → mit dem ersten /24 beginnen, Benutzer passt das 3. Oktett an
            first = next(n.subnets(new_prefix=24))
            self.target.setText(str(first))
            self.nets_info.setText(f"{cidr} ist zu groß für einen Scan ({n.num_addresses:,} Adressen). "
                                   f"Vorgeschlagen: {first} – bei Bedarf das dritte Zahlenfeld anpassen "
                                   "(z. B. 10.20.5.0/24) oder bis zu /20 eingeben.".replace(",", "."))
        else:
            self.target.setText(cidr)

    def showEvent(self, e):
        super().showEvent(e)
        if self._initial:
            self.reload_networks()

    def toggle(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.btn.setText("Wird gestoppt …")
            self.btn.setEnabled(False)
            return
        try:
            targets = net.parse_targets(self.target.text())
        except ValueError as e:
            QMessageBox.warning(self, "Ungültiger Bereich", str(e))
            return
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.rows.clear()
        self.data.clear()
        self.progress.setRange(0, len(targets))
        self.progress.setValue(0)
        self.summary.setText("")
        self.worker = ScanWorker(targets, self.names.isChecked(), self)
        self.worker.found.connect(self._on_found)
        self.worker.progress.connect(lambda n, t: self.progress.setValue(n))
        self.worker.done.connect(self._on_done)
        self.worker.start()
        self.btn.setText("Stopp")
        self.message.emit(f"Scanne {len(targets)} Adressen …")

    def _on_found(self, r: dict):
        self.table.setSortingEnabled(False)
        ip = r["ip"]
        self.data[ip] = r
        if ip not in self.rows:
            self.rows[ip] = self.table.rowCount()
            self.table.insertRow(self.table.rowCount())
        row = self.rows[ip]
        ports = ", ".join(f"{p} {net.port_name(p)}".strip() for p in r["ports"])
        ms = r.get("ms")
        vals = [
            NumItem(ip, int(ipaddress.ip_address(ip))),
            QTableWidgetItem(r.get("name", "")),
            QTableWidgetItem(r.get("mac", "")),
            QTableWidgetItem(r.get("vendor", "")),
            QTableWidgetItem(ports),
            NumItem(f"{ms:.0f} ms" if ms is not None else "nur ARP", ms if ms is not None else 1e9),
        ]
        for c, it in enumerate(vals):
            self.table.setItem(row, c, it)

    def _on_done(self, n: int):
        self.table.setSortingEnabled(True)
        self.table.sortItems(0)
        # Zeilenindex nach Sortierung neu aufbauen
        self.rows = {self.table.item(r, 0).text(): r for r in range(self.table.rowCount())}
        self.progress.setValue(self.progress.maximum())
        self.btn.setText("Scan starten")
        self.btn.setEnabled(True)
        self.summary.setText(f"{n} Geräte gefunden")
        self.message.emit(f"Netzwerkscan fertig: {n} Geräte")

    def _sel_ip(self) -> str | None:
        r = self.table.currentRow()
        return self.table.item(r, 0).text() if r >= 0 and self.table.item(r, 0) else None

    def _add_selected(self):
        ip = self._sel_ip()
        if ip:
            r = self.data.get(ip, {})
            self.add_device.emit(device_from_ports(ip, r.get("name", ""), r.get("ports", []), r.get("dali", False)))

    def _menu(self, pos):
        ip = self._sel_ip()
        if not ip:
            return
        m = QMenu(self)
        m.addAction("Als Gerät übernehmen …", self._add_selected)
        m.addAction("Portscan", lambda: self.request_portscan.emit(ip))
        m.exec(self.table.viewport().mapToGlobal(pos))


# ---------------------------------------------------------------------------
class PortScanPage(QWidget):
    message = Signal(str)
    add_device = Signal(object)
    request_security = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker: PortScanWorker | None = None
        self.open_ports: list[int] = []
        lay = QVBoxLayout(self)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Host:"))
        self.host = QLineEdit()
        self.host.setPlaceholderText("IP, Name oder URL – z. B. 192.168.1.20, pi.local, https://www.bsb.de")
        self.host.returnPressed.connect(self.toggle)
        bar.addWidget(self.host, 2)
        bar.addWidget(QLabel("Ports:"))
        self.ports = QComboBox()
        self.ports.setEditable(True)
        self.ports.addItems(["häufige Ports", "Web: 80,443,8080,8443", "1-1024", "1-65535",
                             "22,80,443,3389,5900,8123"])
        self.ports.setMinimumWidth(170)
        bar.addWidget(self.ports, 1)
        bar.addWidget(QLabel("Timeout:"))
        self.timeout = QDoubleSpinBox()
        self.timeout.setRange(0.1, 5)
        self.timeout.setSingleStep(0.1)
        self.timeout.setValue(0.6)
        self.timeout.setSuffix(" s")
        bar.addWidget(self.timeout)
        self.btn = QPushButton("Scan starten")
        self.btn.clicked.connect(self.toggle)
        bar.addWidget(self.btn)
        lay.addLayout(bar)

        self.resolved = QLabel("")
        self.resolved.setTextInteractionFlags(Qt.TextSelectableByMouse)
        f = self.resolved.font()
        f.setBold(True)
        self.resolved.setFont(f)
        lay.addWidget(self.resolved)

        self.progress = QProgressBar()
        self.progress.setFormat("%v / %m Ports")
        lay.addWidget(self.progress)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Port", "Dienst", "Antwort"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        lay.addWidget(self.table, 1)

        act = QHBoxLayout()
        b = QPushButton("Als Gerät übernehmen")
        b.clicked.connect(self._add)
        act.addWidget(b)
        b = QPushButton("Sicherheits-Check …")
        b.clicked.connect(lambda: self.host.text().strip() and self.request_security.emit(self.host.text().strip()))
        act.addWidget(b)
        act.addStretch(1)
        self.summary = QLabel("")
        act.addWidget(self.summary)
        lay.addLayout(act)

    def start_for(self, host: str):
        self.host.setText(host)
        if not (self.worker and self.worker.isRunning()):
            self.toggle()

    def toggle(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.btn.setEnabled(False)
            return
        host, url_port = net.host_from_input(self.host.text())
        if not host:
            return
        try:
            spec = self.ports.currentText()
            spec = spec.split(":", 1)[1] if spec.lower().startswith("web:") else spec
            ports = net.parse_ports(spec)
            if url_port and url_port not in ports:
                ports.append(url_port)   # Port aus der URL immer mitprüfen
        except ValueError:
            QMessageBox.warning(self, "Ungültige Ports", "Beispiel: 22,80,443 oder 1-1024")
            return
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.open_ports = []
        self.progress.setRange(0, len(ports))
        self.progress.setValue(0)
        self.summary.setText("")
        self.resolved.setText(f"löse {host} auf …")
        self.scanned_ip = ""
        self.worker = PortScanWorker(host, ports, self.timeout.value(), self)
        self.worker.resolved.connect(self._on_resolved)
        self.worker.open_port.connect(self._on_open)
        self.worker.progress.connect(lambda n, t: self.progress.setValue(n))
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()
        self.btn.setText("Stopp")
        self.message.emit(f"Portscan {host}: {len(ports)} Ports …")

    def _on_resolved(self, ip: str, info: str):
        self.scanned_ip = ip
        self.resolved.setText(info)

    def _on_open(self, port: int, ms: float):
        self.open_ports.append(port)
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, NumItem(str(port), port))
        self.table.setItem(r, 1, QTableWidgetItem(net.port_name(port)))
        self.table.setItem(r, 2, NumItem(f"{ms:.0f} ms", ms))

    def _finish(self):
        self.btn.setText("Scan starten")
        self.btn.setEnabled(True)

    def _on_done(self, n: int, secs: float):
        self._finish()
        self.table.setSortingEnabled(True)
        self.table.sortItems(0)
        self.progress.setValue(self.progress.maximum())
        self.summary.setText(f"{n} offene Ports · {secs:.1f} s")
        self.message.emit(f"Portscan fertig: {n} offene Ports")

    def _on_failed(self, msg: str):
        self._finish()
        QMessageBox.warning(self, "Portscan", msg)

    def _add(self):
        host, _ = net.host_from_input(self.host.text())
        if host:
            self.add_device.emit(device_from_ports(host, "", self.open_ports))
