"""Netzentrale – Verbindungsmanager und Netzwerk-Werkzeugkasten."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QStackedWidget, QStatusBar, QWidget,
)

from . import APP_NAME, __version__, launch
from .storage import DeviceStore
from .ui_dali import DaliPage
from .ui_devices import DevicesPage
from .ui_extra import BonjourPage, SubnetPage, WifiPage, WolPage
from .ui_inventory import InventoryPage
from .ui_monitor import MonitorPage, Target
from .ui_proto import ModbusPage, MqttPage, ThroughputPage
from .ui_scan import PortScanPage, ScanPage
from .ui_security import SecurityPage
from .ui_support import SupportPage
from .ui_tools import (InfoPage, LookupPage, NetstatPage, PingPage, SpeedtestPage, TraceroutePage,
                       WhoisPage)

STYLE = """
QListWidget#nav { border: none; background: palette(window); font-size: 13px; padding-top: 6px; }
QListWidget#nav::item { padding: 4px 12px; border-radius: 6px; margin: 1px 6px; }
QListWidget#nav::item:selected { background: #1f3f7a; color: white; }
QTreeWidget, QTableWidget { font-size: 13px; }
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.settings = QSettings("Peter Bode", APP_NAME)
        self.resize(self.settings.value("size", QSize(1180, 780)))
        self.store = DeviceStore()

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(170)
        self.stack = QStackedWidget()

        self.devices = DevicesPage(self.store)
        self.support = SupportPage()
        self.scan = ScanPage()
        self.portscan = PortScanPage()
        self.ping = PingPage()
        self.lookup = LookupPage()
        self.trace = TraceroutePage()
        self.whois = WhoisPage()
        self.netstat = NetstatPage()
        self.info = InfoPage()
        self.speed = SpeedtestPage()
        self.monitor = MonitorPage(self.store)
        self.inventory = InventoryPage(self.scan)
        self.bonjour = BonjourPage()
        self.wifi = WifiPage()
        self.wol = WolPage(self.store)
        self.subnet = SubnetPage()
        self.lan = ThroughputPage()
        self.modbus = ModbusPage()
        self.mqtt = MqttPage()
        self.dali = DaliPage(self.store)
        self.security = SecurityPage()

        self._sections = []
        self._add_header("Fernzugriff")
        self._add("Verbindungen", self.devices)
        self._add("Fernhilfe", self.support)
        self._add_header("Überwachung")
        self._add("Überwachung", self.monitor)
        self._add("Inventar", self.inventory)
        self._add_header("Netzwerk")
        self._add("Netzwerkscan", self.scan)
        self._add("Bonjour-Suche", self.bonjour)
        self._add("Portscan", self.portscan)
        self._add("Sicherheits-Check", self.security)
        self._add("WLAN", self.wifi)
        self._add("Speedtest", self.speed)
        self._add("LAN-Durchsatz", self.lan)
        self._add("Ping", self.ping)
        self._add("Traceroute", self.trace)
        self._add("Lookup (DNS)", self.lookup)
        self._add("Whois", self.whois)
        self._add("Netstat", self.netstat)
        self._add("Info", self.info)
        self._add_header("Werkzeuge")
        self._add("DALI-Licht", self.dali)
        self._add("MQTT", self.mqtt)
        self._add("Modbus TCP", self.modbus)
        self._add("Wake-on-LAN", self.wol)
        self._add("Subnetz-Rechner", self.subnet)
        self.nav.currentRowChanged.connect(self._nav_changed)
        self.nav.setFixedWidth(180)

        central = QWidget()
        h = QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self.nav)
        h.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())

        # Querverbindungen zwischen den Seiten
        for page in (self.devices, self.scan, self.portscan, self.support, self.monitor, self.inventory,
                     self.bonjour, self.wol, self.lan, self.modbus, self.mqtt, self.dali, self.security):
            page.message.connect(lambda m: self.statusBar().showMessage(m, 6000))
        self.devices.request_portscan.connect(self.goto_portscan)
        self.devices.request_ping.connect(self.goto_ping)
        self.scan.request_portscan.connect(self.goto_portscan)
        self.scan.add_device.connect(self.add_device)
        self.portscan.add_device.connect(self.add_device)
        self.support.add_device.connect(self.add_device)
        self.bonjour.add_device.connect(self.add_device)
        self.devices.request_monitor.connect(self.monitor_device)
        self.devices.request_dali.connect(self.goto_dali)
        self.devices.request_security.connect(lambda h: self.goto_security(h, True))
        self.portscan.request_security.connect(lambda h: self.goto_security(h, False))

        self._menus()
        self.nav.setCurrentRow(1)
        self._check_clients()

    # -- Navigation --
    def _add_header(self, text):
        it = QListWidgetItem(text.upper())
        it.setFlags(Qt.NoItemFlags)
        f = it.font()
        f.setPointSize(max(9, f.pointSize() - 2))
        f.setBold(True)
        it.setFont(f)
        self.nav.addItem(it)
        self._sections.append(None)

    def _add(self, text, page):
        self.nav.addItem(QListWidgetItem(text))
        self.stack.addWidget(page)
        self._sections.append(page)

    def _nav_changed(self, row):
        page = self._sections[row] if 0 <= row < len(self._sections) else None
        if page is not None:
            self.stack.setCurrentWidget(page)

    def show_page(self, page):
        self.nav.setCurrentRow(self._sections.index(page))

    def goto_portscan(self, host):
        self.show_page(self.portscan)
        self.portscan.start_for(host)

    def goto_ping(self, host):
        self.show_page(self.ping)
        self.ping.run_for(host)

    def goto_security(self, host, own_device=False):
        self.show_page(self.security)
        self.security.run_for(host, own_device)

    def goto_dali(self, host):
        self.show_page(self.dali)
        self.dali.open_for(host)

    def monitor_device(self, d):
        self.show_page(self.monitor)
        if d.web and d.web.get("url"):
            t = Target(name=d.name, kind="web", target=d.web["url"])
        elif d.dali:
            t = Target(name=d.name, kind="web", target=f"http://{d.host}/info")
        else:
            t = Target(name=d.name, kind="ping", target=d.host)
        self.monitor.add(t)

    def add_device(self, dev):
        self.show_page(self.devices)
        self.devices.add(dev)

    # -- Menüs --
    def _menus(self):
        m = self.menuBar().addMenu("Ablage")
        a = QAction("Neues Gerät …", self, shortcut=QKeySequence.New)
        a.triggered.connect(lambda: (self.show_page(self.devices), self.devices.add()))
        m.addAction(a)
        m.addSeparator()
        a = QAction("Geräteliste exportieren …", self)
        a.triggered.connect(self._export)
        m.addAction(a)
        a = QAction("Geräteliste importieren …", self)
        a.triggered.connect(self._import)
        m.addAction(a)
        m.addSeparator()
        a = QAction("Aus RealVNC importieren (Dateien/ZIP) …", self)
        a.triggered.connect(lambda: self._import_realvnc(folder=False))
        m.addAction(a)
        a = QAction("Aus RealVNC importieren (Ordner) …", self)
        a.triggered.connect(lambda: self._import_realvnc(folder=True))
        m.addAction(a)
        a = QAction("Aus CSV/Excel-Liste importieren …", self)
        a.triggered.connect(self._import_csv)
        m.addAction(a)
        h = self.menuBar().addMenu("Hilfe")
        a = QAction(f"Über {APP_NAME}", self)
        a.triggered.connect(lambda: QMessageBox.about(
            self, APP_NAME, f"<b>{APP_NAME}</b> {__version__}<br>Fernzugriff und Netzwerk-Werkzeuge."))
        h.addAction(a)

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Geräteliste exportieren", "geraete.json", "JSON (*.json)")
        if path:
            self.store.export_to(path)
            self.statusBar().showMessage(f"Exportiert: {path}", 5000)

    def _ask_client(self) -> str | None:
        from PySide6.QtWidgets import QInputDialog
        from .storage import VNC_CLIENTS
        labels = list(VNC_CLIENTS.values())
        label, ok = QInputDialog.getItem(self, "VNC-Client", "Mit welchem Client sollen die importierten "
                                         "Geräte geöffnet werden?\n(Lässt sich später pro Gerät ändern.)",
                                         labels, 0, False)
        return next((k for k, v in VNC_CLIENTS.items() if v == label), None) if ok else None

    def _finish_import(self, devices, source):
        from .importers import merge
        if not devices:
            QMessageBox.information(self, "Import", f"In {source} wurden keine Verbindungen gefunden.")
            return
        added, skipped = merge(self.store, devices)
        self.devices.rebuild()
        self.devices.check_status()
        self.show_page(self.devices)
        QMessageBox.information(self, "Import", f"{added} Geräte übernommen"
                                + (f", {skipped} schon vorhanden (übersprungen)." if skipped else "."))

    def _import_realvnc(self, folder: bool):
        from .importers import import_realvnc
        if folder:
            d = QFileDialog.getExistingDirectory(self, "Ordner mit exportierten RealVNC-Verbindungen wählen")
            paths = [d] if d else []
        else:
            paths, _ = QFileDialog.getOpenFileNames(self, "RealVNC-Verbindungen wählen", "",
                                                    "RealVNC (*.vnc *.zip)")
        if not paths:
            return
        client = self._ask_client()
        if not client:
            return
        try:
            devs = import_realvnc(paths, client)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Import fehlgeschlagen", str(e))
            return
        self._finish_import(devs, "der Auswahl")

    def _import_csv(self):
        from .importers import import_csv
        path, _ = QFileDialog.getOpenFileName(self, "CSV-Liste wählen (Name;Adresse;Gruppe;Port)", "",
                                              "CSV (*.csv *.txt)")
        if not path:
            return
        client = self._ask_client()
        if not client:
            return
        try:
            devs = import_csv(path, client)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Import fehlgeschlagen", str(e))
            return
        self._finish_import(devs, "der CSV-Datei")

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Geräteliste importieren", "", "JSON (*.json)")
        if path:
            try:
                n = self.store.import_from(path)
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "Import fehlgeschlagen", str(e))
                return
            self.devices.rebuild()
            self.devices.check_status()
            self.statusBar().showMessage(f"{n} Geräte importiert", 5000)

    def _check_clients(self):
        if (launch.IS_MAC or launch.IS_WIN) and not launch.tigervnc_installed():
            self.statusBar().showMessage(
                "Hinweis: TigerVNC nicht gefunden – für VNC bitte installieren (tigervnc.org) "
                "oder pro Gerät einen anderen Client wählen.", 15000)

    def shutdown(self):
        """Laufende Hintergrund-Threads beenden (Scans, Überwachung, MQTT …)."""
        from PySide6.QtCore import QThread
        if self.mqtt.client:
            self.mqtt.client.disconnect()
            self.mqtt.client.loop_stop()
        if self.lan.server_stop:
            self.lan.server_stop.set()
        threads = [t for t in self.findChildren(QThread) if t.isRunning()]
        for t in threads:
            if hasattr(t, "stop"):
                t.stop()
        for t in threads:
            t.wait(4000)

    def closeEvent(self, e):
        self.settings.setValue("size", self.size())
        self.shutdown()
        super().closeEvent(e)


def _setup_tray(app, icon: QIcon):
    """Windows/Linux: Symbol im Infobereich – darüber kommen die Meldungen der Überwachung."""
    from PySide6.QtWidgets import QSystemTrayIcon
    from . import net
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return None
    tray = QSystemTrayIcon(icon, app)
    tray.setToolTip(APP_NAME)
    tray.show()
    net.NOTIFIER = lambda title, text: tray.showMessage(title, text, QSystemTrayIcon.Information, 10000)
    return tray


def main():
    from . import osinfo
    osinfo.install_no_console_patch()
    if osinfo.IS_WIN:
        try:   # eigenes Symbol in der Taskleiste statt des Python-Symbols
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PeterBode.Netzentrale")
        except Exception:  # noqa: BLE001
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setStyleSheet(STYLE)
    icon = QIcon(str(Path(__file__).with_name("icon.png")))
    if not icon.isNull():
        app.setWindowIcon(icon)
    if not osinfo.IS_MAC:
        app._tray = _setup_tray(app, icon)   # Referenz halten
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
