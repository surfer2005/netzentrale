"""Werkzeuge: LAN-Durchsatz, Modbus TCP, MQTT-Tester."""
from __future__ import annotations

import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from PySide6.QtCore import QObject, QProcess, QThread, Qt, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from . import modbus, net, throughput


def _mono(w):
    w.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
    return w


# ===========================================================================
# LAN-Durchsatz
# ===========================================================================
class ThroughputWorker(QThread):
    progress = Signal(str, float)
    result = Signal(str, float)
    failed = Signal(str)

    def __init__(self, host, secs, parent=None):
        super().__init__(parent)
        self.host, self.secs = host, secs

    def run(self):
        try:
            for mode, label in (("DOWN", "Download"), ("UP", "Upload")):
                r = throughput.run_test(self.host, mode, self.secs,
                                        progress=lambda v, lb=label: self.progress.emit(lb, v))
                self.result.emit(label, r)
        except OSError as e:
            self.failed.emit(f"{e} – läuft die Gegenstelle auf {self.host} (TCP {throughput.PORT})?")


class ThroughputPage(QWidget):
    message = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None
        self.server_stop: threading.Event | None = None
        self.proc: QProcess | None = None
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Misst die echte Geschwindigkeit zwischen zwei Geräten im eigenen Netz "
                             "(z. B. Schiff ↔ Land, WLAN-Brücke, Richtfunk) – ohne Internet."))

        srv = QGroupBox("Dieser Mac als Gegenstelle")
        h = QHBoxLayout(srv)
        self.btn_srv = QPushButton("Gegenstelle starten")
        self.btn_srv.clicked.connect(self.toggle_server)
        h.addWidget(self.btn_srv)
        self.srv_state = QLabel(f"Auf einem Pi stattdessen:  python3 throughput.py  (aus dem Netzentrale-Ordner, TCP {throughput.PORT})")
        self.srv_state.setWordWrap(True)
        h.addWidget(self.srv_state, 1)
        lay.addWidget(srv)

        cl = QGroupBox("Messung")
        f = QFormLayout(cl)
        self.host = QLineEdit()
        self.host.setPlaceholderText("Adresse der Gegenstelle")
        self.method = QComboBox()
        self.method.addItem("Netzentrale-Gegenstelle", "nz")
        self.method.addItem("iperf3-Server (iperf3 -s)", "iperf")
        self.secs = QSpinBox()
        self.secs.setRange(2, 60)
        self.secs.setValue(8)
        self.secs.setSuffix(" s je Richtung")
        self.btn = QPushButton("Messung starten")
        self.btn.clicked.connect(self.start)
        f.addRow("Gegenstelle:", self.host)
        f.addRow("Methode:", self.method)
        f.addRow("Dauer:", self.secs)
        f.addRow("", self.btn)
        lay.addWidget(cl)

        res = QHBoxLayout()
        self.lbl = {}
        for k in ("Download", "Upload"):
            box = QGroupBox(k)
            v = QVBoxLayout(box)
            lab = QLabel("–")
            lab.setStyleSheet("font-size: 22px; font-weight: 600;")
            v.addWidget(lab)
            res.addWidget(box)
            self.lbl[k] = lab
        lay.addLayout(res)
        self.out = _mono(QPlainTextEdit())
        self.out.setReadOnly(True)
        lay.addWidget(self.out, 1)

    def toggle_server(self):
        if self.server_stop:
            self.server_stop.set()
            self.server_stop = None
            self.btn_srv.setText("Gegenstelle starten")
            self.srv_state.setText("Gegenstelle gestoppt.")
            return
        self.server_stop = threading.Event()
        err = []

        def run():
            try:
                throughput.serve(throughput.PORT, self.server_stop)
            except OSError as e:
                err.append(str(e))
        threading.Thread(target=run, daemon=True).start()
        time.sleep(0.3)
        if err:
            self.srv_state.setText(f"Start fehlgeschlagen: {err[0]}")
            self.server_stop = None
            return
        self.btn_srv.setText("Gegenstelle stoppen")
        self.srv_state.setText(f"Läuft – auf dem anderen Gerät als Gegenstelle eintragen:  {net.local_ip()}")

    def start(self):
        host = self.host.text().strip()
        if not host or (self.worker and self.worker.isRunning()):
            return
        for v in self.lbl.values():
            v.setText("…")
        self.out.clear()
        if self.method.currentData() == "iperf":
            if not shutil.which("iperf3"):
                self.out.setPlainText("iperf3 ist auf diesem Mac nicht installiert (z. B. „brew install iperf3“).\n"
                                      "Alternative: Methode „Netzentrale-Gegenstelle“ – braucht nichts extra.")
                return
            self.proc = QProcess(self)
            self.proc.setProcessChannelMode(QProcess.MergedChannels)
            self.proc.readyReadStandardOutput.connect(
                lambda: self.out.appendPlainText(bytes(self.proc.readAllStandardOutput()).decode(errors="replace").rstrip()))
            self._iperf_phase = 0
            self.proc.finished.connect(self._iperf_next)
            self.proc.start("iperf3", ["-c", host, "-t", str(self.secs.value()), "-R"])
            return
        self.worker = ThroughputWorker(host, self.secs.value(), self)
        self.worker.progress.connect(lambda k, v: self.lbl[k].setText(f"{v:.0f} Mbit/s …"))
        self.worker.result.connect(self._result)
        self.worker.failed.connect(lambda m: (self.out.setPlainText(m), [v.setText("–") for v in self.lbl.values()]))
        self.worker.start()
        self.btn.setText("misst …")
        self.worker.finished.connect(lambda: self.btn.setText("Messung starten"))

    def _iperf_next(self):
        if self._iperf_phase == 0:
            self._iperf_phase = 1
            self.proc.start("iperf3", ["-c", self.host.text().strip(), "-t", str(self.secs.value())])
        else:
            import re
            txt = self.out.toPlainText()
            rates = re.findall(r"([\d.]+)\s+([MG])bits/sec\s+\d*\s*receiver", txt)
            for (v, unit), k in zip(rates, ("Download", "Upload")):
                self.lbl[k].setText(f"{float(v) * (1000 if unit == 'G' else 1):.0f} Mbit/s")

    def _result(self, k, v):
        self.lbl[k].setText(f"{v:.0f} Mbit/s")
        self.out.appendPlainText(f"{datetime.now():%H:%M:%S}  {k}: {v:.1f} Mbit/s")
        self.message.emit(f"LAN-Durchsatz {k}: {v:.0f} Mbit/s")


# ===========================================================================
# Modbus TCP
# ===========================================================================
class ModbusPage(QWidget):
    message = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        g = QGroupBox("Verbindung und Abfrage")
        f = QFormLayout(g)
        row = QHBoxLayout()
        self.host = QLineEdit()
        self.host.setPlaceholderText("IP der Steuerung / des Gateways")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(502)
        self.unit = QSpinBox()
        self.unit.setRange(0, 255)
        self.unit.setValue(1)
        row.addWidget(self.host, 1)
        row.addWidget(QLabel("Port"))
        row.addWidget(self.port)
        row.addWidget(QLabel("Unit-ID"))
        row.addWidget(self.unit)
        f.addRow("Gerät:", row)
        row = QHBoxLayout()
        self.fc = QComboBox()
        for k, v in modbus.FUNCTIONS.items():
            self.fc.addItem(v, k)
        self.addr = QSpinBox()
        self.addr.setRange(0, 65535)
        self.count = QSpinBox()
        self.count.setRange(1, 125)
        self.count.setValue(10)
        self.one_based = QCheckBox("Adressen ab 1 (40001-Schreibweise)")
        row.addWidget(self.fc)
        row.addWidget(QLabel("Startadresse"))
        row.addWidget(self.addr)
        row.addWidget(QLabel("Anzahl"))
        row.addWidget(self.count)
        row.addWidget(self.one_based)
        f.addRow("Lesen:", row)
        row = QHBoxLayout()
        b = QPushButton("Lesen")
        b.clicked.connect(self.read)
        row.addWidget(b)
        self.swap = QCheckBox("Float: Wortreihenfolge tauschen")
        self.swap.toggled.connect(self.read)
        row.addWidget(self.swap)
        row.addStretch(1)
        f.addRow("", row)
        lay.addWidget(g)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Adresse", "Dezimal", "Mit Vorzeichen", "Hex", "Binär", "Float32 (ab hier)"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        lay.addWidget(self.table, 1)

        w = QGroupBox("Schreiben (Vorsicht – verändert Werte in der Steuerung!)")
        h = QHBoxLayout(w)
        self.waddr = QSpinBox()
        self.waddr.setRange(0, 65535)
        self.wval = QSpinBox()
        self.wval.setRange(0, 65535)
        self.wkind = QComboBox()
        self.wkind.addItems(["Holding Register (FC6)", "Coil (FC5, 0/1)"])
        b = QPushButton("Schreiben …")
        b.clicked.connect(self.write)
        for x in (QLabel("Adresse"), self.waddr, QLabel("Wert"), self.wval, self.wkind, b):
            h.addWidget(x)
        h.addStretch(1)
        lay.addWidget(w)
        self.status = QLabel("")
        lay.addWidget(self.status)

    def _client(self):
        host = self.host.text().strip()
        if not host:
            raise ValueError("Bitte eine Adresse eingeben.")
        return modbus.ModbusClient(host, self.port.value(), self.unit.value())

    def _base(self, fc):
        if not self.one_based.isChecked():
            return 0
        return {3: 40001, 4: 30001, 1: 1, 2: 10001}[fc]

    def read(self):
        fc = self.fc.currentData()
        try:
            t = time.perf_counter()
            vals = self._client().read(fc, self.addr.value(), self.count.value())
            ms = (time.perf_counter() - t) * 1000
        except (ValueError, OSError, modbus.ModbusError) as e:
            self.status.setText(f"❌ {e}")
            return
        self.table.setRowCount(len(vals))
        base = self._base(fc)
        for i, v in enumerate(vals):
            a = self.addr.value() + i
            label = str(base + a) if base else str(a)
            if fc in (3, 4):
                signed = v - 65536 if v >= 32768 else v
                fl = modbus.as_float32(vals, i, self.swap.isChecked())
                row = [label, str(v), str(signed), f"0x{v:04X}", f"{v:016b}", f"{fl:.6g}" if fl is not None else ""]
            else:
                row = [label, str(v), "", "", "EIN" if v else "AUS", ""]
            for c, x in enumerate(row):
                it = QTableWidgetItem(x)
                it.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(i, c, it)
        self.status.setText(f"✅ {len(vals)} Werte gelesen in {ms:.0f} ms")

    def write(self):
        kind = self.wkind.currentIndex()
        a, v = self.waddr.value(), self.wval.value()
        what = f"Register {a} auf {v}" if kind == 0 else f"Coil {a} auf {'EIN' if v else 'AUS'}"
        if QMessageBox.warning(self, "Wirklich schreiben?",
                               f"{what} setzen?\n\nDas verändert die Steuerung sofort.",
                               QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
            return
        try:
            c = self._client()
            (c.write_register(a, v) if kind == 0 else c.write_coil(a, bool(v)))
            self.status.setText(f"✅ {what} geschrieben")
        except (ValueError, OSError, modbus.ModbusError) as e:
            self.status.setText(f"❌ {e}")


# ===========================================================================
# MQTT-Tester
# ===========================================================================
class _MqttBridge(QObject):
    connected = Signal(bool, str)
    disconnected = Signal(str)
    message = Signal(str, bytes, int, bool)


class BrokerScan(QThread):
    found = Signal(str, int)
    done = Signal(int)

    def run(self):
        try:
            hosts = [str(h) for h in net.local_network().hosts()]
        except Exception:  # noqa: BLE001
            hosts = []
        n = 0

        def probe(h):
            return [(h, p) for p in (1883, 8883) if net.tcp_check(h, p, 0.4) is not None]

        with ThreadPoolExecutor(max_workers=128) as ex:
            for f in as_completed([ex.submit(probe, h) for h in hosts]):
                for h, p in f.result():
                    n += 1
                    self.found.emit(h, p)
        self.done.emit(n)


class MqttPage(QWidget):
    message = Signal(str)
    MAX_ROWS = 2000

    def __init__(self, parent=None):
        super().__init__(parent)
        self.client = None
        self.bridge = _MqttBridge()
        self.bridge.connected.connect(self._on_connected)
        self.bridge.disconnected.connect(self._on_disconnected)
        self.bridge.message.connect(self._on_message)
        self.paused = False
        self.count = 0
        self._probe: tuple[str, float] | None = None
        self._scan = None

        lay = QVBoxLayout(self)
        g = QGroupBox("Broker")
        f = QFormLayout(g)
        row = QHBoxLayout()
        self.host = QComboBox()
        self.host.setEditable(True)
        self.host.lineEdit().setPlaceholderText("Adresse des Brokers")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(1883)
        self.tls = QCheckBox("TLS")
        self.tls.toggled.connect(lambda on: self.port.setValue(8883 if on else 1883))
        b = QPushButton("Broker im Netz suchen")
        b.clicked.connect(self.scan_brokers)
        self.btn_scan = b
        row.addWidget(self.host, 1)
        row.addWidget(QLabel("Port"))
        row.addWidget(self.port)
        row.addWidget(self.tls)
        row.addWidget(b)
        f.addRow("Adresse:", row)
        row = QHBoxLayout()
        self.user = QLineEdit()
        self.user.setPlaceholderText("optional")
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.Password)
        self.pw.setPlaceholderText("optional")
        self.cid = QLineEdit(f"netzentrale-{uuid.uuid4().hex[:6]}")
        row.addWidget(QLabel("Benutzer"))
        row.addWidget(self.user)
        row.addWidget(QLabel("Passwort"))
        row.addWidget(self.pw)
        row.addWidget(QLabel("Client-ID"))
        row.addWidget(self.cid)
        f.addRow("Anmeldung:", row)
        row = QHBoxLayout()
        self.btn_conn = QPushButton("Verbinden")
        self.btn_conn.clicked.connect(self.toggle_connect)
        self.btn_test = QPushButton("Funktionstest (senden + empfangen)")
        self.btn_test.clicked.connect(self.selftest)
        self.btn_test.setEnabled(False)
        self.state = QLabel("nicht verbunden")
        row.addWidget(self.btn_conn)
        row.addWidget(self.btn_test)
        row.addWidget(self.state, 1)
        f.addRow("", row)
        lay.addWidget(g)

        sub = QHBoxLayout()
        sub.addWidget(QLabel("Abonnieren:"))
        self.topic = QLineEdit("#")
        self.topic.setToolTip("# = alle Themen, + = eine Ebene, z. B. bordwaechter/+/status")
        sub.addWidget(self.topic, 1)
        b = QPushButton("Abonnieren")
        b.clicked.connect(self.subscribe)
        sub.addWidget(b)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Anzeige filtern …")
        sub.addWidget(self.filter)
        self.btn_pause = QPushButton("Pause")
        self.btn_pause.clicked.connect(self._toggle_pause)
        sub.addWidget(self.btn_pause)
        b = QPushButton("Leeren")
        b.clicked.connect(lambda: (self.table.setRowCount(0), setattr(self, "count", 0)))
        sub.addWidget(b)
        lay.addLayout(sub)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Zeit", "Thema", "QoS", "Retain", "Inhalt"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        h = self.table.horizontalHeader()
        for c in range(4):
            h.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._show_payload)
        lay.addWidget(self.table, 1)
        self.detail = _mono(QPlainTextEdit())
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(90)
        lay.addWidget(self.detail)

        pub = QGroupBox("Senden")
        h2 = QHBoxLayout(pub)
        self.ptopic = QLineEdit("netzentrale/test")
        self.payload = QLineEdit('{"hallo": "welt"}')
        self.qos = QComboBox()
        self.qos.addItems(["QoS 0", "QoS 1", "QoS 2"])
        self.retain = QCheckBox("Retain")
        b = QPushButton("Senden")
        b.clicked.connect(self.publish)
        for w in (QLabel("Thema"), self.ptopic, QLabel("Inhalt"), self.payload, self.qos, self.retain, b):
            h2.addWidget(w)
        h2.setStretch(1, 1)
        h2.setStretch(3, 2)
        lay.addWidget(pub)

    # -- Verbindung --
    def toggle_connect(self):
        if self.client:
            self.client.disconnect()
            return
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            QMessageBox.warning(self, "MQTT", "Python-Paket „paho-mqtt“ fehlt.")
            return
        host = self.host.currentText().strip()
        if not host:
            return
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.cid.text().strip() or None)
        if self.user.text().strip():
            c.username_pw_set(self.user.text().strip(), self.pw.text() or None)
        if self.tls.isChecked():
            c.tls_set()
        br = self.bridge

        def on_connect(cl, ud, flags, rc, props=None):
            br.connected.emit(not rc.is_failure, str(rc))

        def on_disconnect(cl, ud, flags, rc, props=None):
            br.disconnected.emit(str(rc))

        def on_message(cl, ud, msg):
            br.message.emit(msg.topic, bytes(msg.payload), msg.qos, bool(msg.retain))

        def on_connect_fail(cl, ud):
            br.disconnected.emit("Broker nicht erreichbar (Adresse/Port/Firewall prüfen)")
            cl.disconnect()

        c.on_connect, c.on_disconnect, c.on_message = on_connect, on_disconnect, on_message
        c.on_connect_fail = on_connect_fail
        self.state.setText("verbinde …")
        try:
            c.connect_async(host, self.port.value(), keepalive=30)
            c.loop_start()
        except (OSError, ValueError) as e:
            self.state.setText(f"❌ {e}")
            return
        self.client = c
        self.btn_conn.setText("Trennen")

    def _on_connected(self, ok: bool, rc: str):
        if ok:
            self.state.setText(f"✅ verbunden mit {self.host.currentText()}:{self.port.value()}")
            self.btn_test.setEnabled(True)
            self.subscribe()
        else:
            self.state.setText(f"❌ Broker lehnt ab: {rc}")
            self.client.disconnect()

    def _on_disconnected(self, rc: str):
        if self.client:
            self.client.loop_stop() if threading.current_thread() is threading.main_thread() else None
        self.client = None
        self.btn_conn.setText("Verbinden")
        self.btn_test.setEnabled(False)
        if "Success" in rc or rc == "0":
            self.state.setText("getrennt")
        else:
            self.state.setText(f"Verbindung verloren / nicht möglich: {rc}")

    def subscribe(self):
        if self.client and self.topic.text().strip():
            self.client.subscribe(self.topic.text().strip(), qos=0)
            self.message.emit(f"MQTT: abonniert {self.topic.text().strip()}")

    def publish(self):
        if not self.client:
            self.state.setText("Bitte zuerst verbinden.")
            return
        info = self.client.publish(self.ptopic.text().strip(), self.payload.text().encode(),
                                   qos=self.qos.currentIndex(), retain=self.retain.isChecked())
        self.message.emit("MQTT: gesendet" if info.rc == 0 else f"MQTT: Senden fehlgeschlagen ({info.rc})")

    def selftest(self):
        """Prüft, ob der Broker eine Nachricht annimmt UND wieder ausliefert."""
        if not self.client:
            return
        topic = f"netzentrale/selbsttest/{uuid.uuid4().hex[:8]}"
        self.client.subscribe(topic, qos=1)
        time.sleep(0.2)  # Abo beim Broker ankommen lassen
        self._probe = (topic, time.perf_counter())
        self.client.publish(topic, b"ping", qos=1)
        self.state.setText("Funktionstest läuft …")
        from PySide6.QtCore import QTimer
        QTimer.singleShot(5000, self._probe_timeout)

    def _probe_timeout(self):
        if self._probe:
            self.state.setText("❌ Funktionstest: Nachricht kam nicht zurück (Rechte/ACL des Brokers prüfen)")
            self._probe = None

    def _on_message(self, topic: str, payload: bytes, qos: int, retain: bool):
        if self._probe and topic == self._probe[0]:
            ms = (time.perf_counter() - self._probe[1]) * 1000
            self.state.setText(f"✅ Broker nimmt an und liefert aus – Umlaufzeit {ms:.0f} ms")
            self.client.unsubscribe(topic)
            self._probe = None
            return
        if self.paused:
            return
        flt = self.filter.text().strip().lower()
        text = payload.decode("utf-8", errors="replace")
        if flt and flt not in topic.lower() and flt not in text.lower():
            return
        self.count += 1
        if self.table.rowCount() >= self.MAX_ROWS:
            self.table.removeRow(self.table.rowCount() - 1)
        self.table.insertRow(0)
        vals = [datetime.now().strftime("%H:%M:%S.%f")[:-3], topic, str(qos), "ja" if retain else "",
                text if len(text) < 300 else text[:300] + " …"]
        for c, v in enumerate(vals):
            it = QTableWidgetItem(v)
            if c == 4:
                it.setData(Qt.UserRole, text)
            self.table.setItem(0, c, it)

    def _show_payload(self):
        r = self.table.currentRow()
        if r >= 0 and self.table.item(r, 4):
            txt = self.table.item(r, 4).data(Qt.UserRole)
            try:
                import json
                txt = json.dumps(json.loads(txt), indent=2, ensure_ascii=False)
            except (ValueError, TypeError):
                pass
            self.detail.setPlainText(f"{self.table.item(r, 1).text()}\n{txt}")

    def _toggle_pause(self):
        self.paused = not self.paused
        self.btn_pause.setText("Weiter" if self.paused else "Pause")

    def scan_brokers(self):
        if self._scan and self._scan.isRunning():
            return
        self.btn_scan.setText("suche …")
        self._scan = BrokerScan(self)
        self._scan.found.connect(lambda h, p: self.host.addItem(f"{h}") if self.host.findText(h) < 0 else None)
        self._scan.done.connect(self._scan_done)
        self._scan.start()

    def _scan_done(self, n):
        self.btn_scan.setText("Broker im Netz suchen")
        self.state.setText(f"{n} MQTT-Broker im eigenen Netz gefunden" + (" – Auswahl im Adressfeld" if n else ""))
        if n:
            self.host.showPopup()
