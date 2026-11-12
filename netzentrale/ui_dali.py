"""Seite „DALI-Licht“: Leuchten, Gruppen und Broadcast über Lunatone DALI-2 IoT Gateways.
Mehrere Gateways werden über die Geräteliste (Dienst „DALI“) verwaltet."""
from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QPushButton, QTabWidget, QVBoxLayout,
    QWidget,
)

from . import launch
from .dali.gateway import DaliGateway, Device as DaliDevice, control_payload
from .dali.widgets import DALI_TYPE_NAMES, REFRESH_MS, STYLE, ControlRow, Job, card, scroll_page


class DaliPage(QWidget):
    message = Signal(str)

    def __init__(self, device_store, parent=None):
        super().__init__(parent)
        self.setStyleSheet(STYLE)
        self.device_store = device_store
        self.settings = QSettings("BSB", "Netzentrale")
        self.pool = QThreadPool.globalInstance()
        self.gw: DaliGateway | None = None
        self.devices: dict[int, DaliDevice] = {}
        self.device_rows: dict[int, ControlRow] = {}
        self.group_rows: dict[int, ControlRow] = {}
        self._known_layout: tuple = ()
        self._refresh_busy = False
        self._jobs: set[Job] = set()

        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Gateway:"))
        self.gw_combo = QComboBox()
        self.gw_combo.setEditable(True)
        self.gw_combo.setMinimumWidth(320)
        self.gw_combo.lineEdit().setPlaceholderText("Gateway aus der Liste wählen oder IP eingeben")
        self.gw_combo.lineEdit().returnPressed.connect(self.connect_gateway)
        self.gw_combo.activated.connect(lambda _: self.connect_gateway())
        top.addWidget(self.gw_combo, 1)
        b = QPushButton("Verbinden")
        b.clicked.connect(self.connect_gateway)
        top.addWidget(b)
        self.btn_refresh = QPushButton("Aktualisieren")
        self.btn_refresh.clicked.connect(lambda: self.refresh(force_rebuild=True))
        self.btn_refresh.setEnabled(False)
        top.addWidget(self.btn_refresh)
        self.btn_web = QPushButton("Web-Oberfläche")
        self.btn_web.clicked.connect(self._open_web)
        self.btn_web.setEnabled(False)
        top.addWidget(self.btn_web)
        lay.addLayout(top)
        self.state = QLabel("Nicht verbunden. Gateways legst du in „Verbindungen“ mit dem Dienst „DALI“ an "
                            "– oder übernimmst sie aus dem Netzwerkscan.")
        self.state.setWordWrap(True)
        lay.addWidget(self.state)

        self.tabs = QTabWidget()
        self.dev_page, self.dev_lay = scroll_page()
        self.grp_page, self.grp_lay = scroll_page()
        self.tabs.addTab(self.dev_page, "Leuchten")
        self.tabs.addTab(self.grp_page, "Gruppen")
        self.tabs.addTab(self._build_broadcast_page(), "Alle (Broadcast)")
        self.tabs.setEnabled(False)
        lay.addWidget(self.tabs, 1)

        self.timer = QTimer(self, interval=REFRESH_MS)
        self.timer.timeout.connect(self.refresh)

    # -- Gateways aus der Geräteliste --
    def reload_gateways(self):
        cur = self.gw_combo.currentText()
        self.gw_combo.blockSignals(True)
        self.gw_combo.clear()
        for d in self.device_store.devices:
            if getattr(d, "dali", None) and d.host:
                self.gw_combo.addItem(f"{d.name}  ({d.host})", d.host)
        last = self.settings.value("dali/last", "")
        if cur:
            self.gw_combo.setEditText(cur)
        elif last:
            idx = self.gw_combo.findData(last)
            self.gw_combo.setCurrentIndex(idx) if idx >= 0 else self.gw_combo.setEditText(last)
        self.gw_combo.blockSignals(False)

    def open_for(self, host: str):
        """Von der Geräteliste aus aufgerufen."""
        self.reload_gateways()
        idx = self.gw_combo.findData(host)
        if idx >= 0:
            self.gw_combo.setCurrentIndex(idx)
        else:
            self.gw_combo.setEditText(host)
        self.connect_gateway()

    def _host(self) -> str:
        idx = self.gw_combo.currentIndex()
        if idx >= 0 and self.gw_combo.itemText(idx) == self.gw_combo.currentText():
            return self.gw_combo.itemData(idx)
        return self.gw_combo.currentText().strip()

    def showEvent(self, e):
        super().showEvent(e)
        self.reload_gateways()
        if self.gw:
            self.timer.start()
            self.refresh()

    def hideEvent(self, e):
        super().hideEvent(e)
        self.timer.stop()   # kein Polling, solange die Seite nicht sichtbar ist

    # -- Hintergrundaufträge --
    def run(self, fn, *args, on_done=None, on_fail=None, **kwargs):
        job = Job(fn, *args, **kwargs)
        job.setAutoDelete(False)
        self._jobs.add(job)

        def finished(result):
            self._jobs.discard(job)
            if on_done:
                on_done(result)

        def failed(msg):
            self._jobs.discard(job)
            (on_fail or self.on_error)(msg)

        job.signals.done.connect(finished, Qt.QueuedConnection)
        job.signals.failed.connect(failed, Qt.QueuedConnection)
        self.pool.start(job)

    def on_error(self, msg: str):
        self.state.setText(f"⚠ {msg}")
        self.message.emit(f"DALI: {msg}")

    # -- Verbindung --
    def connect_gateway(self):
        host = self._host()
        if not host:
            return
        self.timer.stop()
        gw = DaliGateway(host)
        self.gw = gw
        self.tabs.setEnabled(False)
        self.state.setText(f"Verbinde mit {host} …")
        self.run(gw.info, on_done=lambda info: self._on_connected(gw, host, info))

    def _on_connected(self, gw, host: str, info: dict):
        if self.gw is not gw:
            return  # inzwischen anderes Gateway gewählt
        self.settings.setValue("dali/last", host)
        self.btn_refresh.setEnabled(True)
        self.btn_web.setEnabled(True)
        self.tabs.setEnabled(True)
        name = (info or {}).get("name", "DALI-2 IoT")
        ver = (info or {}).get("version", "?")
        self.state.setText(f"✅ Verbunden mit „{name}“ ({host}, Firmware {ver})")
        self._known_layout = ()
        self.refresh(force_rebuild=True)
        if self.isVisible():
            self.timer.start()

    def _open_web(self):
        if self.gw:
            launch.open_link(self.gw.base)

    # -- Status --
    def refresh(self, force_rebuild: bool = False):
        if not self.gw or self._refresh_busy:
            return
        self._refresh_busy = True

        def done(devs):
            self._refresh_busy = False
            self._apply_devices(devs, force_rebuild)

        def fail(msg):
            self._refresh_busy = False
            self.on_error(msg)

        self.run(self.gw.devices, on_done=done, on_fail=fail)

    def _apply_devices(self, devs, force_rebuild):
        self.devices = {d.id: d for d in devs}
        key = tuple((d.id, d.label, tuple(d.groups), d.is_dimmable) for d in devs)
        if force_rebuild or key != self._known_layout:
            self._known_layout = key
            self._build_device_page(devs)
            self._build_group_page(devs)
        for d in devs:
            if (row := self.device_rows.get(d.id)):
                row.show_state(d.is_on, d.level)
        for g, row in self.group_rows.items():
            members = [d for d in devs if g in d.groups and d.level is not None]
            if members:
                row.show_state(any(d.is_on or (d.level or 0) > 0 for d in members),
                               sum(d.level for d in members) / len(members))

    # -- Senden --
    def send(self, target, ident, payload):
        if not self.gw or not payload:
            return
        gw = self.gw
        fn = {"device": lambda: gw.control_device(ident, **payload),
              "group": lambda: gw.control_group(ident, **payload),
              "broadcast": lambda: gw.control_broadcast(**payload)}[target]
        self.run(fn, on_done=lambda _: self._after_send(target, ident, payload))

    def _after_send(self, target, ident, payload):
        what = {"device": f"Leuchte {self.devices[ident].label}" if ident in self.devices else "Leuchte",
                "group": self._group_name(ident) if target == "group" else "", "broadcast": "Alle"}[target]
        parts = []
        if "switchable" in payload:
            parts.append("EIN" if payload["switchable"] else "AUS")
        if "dimmable" in payload:
            parts.append(f"{payload['dimmable']} %")
        if "scene" in payload:
            parts.append(f"Szene {payload['scene']}")
        if payload.get("gotoLastActive"):
            parts.append("letzter Wert")
        self.message.emit(f"DALI: {what} → {', '.join(parts)}")
        if target != "device":
            QTimer.singleShot(400, self.refresh)

    # -- Seiten --
    @staticmethod
    def _clear(lay):
        while lay.count() > 1:
            item = lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _gkey(self, g):
        host = self.gw.base.split("://")[-1] if self.gw else ""
        return f"dali/{host}/groupname/{g}"

    def _group_name(self, g):
        return self.settings.value(self._gkey(g), f"Gruppe {g}")

    def _build_device_page(self, devs):
        self._clear(self.dev_lay)
        self.device_rows.clear()
        if not devs:
            self.dev_lay.insertWidget(0, QLabel("Keine Leuchten gefunden – bitte zuerst im Gateway-Webinterface "
                                                "einen DALI-Scan durchführen."))
            return
        for i, d in enumerate(devs):
            title = QLabel(d.label)
            title.setObjectName("title")
            types = ", ".join(DALI_TYPE_NAMES.get(t, f"DT{t}") for t in d.dali_types) or "DALI-Gerät"
            grp = ", ".join(map(str, d.groups)) if d.groups else "–"
            sub = QLabel(f"Adresse A{d.address} · Linie {d.line} · {types} · Gruppen: {grp}")
            sub.setObjectName("sub")
            rn = QPushButton("Umbenennen")
            rn.setFlat(True)
            rn.clicked.connect(lambda _=False, dev=d: self._rename(dev))
            head = QHBoxLayout()
            head.addWidget(title)
            head.addStretch(1)
            head.addWidget(rn)
            row = ControlRow(dimmable=d.is_dimmable, switchable=d.is_switchable, scenes=d.supports_scenes)
            row.command.connect(lambda p, did=d.id: self.send("device", did, p))
            self.device_rows[d.id] = row
            self.dev_lay.insertWidget(i, card(head, sub, row))

    def _build_group_page(self, devs):
        self._clear(self.grp_lay)
        self.group_rows.clear()
        groups = sorted({g for d in devs for g in d.groups})
        if not groups:
            self.grp_lay.insertWidget(0, QLabel("Keinem Gerät ist eine DALI-Gruppe zugewiesen.\n"
                                                "Gruppen werden im Gateway-Webinterface konfiguriert."))
            return
        for i, g in enumerate(groups):
            members = [d.label for d in devs if g in d.groups]
            title = QLabel(self._group_name(g))
            title.setObjectName("title")
            sub = QLabel(f"G{g} · {len(members)} Geräte: " + ", ".join(members))
            sub.setObjectName("sub")
            sub.setWordWrap(True)
            rn = QPushButton("Umbenennen")
            rn.setFlat(True)
            rn.clicked.connect(lambda _=False, gg=g: self._rename_group(gg))
            head = QHBoxLayout()
            head.addWidget(title)
            head.addStretch(1)
            head.addWidget(rn)
            row = ControlRow()
            row.command.connect(lambda p, gg=g: self.send("group", gg, p))
            self.group_rows[g] = row
            self.grp_lay.insertWidget(i, card(head, sub, row))

    def _build_broadcast_page(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Befehle auf dieser Seite gehen per Broadcast an <b>alle</b> Geräte dieses Gateways."))
        btns = QHBoxLayout()
        for text, payload in (("Alle EIN", control_payload(on=True)), ("Alle AUS", control_payload(on=False)),
                              ("Letzter Wert", control_payload(last_active=True))):
            b = QPushButton(text)
            b.setObjectName("big")
            b.clicked.connect(lambda _=False, p=payload: self.send("broadcast", None, p))
            btns.addWidget(b)
        lay.addLayout(btns)
        grid = QGridLayout()
        for i, lvl in enumerate((10, 25, 50, 75, 100)):
            b = QPushButton(f"{lvl} %")
            b.clicked.connect(lambda _=False, v=lvl: self.send("broadcast", None, control_payload(level=v)))
            grid.addWidget(b, 0, i)
        lay.addWidget(QLabel("Helligkeit:"))
        lay.addLayout(grid)
        lay.addWidget(QLabel("Freie Einstellung:"))
        row = ControlRow()
        row.command.connect(lambda p: self.send("broadcast", None, p))
        lay.addWidget(card(row))
        lay.addStretch(1)
        return w

    def _rename(self, dev):
        name, ok = QInputDialog.getText(self, "Leuchte umbenennen", f"Neuer Name für A{dev.address}:", text=dev.name)
        if ok and name.strip() and self.gw:
            self.run(self.gw.rename_device, dev.id, name.strip(), on_done=lambda _: self.refresh(force_rebuild=True))

    def _rename_group(self, g):
        name, ok = QInputDialog.getText(self, "Gruppe benennen",
                                        f"Anzeigename für DALI-Gruppe {g} (nur lokal gespeichert):",
                                        text=self._group_name(g))
        if ok and name.strip():
            self.settings.setValue(self._gkey(g), name.strip())
            self.refresh(force_rebuild=True)
