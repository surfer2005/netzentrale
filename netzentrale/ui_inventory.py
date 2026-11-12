"""Seite „Inventar“: Netzwerkscans als Stand speichern, vergleichen, exportieren."""
from __future__ import annotations

import csv
import json
from datetime import datetime

from PySide6.QtCore import Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from . import net
from .storage import data_dir

NEW, GONE, CHANGED = QColor("#2e9d4f"), QColor("#c94040"), QColor("#d98a00")


class InventoryStore:
    def __init__(self):
        self.dir = data_dir() / "inventar"
        self.dir.mkdir(exist_ok=True)
        self.known_path = self.dir / "bekannt.json"

    def snapshots(self) -> list[dict]:
        out = []
        for p in sorted(self.dir.glob("stand-*.json"), reverse=True):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                d["_file"] = p.name
                out.append(d)
            except ValueError:
                continue
        return out

    def save_snapshot(self, label: str, rng: str, hosts: dict[str, dict]) -> str:
        ts = datetime.now()
        name = f"stand-{ts:%Y%m%d-%H%M%S}.json"
        (self.dir / name).write_text(json.dumps(
            {"ts": ts.strftime("%d.%m.%Y %H:%M"), "label": label, "range": rng, "hosts": hosts},
            ensure_ascii=False, indent=1), encoding="utf-8")
        return name

    def delete(self, fname: str):
        (self.dir / fname).unlink(missing_ok=True)

    def known(self) -> dict[str, str]:
        try:
            return json.loads(self.known_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}

    def set_known(self, key: str, note: str):
        k = self.known()
        k[key] = note
        self.known_path.write_text(json.dumps(k, ensure_ascii=False, indent=1), encoding="utf-8")


def host_key(h: dict) -> str:
    """Gerät eindeutig über MAC erkennen, sonst über IP."""
    return h.get("mac") or h["ip"]


def compare(old: dict | None, new: dict) -> list[dict]:
    """Vergleich zweier Stände → Liste mit status neu/weg/geändert/gleich."""
    old_hosts = {host_key(h): h for h in (old or {}).get("hosts", {}).values()}
    new_hosts = {host_key(h): h for h in new.get("hosts", {}).values()}
    rows = []
    for k, h in new_hosts.items():
        o = old_hosts.get(k)
        if o is None:
            status, note = ("neu" if old else ""), ""
        else:
            diffs = []
            if o["ip"] != h["ip"]:
                diffs.append(f"IP {o['ip']} → {h['ip']}")
            if set(o.get("ports", [])) != set(h.get("ports", [])):
                diffs.append(f"Ports {o.get('ports', [])} → {h.get('ports', [])}")
            status, note = ("geändert", "; ".join(diffs)) if diffs else ("", "")
        rows.append(dict(h, status=status, note=note, key=k))
    for k, o in old_hosts.items():
        if k not in new_hosts:
            rows.append(dict(o, status="fehlt", note="im neuen Stand nicht gefunden", key=k))
    import ipaddress
    rows.sort(key=lambda r: int(ipaddress.ip_address(r["ip"])))
    return rows


class InventoryPage(QWidget):
    message = Signal(str)
    COLS = ["Status", "IP-Adresse", "Name", "MAC-Adresse", "Hersteller", "Offene Ports", "Bekannt als", "Hinweis"]

    def __init__(self, scan_page, parent=None):
        super().__init__(parent)
        self.store = InventoryStore()
        self.scan_page = scan_page
        self.rows: list[dict] = []
        lay = QVBoxLayout(self)

        bar = QHBoxLayout()
        b = QPushButton("Aktuellen Netzwerkscan als Stand speichern")
        b.clicked.connect(self.save_from_scan)
        bar.addWidget(b)
        bar.addStretch(1)
        lay.addLayout(bar)

        cmp = QHBoxLayout()
        cmp.addWidget(QLabel("Stand:"))
        self.cur = QComboBox()
        cmp.addWidget(self.cur, 1)
        cmp.addWidget(QLabel("vergleichen mit:"))
        self.prev = QComboBox()
        cmp.addWidget(self.prev, 1)
        for c in (self.cur, self.prev):
            c.currentIndexChanged.connect(self.show_compare)
        lay.addLayout(cmp)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        h = self.table.horizontalHeader()
        for c in range(len(self.COLS)):
            h.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(7, QHeaderView.Stretch)
        lay.addWidget(self.table, 1)

        act = QHBoxLayout()
        b = QPushButton("Als bekannt markieren …")
        b.clicked.connect(self.mark_known)
        act.addWidget(b)
        b = QPushButton("Als CSV (Excel) exportieren …")
        b.clicked.connect(self.export_csv)
        act.addWidget(b)
        b = QPushButton("Stand löschen")
        b.clicked.connect(self.delete_snapshot)
        act.addWidget(b)
        act.addStretch(1)
        self.summary = QLabel("")
        act.addWidget(self.summary)
        lay.addLayout(act)
        self.reload()

    def reload(self, select_file: str | None = None):
        self.snaps = self.store.snapshots()
        for c in (self.cur, self.prev):
            c.blockSignals(True)
            c.clear()
        self.prev.addItem("– kein Vergleich –", None)
        for s in self.snaps:
            text = f"{s['ts']}  ·  {s.get('label') or s.get('range', '')}  ·  {len(s['hosts'])} Geräte"
            self.cur.addItem(text, s["_file"])
            self.prev.addItem(text, s["_file"])
        if select_file:
            self.cur.setCurrentIndex(max(0, self.cur.findData(select_file)))
        if len(self.snaps) > 1:
            self.prev.setCurrentIndex(self.cur.currentIndex() + 2)
        for c in (self.cur, self.prev):
            c.blockSignals(False)
        self.show_compare()

    def _snap(self, fname):
        return next((s for s in self.snaps if s["_file"] == fname), None)

    def show_compare(self):
        cur = self._snap(self.cur.currentData())
        if not cur:
            self.table.setRowCount(0)
            self.summary.setText("Noch kein Stand gespeichert – erst Netzwerkscan, dann hier speichern.")
            return
        prev = self._snap(self.prev.currentData())
        self.rows = compare(prev, cur)
        known = self.store.known()
        self.table.setRowCount(len(self.rows))
        for r, h in enumerate(self.rows):
            kn = known.get(h["key"], "")
            hint = h["note"] or ("unbekanntes Gerät" if not kn and h["status"] == "neu" else "")
            vals = [h["status"], h["ip"], h.get("name", ""), h.get("mac", ""), h.get("vendor", ""),
                    ", ".join(map(str, h.get("ports", []))), kn, hint]
            color = {"neu": NEW, "fehlt": GONE, "geändert": CHANGED}.get(h["status"])
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if color and c in (0, 7):
                    it.setForeground(QBrush(color))
                self.table.setItem(r, c, it)
        n = {s: sum(1 for x in self.rows if x["status"] == s) for s in ("neu", "fehlt", "geändert")}
        self.summary.setText(f"{len(cur['hosts'])} Geräte · {n['neu']} neu · {n['fehlt']} fehlen · {n['geändert']} geändert"
                             if prev else f"{len(cur['hosts'])} Geräte")

    def save_from_scan(self):
        data = self.scan_page.data
        if not data:
            QMessageBox.information(self, "Inventar", "Bitte zuerst einen Netzwerkscan durchführen.")
            return
        label, ok = QInputDialog.getText(self, "Stand speichern", "Bezeichnung (z. B. „Hafen Konstanz“):")
        if not ok:
            return
        hosts = {ip: {"ip": ip, "name": h.get("name", ""), "mac": h.get("mac", ""), "vendor": h.get("vendor", ""),
                      "ports": h.get("ports", [])} for ip, h in data.items()}
        fname = self.store.save_snapshot(label.strip(), self.scan_page.target.text(), hosts)
        self.reload(fname)
        self.message.emit(f"Inventar gespeichert: {len(hosts)} Geräte")

    def mark_known(self):
        r = self.table.currentRow()
        if r < 0:
            return
        h = self.rows[r]
        note, ok = QInputDialog.getText(self, "Bekanntes Gerät", f"Was ist {h['ip']} ({h.get('mac') or 'ohne MAC'})?",
                                        text=self.store.known().get(h["key"], h.get("name", "")))
        if ok:
            self.store.set_known(h["key"], note.strip())
            self.show_compare()

    def export_csv(self):
        if not self.rows:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Inventar exportieren",
                                              f"netzwerk-inventar-{datetime.now():%Y-%m-%d}.csv", "CSV (*.csv)")
        if not path:
            return
        known = self.store.known()
        with open(path, "w", newline="", encoding="utf-8-sig") as f:   # BOM → Excel erkennt Umlaute
            w = csv.writer(f, delimiter=";")
            w.writerow(self.COLS)
            for h in self.rows:
                w.writerow([h["status"], h["ip"], h.get("name", ""), h.get("mac", ""), h.get("vendor", ""),
                            ", ".join(f"{p} {net.port_name(p)}".strip() for p in h.get("ports", [])),
                            known.get(h["key"], ""), h["note"]])
        self.message.emit(f"Exportiert: {path}")

    def delete_snapshot(self):
        fname = self.cur.currentData()
        if fname and QMessageBox.question(self, "Löschen", "Diesen Stand löschen?") == QMessageBox.Yes:
            self.store.delete(fname)
            self.reload()
