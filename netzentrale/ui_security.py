"""Seite „Sicherheits-Check“: offene Dienste und typische Schwachstellen eigener Systeme."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from . import security

COLORS = {security.HOCH: QColor("#c94040"), security.MITTEL: QColor("#d98a00"),
          security.NIEDRIG: QColor("#b8a000"), security.INFO: QColor("#7a7a7a"), security.OK: QColor("#2e9d4f")}


class SecurityWorker(QThread):
    progress = Signal(str)
    done = Signal(object, object)
    failed = Signal(str)

    def __init__(self, target: str, parent=None):
        super().__init__(parent)
        self.target = target

    def run(self):
        try:
            meta, findings = security.run_check(self.target, progress=self.progress.emit)
            self.done.emit(meta, findings)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class SecurityPage(QWidget):
    message = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker: SecurityWorker | None = None
        self.meta, self.findings = None, []
        lay = QVBoxLayout(self)
        intro = QLabel("Prüft ein System auf offene Dienste und typische Schwachstellen – nur beobachtend: "
                       "keine Angriffe, keine Passwortversuche, keine Änderungen.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        bar = QHBoxLayout()
        self.host = QLineEdit()
        self.host.setPlaceholderText("IP, Name oder URL – z. B. 192.168.1.20 oder https://kasse.bsb.local")
        self.host.returnPressed.connect(self.start)
        bar.addWidget(self.host, 1)
        self.btn = QPushButton("Check starten")
        self.btn.clicked.connect(self.start)
        bar.addWidget(self.btn)
        lay.addLayout(bar)
        self.consent = QCheckBox("Ich bin berechtigt, dieses System zu prüfen (eigenes System oder Auftrag).")
        lay.addWidget(self.consent)

        self.head = QLabel("")
        self.head.setWordWrap(True)
        self.head.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.head)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Risiko", "Port", "Befund", "Details", "Empfehlung"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setWordWrap(True)
        self.table.verticalHeader().setVisible(False)
        h = self.table.horizontalHeader()
        for c in (0, 1):
            h.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.Interactive)
        self.table.setColumnWidth(2, 260)
        h.setSectionResizeMode(3, QHeaderView.Stretch)
        h.setSectionResizeMode(4, QHeaderView.Stretch)
        lay.addWidget(self.table, 1)

        act = QHBoxLayout()
        b = QPushButton("Bericht kopieren")
        b.clicked.connect(self.copy_report)
        act.addWidget(b)
        b = QPushButton("Bericht speichern …")
        b.clicked.connect(self.save_report)
        act.addWidget(b)
        act.addStretch(1)
        note = QLabel("Ersetzt keinen Penetrationstest – findet aber die häufigsten Fehler schnell.")
        note.setStyleSheet("color: gray; font-size: 11px;")
        act.addWidget(note)
        lay.addLayout(act)

    def run_for(self, host: str, own_device: bool = False):
        self.host.setText(host)
        if own_device:
            self.consent.setChecked(True)   # Aufruf aus der eigenen Geräteliste
        self.start()

    def start(self):
        if self.worker and self.worker.isRunning():
            return
        target = self.host.text().strip()
        if not target:
            return
        if not self.consent.isChecked():
            self.head.setText("Bitte bestätigen, dass du dieses System prüfen darfst. "
                              "Fremde Systeme ohne Erlaubnis zu scannen kann rechtliche Folgen haben.")
            return
        self.table.setRowCount(0)
        self.head.setText("Starte …")
        self.btn.setEnabled(False)
        self.worker = SecurityWorker(target, self)
        self.worker.progress.connect(self.head.setText)
        self.worker.done.connect(self._done)
        self.worker.failed.connect(lambda m: (self.head.setText(f"❌ {m}"), self.btn.setEnabled(True)))
        self.worker.start()

    def _done(self, meta, findings):
        self.btn.setEnabled(True)
        self.meta, self.findings = meta, findings
        counts = {lv: sum(f.level == lv for f in findings) for lv in (security.HOCH, security.MITTEL, security.NIEDRIG)}
        verdict = ("🔴 Handlungsbedarf" if counts[security.HOCH] else
                   "🟠 Verbesserungen empfohlen" if counts[security.MITTEL] else
                   "🟢 Keine größeren Probleme gefunden")
        rev = f" · Name: {meta['reverse']}" if meta.get("reverse") else ""
        where = "öffentlich (Internet)" if meta["public"] else "internes Netz"
        self.head.setText(f"<b>{verdict}</b> — {meta['host']} → {meta['ip']}{rev} · {where} · "
                          f"offene Ports: {', '.join(map(str, meta['open'])) or 'keine'}<br>"
                          f"{counts[security.HOCH]} hoch · {counts[security.MITTEL]} mittel · "
                          f"{counts[security.NIEDRIG]} niedrig")
        self.table.setRowCount(len(findings))
        for r, f in enumerate(findings):
            vals = [f.level, str(f.port or ""), f.title, f.detail, f.advice]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 0:
                    it.setForeground(QBrush(COLORS[f.level]))
                    fn = it.font()
                    fn.setBold(True)
                    it.setFont(fn)
                self.table.setItem(r, c, it)
        self.table.resizeRowsToContents()
        self.message.emit(f"Sicherheits-Check {meta['host']}: {verdict}")

    def copy_report(self):
        if self.meta:
            QGuiApplication.clipboard().setText(security.report_text(self.meta, self.findings))
            self.message.emit("Bericht in die Zwischenablage kopiert")

    def save_report(self):
        if not self.meta:
            return
        name = f"sicherheitscheck-{self.meta['host']}-{datetime.now():%Y-%m-%d}.txt".replace(":", "_")
        path, _ = QFileDialog.getSaveFileName(self, "Bericht speichern", name, "Text (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(security.report_text(self.meta, self.findings))
            self.message.emit(f"Bericht gespeichert: {path}")
