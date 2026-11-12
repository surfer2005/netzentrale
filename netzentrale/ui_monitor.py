"""Seite „Überwachung“: Geräte/Dienste laufend prüfen, Ausfälle melden,
Verfügbarkeit und Zertifikatsablauf anzeigen."""
from __future__ import annotations

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton, QSpinBox, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from . import net
from .storage import data_dir

KINDS = {"ping": "Ping", "tcp": "TCP-Port", "web": "Webseite"}
GREEN, RED, GREY, ORANGE = QColor("#2e9d4f"), QColor("#c94040"), QColor("#9a9a9a"), QColor("#d98a00")
FAILS_BEFORE_ALARM = 2   # erst nach 2 Fehlversuchen in Folge melden (kein Flattern)
MAX_EVENTS = 300


@dataclass
class Target:
    name: str
    kind: str            # ping | tcp | web
    target: str          # Host oder URL
    port: int = 0
    interval: int = 60   # Sekunden
    notify: bool = True
    cert_warn: int = 21  # Tage
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    # Laufzeitstatistik (wird mitgespeichert)
    checks: int = 0
    ok_count: int = 0
    state: str = "?"     # up | down | ?
    since: float = 0.0
    fails: int = 0
    last_ms: float | None = None
    detail: str = ""
    cert_days: int | None = None
    cert_info: str = ""
    last_check: float = 0.0
    cert_notified: str = ""


class MonitorStore:
    def __init__(self):
        self.path = data_dir() / "ueberwachung.json"
        self.targets: list[Target] = []
        self.events: list[str] = []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            fields = Target.__dataclass_fields__
            self.targets = [Target(**{k: v for k, v in t.items() if k in fields}) for t in raw.get("targets", [])]
            self.events = raw.get("events", [])
        except (FileNotFoundError, ValueError, TypeError):
            pass

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"targets": [asdict(t) for t in self.targets],
                                   "events": self.events[:MAX_EVENTS]}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(self.path)

    def log(self, text: str):
        self.events.insert(0, f"{datetime.now():%d.%m. %H:%M:%S}  {text}")
        del self.events[MAX_EVENTS:]


class CheckRound(QThread):
    result = Signal(str, dict)

    def __init__(self, targets: list[Target], parent=None):
        super().__init__(parent)
        self.targets = [(t.id, t.kind, t.target, t.port) for t in targets]

    def run(self):
        with ThreadPoolExecutor(max_workers=24) as ex:
            futs = {ex.submit(net.check_target, k, tg, p): tid for tid, k, tg, p in self.targets}
            for f in as_completed(futs):
                try:
                    self.result.emit(futs[f], f.result())
                except Exception as e:  # noqa: BLE001
                    self.result.emit(futs[f], {"ok": False, "ms": None, "detail": str(e),
                                               "cert_days": None, "cert_info": ""})


class TargetDialog(QDialog):
    def __init__(self, t: Target | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Überwachungsziel")
        self.setMinimumWidth(440)
        self.t = t or Target(name="", kind="ping", target="")
        f = QFormLayout(self)
        self.name = QLineEdit(self.t.name)
        self.kind = QComboBox()
        for k, v in KINDS.items():
            self.kind.addItem(v, k)
        self.kind.setCurrentIndex(self.kind.findData(self.t.kind))
        self.target = QLineEdit(self.t.target)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(self.t.port or 80)
        self.interval = QSpinBox()
        self.interval.setRange(10, 86400)
        self.interval.setSuffix(" s")
        self.interval.setValue(self.t.interval)
        self.notify = QCheckBox("Bei Ausfall und Wiederkehr Mitteilung anzeigen")
        self.notify.setChecked(self.t.notify)
        self.cert = QSpinBox()
        self.cert.setRange(1, 365)
        self.cert.setSuffix(" Tage vorher")
        self.cert.setValue(self.t.cert_warn)
        f.addRow("Name:", self.name)
        f.addRow("Art:", self.kind)
        f.addRow("Adresse / URL:", self.target)
        f.addRow("Port:", self.port)
        f.addRow("Prüfintervall:", self.interval)
        f.addRow("Zertifikatswarnung:", self.cert)
        f.addRow("", self.notify)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self._kind_changed()
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setText("Speichern")
        bb.button(QDialogButtonBox.Cancel).setText("Abbrechen")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def _kind_changed(self):
        k = self.kind.currentData()
        self.port.setEnabled(k == "tcp")
        self.cert.setEnabled(k == "web")
        self.target.setPlaceholderText("https://server-01.local" if k == "web" else "IP oder Hostname")

    def _ok(self):
        if not self.target.text().strip():
            return
        t = self.t
        t.kind = self.kind.currentData()
        t.target = self.target.text().strip()
        t.name = self.name.text().strip() or t.target
        t.port = self.port.value() if t.kind == "tcp" else 0
        t.interval = self.interval.value()
        t.notify = self.notify.isChecked()
        t.cert_warn = self.cert.value()
        self.accept()


class MonitorPage(QWidget):
    message = Signal(str)
    COLS = ["", "Name", "Ziel", "Art", "Antwort", "Verfügbarkeit", "Status seit", "Zertifikat", "Meldung"]

    def __init__(self, device_store=None, parent=None):
        super().__init__(parent)
        self.store = MonitorStore()
        self.device_store = device_store
        self.round: CheckRound | None = None
        self.paused = False

        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        for text, slot in (("+ Ziel", self.add), ("Aus Geräteliste …", self.add_from_devices),
                           ("Bearbeiten", self.edit), ("Löschen", self.delete), ("Jetzt prüfen", self.check_all)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            bar.addWidget(b)
        bar.addStretch(1)
        self.btn_pause = QPushButton("Pausieren")
        self.btn_pause.clicked.connect(self.toggle_pause)
        bar.addWidget(self.btn_pause)
        self.summary = QLabel("")
        bar.addWidget(self.summary)
        lay.addLayout(bar)

        split = QSplitter(Qt.Vertical)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        h = self.table.horizontalHeader()
        for c in range(len(self.COLS)):
            h.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(8, QHeaderView.Stretch)
        self.table.itemDoubleClicked.connect(lambda _: self.edit())
        split.addWidget(self.table)
        ev = QWidget()
        evl = QVBoxLayout(ev)
        evl.setContentsMargins(0, 0, 0, 0)
        evl.addWidget(QLabel("Ereignisse:"))
        self.events = QListWidget()
        evl.addWidget(self.events)
        split.addWidget(ev)
        split.setSizes([400, 160])
        lay.addWidget(split, 1)
        lay.addWidget(QLabel("Die Überwachung läuft, solange die Netzentrale geöffnet ist (auch minimiert)."))

        self.timer = QTimer(self, interval=5000)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self.refresh()
        QTimer.singleShot(1500, self.check_all)

    # -- Prüfablauf --
    def tick(self):
        if self.paused or (self.round and self.round.isRunning()):
            return
        now = time.time()
        due = [t for t in self.store.targets if now - t.last_check >= t.interval]
        if due:
            self._start(due)

    def check_all(self):
        if not (self.round and self.round.isRunning()) and self.store.targets:
            self._start(list(self.store.targets))

    def _start(self, targets):
        for t in targets:
            t.last_check = time.time()
        self.round = CheckRound(targets, self)
        self.round.result.connect(self._on_result)
        self.round.finished.connect(self._round_done)
        self.round.start()

    def _on_result(self, tid: str, r: dict):
        t = next((x for x in self.store.targets if x.id == tid), None)
        if not t:
            return
        t.checks += 1
        t.last_ms, t.detail = r.get("ms"), r.get("detail", "")
        t.cert_days, t.cert_info = r.get("cert_days"), r.get("cert_info", "")
        if r["ok"]:
            t.ok_count += 1
            if t.state != "up":
                if t.state == "down":
                    downtime = self._fmt_dur(time.time() - t.since)
                    self.store.log(f"✅ {t.name} wieder erreichbar (war {downtime} weg)")
                    if t.notify:
                        net.notify("Wieder erreichbar", f"{t.name} ({t.target})")
                t.state, t.since = "up", time.time()
            t.fails = 0
        else:
            t.fails += 1
            if t.state != "down" and (t.fails >= FAILS_BEFORE_ALARM or t.state == "?"):
                if t.state == "up" or t.fails >= FAILS_BEFORE_ALARM:
                    self.store.log(f"❌ {t.name} nicht erreichbar – {t.detail}")
                    if t.notify:
                        net.notify("Ausfall", f"{t.name}: {t.detail}")
                t.state, t.since = "down", time.time()
        # Zertifikat läuft bald ab → einmal pro Tag melden
        if t.cert_days is not None and t.cert_days <= t.cert_warn:
            today = datetime.now().strftime("%Y-%m-%d")
            if t.cert_notified != today:
                t.cert_notified = today
                self.store.log(f"⚠️ Zertifikat von {t.name} läuft in {t.cert_days} Tagen ab ({t.cert_info})")
                if t.notify:
                    net.notify("Zertifikat läuft ab", f"{t.name}: noch {t.cert_days} Tage")

    def _round_done(self):
        self.store.save()
        self.refresh()

    # -- Anzeige --
    @staticmethod
    def _fmt_dur(sec: float) -> str:
        sec = int(sec)
        if sec < 120:
            return f"{sec} s"
        if sec < 7200:
            return f"{sec // 60} min"
        if sec < 172800:
            return f"{sec // 3600} h"
        return f"{sec // 86400} Tage"

    def refresh(self):
        sel = self._selected()
        ts = self.store.targets
        self.table.setRowCount(len(ts))
        for row, t in enumerate(ts):
            color = {"up": GREEN, "down": RED}.get(t.state, GREY)
            avail = f"{100 * t.ok_count / t.checks:.1f} %" if t.checks else "–"
            if t.kind == "web" and t.cert_days is not None:
                cert = f"{t.cert_days} Tage ({t.cert_info})"
            elif t.kind == "web" and t.cert_info:
                cert = t.cert_info
            else:
                cert = ""
            since = f"{datetime.fromtimestamp(t.since):%d.%m. %H:%M} ({self._fmt_dur(time.time() - t.since)})" if t.since else ""
            target = t.target + (f":{t.port}" if t.kind == "tcp" else "")
            vals = ["●", t.name, target, KINDS[t.kind], f"{t.last_ms:.0f} ms" if t.last_ms is not None else "–",
                    avail, since, cert, t.detail]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.UserRole, t.id)
                if c == 0:
                    it.setForeground(QBrush(color))
                if c == 7 and t.cert_days is not None and t.cert_days <= t.cert_warn:
                    it.setForeground(QBrush(ORANGE))
                if c == 7 and t.kind == "web" and t.cert_days is None and t.cert_info:
                    it.setForeground(QBrush(RED))
                self.table.setItem(row, c, it)
            if sel and sel.id == t.id:
                self.table.selectRow(row)
        up = sum(t.state == "up" for t in ts)
        down = sum(t.state == "down" for t in ts)
        self.summary.setText(f"{up} erreichbar · {down} gestört · {len(ts)} Ziele")
        self.events.clear()
        self.events.addItems(self.store.events[:100])

    def _selected(self) -> Target | None:
        r = self.table.currentRow()
        it = self.table.item(r, 0) if r >= 0 else None
        tid = it.data(Qt.UserRole) if it else None
        return next((t for t in self.store.targets if t.id == tid), None)

    # -- Bearbeiten --
    def add(self, prefill: Target | None = None):
        dlg = TargetDialog(prefill, self)
        if dlg.exec():
            self.store.targets.append(dlg.t)
            self.store.save()
            self.refresh()
            self._start([dlg.t])

    def edit(self):
        t = self._selected()
        if t and TargetDialog(t, self).exec():
            t.checks = t.ok_count = t.fails = 0
            t.state, t.since = "?", 0.0
            self.store.save()
            self.refresh()
            self._start([t])

    def delete(self):
        t = self._selected()
        if t and QMessageBox.question(self, "Löschen", f"„{t.name}“ nicht mehr überwachen?") == QMessageBox.Yes:
            self.store.targets.remove(t)
            self.store.save()
            self.refresh()

    def add_from_devices(self):
        """Alle Geräte der Verbindungsliste übernehmen, die noch nicht überwacht werden."""
        if not self.device_store:
            return
        existing = {(t.kind, t.target) for t in self.store.targets}
        new = []
        for d in self.device_store.devices:
            if not d.host:
                continue
            if d.web and d.web.get("url"):
                cand = Target(name=d.name, kind="web", target=d.web["url"])
            elif d.dali:
                cand = Target(name=d.name, kind="web", target=f"http://{d.host}/info")
            else:
                cand = Target(name=d.name, kind="ping", target=d.host)
            if (cand.kind, cand.target) not in existing:
                new.append(cand)
                existing.add((cand.kind, cand.target))
        if not new:
            QMessageBox.information(self, "Überwachung", "Alle Geräte werden bereits überwacht.")
            return
        if QMessageBox.question(self, "Überwachung", f"{len(new)} Geräte zur Überwachung hinzufügen?") == QMessageBox.Yes:
            self.store.targets.extend(new)
            self.store.save()
            self.refresh()
            self._start(new)

    def toggle_pause(self):
        self.paused = not self.paused
        self.btn_pause.setText("Fortsetzen" if self.paused else "Pausieren")
        self.message.emit("Überwachung pausiert" if self.paused else "Überwachung läuft")
