"""Seite „Verbindungen“: Geräteliste mit Online-Status und Ein-Klick-Verbindung."""
from __future__ import annotations

import copy

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from . import launch, vault
from .storage import (DEFAULT_VNC_CLIENT, FILE_PROTOCOLS, SERVICE_LABEL, SERVICE_ORDER, VNC_CLIENTS, Device,
                      DeviceStore)
from .workers import StatusWorker

STATUS_INTERVAL_MS = 30_000
CLIPBOARD_CLEAR_S = 30
GREEN, RED, GREY = QColor("#2e9d4f"), QColor("#c94040"), QColor("#9a9a9a")
PW_SERVICES = ("vnc", "ssh", "web", "rdp", "rustdesk", "files")
PW_LABEL = {"vnc": "VNC", "ssh": "SSH", "web": "Web-Anmeldung", "rdp": "RDP", "rustdesk": "RustDesk",
            "files": "Dateifreigabe"}


# ---------------------------------------------------------------------------
class PasswordField(QWidget):
    """Passwortfeld mit Anzeigen-Knopf; zeigt an, ob schon etwas im Schlüsselbund liegt."""

    def __init__(self, dev_id: str, svc: str, parent=None):
        super().__init__(parent)
        self.dev_id, self.svc = dev_id, svc
        self.existing = vault.get(dev_id, svc) if dev_id else None
        self.cleared = False
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.Password)
        self.edit.setPlaceholderText("im Schlüsselbund gespeichert – leer lassen = unverändert"
                                     if self.existing else "optional – wird im Schlüsselbund gespeichert")
        h.addWidget(self.edit, 1)
        eye = QPushButton("👁")
        eye.setFixedWidth(34)
        eye.setCheckable(True)
        eye.toggled.connect(self._toggle)
        h.addWidget(eye)
        if self.existing:
            rm = QPushButton("Entfernen")
            rm.clicked.connect(self._remove)
            h.addWidget(rm)

    def _toggle(self, on):
        if on and not self.edit.text() and self.existing:
            self.edit.setText(self.existing)
        self.edit.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password)

    def _remove(self):
        self.cleared = True
        self.edit.clear()
        self.edit.setPlaceholderText("wird beim Speichern entfernt")

    def commit(self, dev_id: str, active: bool):
        if not active or self.cleared:
            vault.delete(dev_id, self.svc)
        if active and self.edit.text():
            vault.put(dev_id, self.svc, self.edit.text())


class DeviceDialog(QDialog):
    def __init__(self, groups: list[str], dev: Device | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Gerät bearbeiten" if dev and dev.name else "Neues Gerät")
        self.setMinimumWidth(560)
        self.dev = dev or Device(name="", host="")
        d = self.dev
        self.pw: dict[str, PasswordField] = {}
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs)

        # --- Allgemein ---
        w = QWidget()
        form = QFormLayout(w)
        self.name = QLineEdit(d.name)
        self.host = QLineEdit(d.host)
        self.host.setPlaceholderText("IP-Adresse oder Hostname, z. B. 192.168.1.20 oder bordwaechter-01.local")
        self.group = QComboBox()
        self.group.setEditable(True)
        self.group.addItems(sorted(set(groups) | {"Allgemein"}, key=str.lower))
        self.group.setCurrentText(d.group or "Allgemein")
        self.mac = QLineEdit(d.mac)
        self.mac.setPlaceholderText("optional, für Wake-on-LAN – z. B. 3C:22:FB:12:34:56")
        self.note = QLineEdit(d.note)
        form.addRow("Name:", self.name)
        form.addRow("Adresse:", self.host)
        form.addRow("Gruppe:", self.group)
        form.addRow("MAC-Adresse:", self.mac)
        form.addRow("Notiz:", self.note)
        tabs.addTab(w, "Allgemein")

        # --- Fernzugriff ---
        w = QWidget()
        v = QVBoxLayout(w)
        self.vnc_box, f = self._section("VNC (Bildschirm)", d.vnc)
        self.vnc_port = self._port(d.vnc, 5900)
        self.vnc_client = QComboBox()
        for k, label in VNC_CLIENTS.items():
            self.vnc_client.addItem(label, k)
        self.vnc_client.setCurrentIndex(max(0, self.vnc_client.findData((d.vnc or {}).get("client", DEFAULT_VNC_CLIENT))))
        f.addRow("Port:", self.vnc_port)
        f.addRow("Client:", self.vnc_client)
        f.addRow("Passwort:", self._pw("vnc"))
        v.addWidget(self.vnc_box)

        self.ssh_box, f = self._section("SSH (Terminal)", d.ssh)
        self.ssh_port = self._port(d.ssh, 22)
        self.ssh_user = QLineEdit((d.ssh or {}).get("user", ""))
        self.ssh_user.setPlaceholderText("z. B. pi oder admin")
        f.addRow("Port:", self.ssh_port)
        f.addRow("Benutzer:", self.ssh_user)
        f.addRow("Passwort:", self._pw("ssh"))
        v.addWidget(self.ssh_box)

        self.rdp_box, f = self._section("RDP (Windows)", d.rdp)
        self.rdp_port = self._port(d.rdp, 3389)
        self.rdp_user = QLineEdit((d.rdp or {}).get("user", ""))
        self.rdp_user.setPlaceholderText("optional, z. B. BSB\\pbode")
        f.addRow("Port:", self.rdp_port)
        f.addRow("Benutzer:", self.rdp_user)
        f.addRow("Passwort:", self._pw("rdp"))
        v.addWidget(self.rdp_box)

        self.rd_box, f = self._section("RustDesk (Fernhilfe übers Internet)", d.rustdesk)
        self.rd_id = QLineEdit((d.rustdesk or {}).get("id", ""))
        self.rd_id.setPlaceholderText("ID aus dem RustDesk-Fenster des Gegenübers, z. B. 123 456 789")
        f.addRow("RustDesk-ID:", self.rd_id)
        f.addRow("Festes Passwort:", self._pw("rustdesk"))
        v.addWidget(self.rd_box)

        self.dali_box, f = self._section("DALI-Gateway (Lunatone DALI-2 IoT)", d.dali)
        f.addRow(QLabel("Leuchten und Gruppen dieses Gateways in der Netzentrale steuern (Seite „DALI-Licht“)."))
        v.addWidget(self.dali_box)
        v.addStretch(1)
        tabs.addTab(w, "Fernzugriff")

        # --- Web & Links ---
        w = QWidget()
        v = QVBoxLayout(w)
        self.web_box, f = self._section("Hauptseite (Doppelklick in der Liste)", d.web)
        self.web_url = QLineEdit((d.web or {}).get("url", ""))
        self.web_url.setPlaceholderText("leer = http://<Adresse>")
        self.web_user = QLineEdit((d.web or {}).get("user", ""))
        self.web_user.setPlaceholderText("optional")
        f.addRow("URL:", self.web_url)
        f.addRow("Benutzer:", self.web_user)
        f.addRow("Passwort:", self._pw("web"))
        v.addWidget(self.web_box)
        v.addWidget(QLabel("Weitere Links (z. B. Grafana, Logs, Router-Oberfläche) – erscheinen im Rechtsklick-Menü:"))
        self.links = QTableWidget(0, 2)
        self.links.setHorizontalHeaderLabels(["Bezeichnung", "URL"])
        self.links.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.links.verticalHeader().setVisible(False)
        for ln in d.links or []:
            self._add_link_row(ln.get("name", ""), ln.get("url", ""))
        v.addWidget(self.links, 1)
        h = QHBoxLayout()
        b = QPushButton("+ Link")
        b.clicked.connect(lambda: self._add_link_row("", ""))
        h.addWidget(b)
        b = QPushButton("Link entfernen")
        b.clicked.connect(lambda: self.links.removeRow(self.links.currentRow()) if self.links.currentRow() >= 0 else None)
        h.addWidget(b)
        h.addStretch(1)
        v.addLayout(h)
        tabs.addTab(w, "Web && Links")

        # --- Dateien ---
        w = QWidget()
        v = QVBoxLayout(w)
        self.files_box, f = self._section("Dateizugriff", d.files)
        self.files_proto = QComboBox()
        for k, label in FILE_PROTOCOLS.items():
            self.files_proto.addItem(label, k)
        self.files_proto.setCurrentIndex(max(0, self.files_proto.findData((d.files or {}).get("proto", "smb"))))
        self.files_share = QLineEdit((d.files or {}).get("share", ""))
        self.files_share.setPlaceholderText("Freigabe bzw. Ordner, z. B. daten oder home/pi")
        self.files_user = QLineEdit((d.files or {}).get("user", ""))
        f.addRow("Protokoll:", self.files_proto)
        f.addRow("Freigabe/Pfad:", self.files_share)
        f.addRow("Benutzer:", self.files_user)
        f.addRow("Passwort:", self._pw("files"))
        v.addWidget(self.files_box)
        hint = QLabel("SMB öffnet im Finder. SFTP öffnet Cyberduck/Transmit/FileZilla, falls installiert, "
                      "sonst sftp im Terminal.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        v.addWidget(hint)
        v.addStretch(1)
        tabs.addTab(w, "Dateien")

        note = QLabel("🔒 Passwörter liegen verschlüsselt im macOS-Schlüsselbund, nicht in der Geräteliste "
                      "und nicht im Export.")
        note.setStyleSheet("color: gray; font-size: 11px;")
        note.setWordWrap(True)
        lay.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setText("Speichern")
        bb.button(QDialogButtonBox.Cancel).setText("Abbrechen")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _pw(self, svc):
        self.pw[svc] = PasswordField(self.dev.id, svc)
        return self.pw[svc]

    def _add_link_row(self, name, url):
        r = self.links.rowCount()
        self.links.insertRow(r)
        self.links.setItem(r, 0, QTableWidgetItem(name))
        self.links.setItem(r, 1, QTableWidgetItem(url))

    @staticmethod
    def _section(title, cfg):
        box = QGroupBox(title)
        box.setCheckable(True)
        box.setChecked(cfg is not None)
        return box, QFormLayout(box)

    @staticmethod
    def _port(cfg, default):
        sb = QSpinBox()
        sb.setRange(1, 65535)
        sb.setValue(int((cfg or {}).get("port", default)))
        return sb

    def _accept(self):
        from .ui_extra import normalize_mac
        rid = launch.clean_rustdesk_id(self.rd_id.text()) if self.rd_box.isChecked() else ""
        if self.rd_box.isChecked() and not rid:
            QMessageBox.warning(self, "RustDesk-ID fehlt", "Bitte die RustDesk-ID eintragen oder RustDesk abwählen.")
            return
        local = any(b.isChecked() for b in (self.vnc_box, self.ssh_box, self.web_box, self.rdp_box, self.files_box,
                                            self.dali_box))
        has_links = any((self.links.item(r, 1) and self.links.item(r, 1).text().strip()) for r in range(self.links.rowCount()))
        if not self.host.text().strip() and (local or not (rid or has_links)):
            QMessageBox.warning(self, "Adresse fehlt", "Bitte eine IP-Adresse oder einen Hostnamen eintragen "
                                "(nur bei reinen RustDesk- oder Link-Einträgen darf sie leer bleiben).")
            return
        mac = self.mac.text().strip()
        if mac and not normalize_mac(mac):
            QMessageBox.warning(self, "MAC-Adresse", "Die MAC-Adresse ist ungültig (12 Hex-Zeichen).")
            return
        d = self.dev
        d.host = self.host.text().strip()
        d.name = self.name.text().strip() or d.host or (f"RustDesk {rid}" if rid else "Links")
        d.group = self.group.currentText().strip() or "Allgemein"
        d.note = self.note.text().strip()
        d.mac = normalize_mac(mac) if mac else ""
        d.vnc = {"port": self.vnc_port.value(), "client": self.vnc_client.currentData()} if self.vnc_box.isChecked() else None
        d.ssh = {"port": self.ssh_port.value(), "user": self.ssh_user.text().strip()} if self.ssh_box.isChecked() else None
        d.web = {"url": self.web_url.text().strip(), "user": self.web_user.text().strip()} if self.web_box.isChecked() else None
        d.rdp = {"port": self.rdp_port.value(), "user": self.rdp_user.text().strip()} if self.rdp_box.isChecked() else None
        d.rustdesk = {"id": rid} if rid else None
        d.dali = {"enabled": True} if self.dali_box.isChecked() else None
        d.files = ({"proto": self.files_proto.currentData(), "share": self.files_share.text().strip(),
                    "user": self.files_user.text().strip()} if self.files_box.isChecked() else None)
        d.links = []
        for r in range(self.links.rowCount()):
            url = (self.links.item(r, 1).text() if self.links.item(r, 1) else "").strip()
            name = (self.links.item(r, 0).text() if self.links.item(r, 0) else "").strip()
            if url:
                d.links.append({"name": name or url, "url": url})
        try:
            for svc, field in self.pw.items():
                field.commit(d.id, bool(getattr(d, svc)))
        except vault.SecretError as e:
            QMessageBox.warning(self, "Schlüsselbund", f"Passwort konnte nicht gespeichert werden:\n{e}")
        self.accept()


# ---------------------------------------------------------------------------
class DevicesPage(QWidget):
    request_portscan = Signal(str)
    request_ping = Signal(str)
    request_monitor = Signal(object)
    request_dali = Signal(str)
    request_security = Signal(str)
    message = Signal(str)

    def __init__(self, store: DeviceStore, parent=None):
        super().__init__(parent)
        self.store = store
        self.status: dict[str, float | None] = {}
        self._worker: StatusWorker | None = None
        self._clip_token = None

        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Suchen (Name, Adresse, Gruppe, Notiz) …")
        self.search.textChanged.connect(self.rebuild)
        bar.addWidget(self.search, 1)
        for text, slot in (("+ Neu", self.add), ("Bearbeiten", self.edit), ("Löschen", self.delete)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            bar.addWidget(b)
        lay.addLayout(bar)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Adresse", "Dienste", "Status", "Notiz"])
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)
        hdr = self.tree.header()
        for c in (0, 1, 2, 3):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.Stretch)
        self.tree.itemDoubleClicked.connect(lambda it, _c: self.connect_selected())
        self.tree.itemSelectionChanged.connect(self._update_buttons)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        lay.addWidget(self.tree, 1)

        act = QHBoxLayout()
        self.svc_buttons = {}
        for s in SERVICE_ORDER:
            b = QPushButton(SERVICE_LABEL[s])
            b.setToolTip(f"{SERVICE_LABEL[s]} öffnen")
            b.clicked.connect(lambda _=False, svc=s: self.connect_selected(svc))
            act.addWidget(b)
            self.svc_buttons[s] = b
        self.btn_links = QPushButton("Links ▾")
        self.btn_links.clicked.connect(self._links_menu)
        act.addWidget(self.btn_links)
        self.btn_pw = QPushButton("Passwort kopieren")
        self.btn_pw.clicked.connect(lambda: self.copy_password())
        act.addWidget(self.btn_pw)
        act.addStretch(1)
        b = QPushButton("Status prüfen")
        b.clicked.connect(self.check_status)
        act.addWidget(b)
        lay.addLayout(act)

        self.timer = QTimer(self, interval=STATUS_INTERVAL_MS)
        self.timer.timeout.connect(self.check_status)
        self.timer.start()
        self.rebuild()
        QTimer.singleShot(300, self.check_status)

    # -- Anzeige --
    def rebuild(self):
        sel = self.selected()
        q = self.search.text().strip().lower()
        self.tree.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        for d in sorted(self.store.devices, key=lambda x: ((x.group or "").lower(), x.name.lower())):
            rid = (d.rustdesk or {}).get("id", "")
            if q and q not in f"{d.name} {d.host} {d.group} {d.note} {rid} {d.mac}".lower():
                continue
            g = d.group or "Allgemein"
            if g not in groups:
                gi = QTreeWidgetItem([g])
                f = gi.font(0)
                f.setBold(True)
                gi.setFont(0, f)
                gi.setFlags(Qt.ItemIsEnabled)
                self.tree.addTopLevelItem(gi)
                gi.setExpanded(True)
                groups[g] = gi
            svcs = [SERVICE_LABEL[s] for s in d.services()] + ([f"{len(d.links)} Links"] if d.links else [])
            it = QTreeWidgetItem([d.name, d.host or (f"RustDesk-ID {rid}" if rid else "–"), " · ".join(svcs), "", d.note])
            it.setData(0, Qt.UserRole, d.id)
            groups[g].addChild(it)
            self._paint_status(it, d.id)
            if sel and sel.id == d.id:
                it.setSelected(True)
        if not self.store.devices:
            hint = QTreeWidgetItem(["Noch keine Geräte – „+ Neu“, Netzwerkscan oder Ablage → Importieren."])
            hint.setFlags(Qt.NoItemFlags)
            self.tree.addTopLevelItem(hint)
        self._update_buttons()

    def _paint_status(self, it: QTreeWidgetItem, dev_id: str):
        dev = next((x for x in self.store.devices if x.id == dev_id), None)
        if dev and not dev.host:
            it.setText(3, "übers Internet" if dev.rustdesk else "")
            it.setForeground(3, QBrush(GREY))
            return
        if dev_id not in self.status:
            it.setText(3, "…")
            it.setForeground(3, QBrush(GREY))
            return
        ms = self.status[dev_id]
        it.setText(3, f"● online ({ms:.0f} ms)" if ms is not None else "● offline")
        it.setForeground(3, QBrush(GREEN if ms is not None else RED))

    def _items(self):
        for i in range(self.tree.topLevelItemCount()):
            g = self.tree.topLevelItem(i)
            for j in range(g.childCount()):
                yield g.child(j)

    def selected(self) -> Device | None:
        items = self.tree.selectedItems()
        if not items:
            return None
        dev_id = items[0].data(0, Qt.UserRole)
        return next((d for d in self.store.devices if d.id == dev_id), None)

    def _update_buttons(self):
        d = self.selected()
        for s, b in self.svc_buttons.items():
            b.setEnabled(bool(d and getattr(d, s)))
        self.btn_links.setEnabled(bool(d and d.links))
        self.btn_pw.setEnabled(bool(d))

    # -- Status --
    def check_status(self):
        if (self._worker and self._worker.isRunning()) or not self.store.devices:
            return
        self._worker = StatusWorker(self.store.devices, self)
        self._worker.status.connect(self._on_status)
        self._worker.start()

    def _on_status(self, dev_id: str, ms):
        self.status[dev_id] = ms
        for it in self._items():
            if it.data(0, Qt.UserRole) == dev_id:
                self._paint_status(it, dev_id)

    # -- Aktionen --
    def connect_selected(self, service: str | None = None):
        d = self.selected()
        if not d:
            return
        service = service or d.default_service()
        if not service and d.links:
            launch.open_link(d.links[0]["url"])
            return
        if service == "dali":
            self.request_dali.emit(d.host)
            return
        try:
            launch.connect(d, service)
            msg = f"Verbinde mit {d.name} ({SERVICE_LABEL[service]}) …"
            # Passwort für Dienste, die es nicht selbst übernehmen können, in die Zwischenablage
            if service in ("vnc", "ssh", "web", "rdp", "files") and vault.get(d.id, service):
                self.copy_password(d, service, quiet=True)
                msg += f"  Passwort liegt {CLIPBOARD_CLEAR_S} s in der Zwischenablage (⌘V)."
            self.message.emit(msg)
        except launch.LaunchError as e:
            QMessageBox.warning(self, "Verbindung nicht möglich", str(e))

    def copy_password(self, d: Device | None = None, svc: str | None = None, quiet=False):
        d = d or self.selected()
        if not d:
            return
        if svc is None:
            avail = [s for s in PW_SERVICES if vault.get(d.id, s)]
            if not avail:
                self.message.emit(f"Für „{d.name}“ ist kein Passwort gespeichert.")
                return
            if len(avail) > 1:
                m = QMenu(self)
                for s in avail:
                    m.addAction(PW_LABEL[s], lambda s=s: self.copy_password(d, s))
                m.exec(self.btn_pw.mapToGlobal(self.btn_pw.rect().bottomLeft()))
                return
            svc = avail[0]
        pw = vault.get(d.id, svc)
        if not pw:
            return
        cb = QGuiApplication.clipboard()
        cb.setText(pw)
        token = object()
        self._clip_token = token

        def clear():
            if self._clip_token is token and cb.text() == pw:
                cb.clear()
        QTimer.singleShot(CLIPBOARD_CLEAR_S * 1000, clear)
        if not quiet:
            self.message.emit(f"{PW_LABEL[svc]}-Passwort von {d.name} kopiert – wird nach {CLIPBOARD_CLEAR_S} s gelöscht.")

    def _links_menu(self):
        d = self.selected()
        if not d or not d.links:
            return
        m = QMenu(self)
        for ln in d.links:
            m.addAction(ln["name"], lambda u=ln["url"]: self._open_link(u))
        m.exec(self.btn_links.mapToGlobal(self.btn_links.rect().bottomLeft()))

    def _open_link(self, url):
        try:
            launch.open_link(url)
        except launch.LaunchError as e:
            QMessageBox.warning(self, "Link", str(e))

    def add(self, prefill: Device | None = None):
        dlg = DeviceDialog(self.store.groups(), prefill, self)
        if dlg.exec():
            self.store.upsert(dlg.dev)
            self.status.pop(dlg.dev.id, None)
            self.rebuild()
            self.check_status()

    def edit(self):
        d = self.selected()
        if not d:
            return
        dlg = DeviceDialog(self.store.groups(), copy.deepcopy(d), self)
        if dlg.exec():
            self.store.upsert(dlg.dev)
            self.status.pop(d.id, None)
            self.rebuild()
            self.check_status()

    def delete(self):
        d = self.selected()
        if d and QMessageBox.question(self, "Löschen", f"„{d.name}“ aus der Liste entfernen?\n"
                                      "Gespeicherte Passwörter werden ebenfalls entfernt.") == QMessageBox.Yes:
            vault.delete_all(d.id, PW_SERVICES)
            self.store.remove(d.id)
            self.rebuild()

    def _wake(self, d: Device):
        from .ui_extra import send_wol
        try:
            send_wol(d.mac)
            self.message.emit(f"Wake-on-LAN an {d.name} ({d.mac}) gesendet")
        except (ValueError, OSError) as e:
            self.message.emit(f"Wake-on-LAN fehlgeschlagen: {e}")

    def _context_menu(self, pos):
        d = self.selected()
        if not d:
            return
        m = QMenu(self)
        for s in d.services():
            m.addAction(f"{SERVICE_LABEL[s]} öffnen", lambda svc=s: self.connect_selected(svc))
        if d.links:
            sub = m.addMenu("Links")
            for ln in d.links:
                sub.addAction(ln["name"], lambda u=ln["url"]: self._open_link(u))
        pws = [s for s in PW_SERVICES if vault.get(d.id, s)]
        if pws:
            sub = m.addMenu("Passwort kopieren")
            for s in pws:
                sub.addAction(PW_LABEL[s], lambda s=s: self.copy_password(d, s))
        m.addSeparator()
        if d.host:
            m.addAction("Ping", lambda: self.request_ping.emit(d.host))
            m.addAction("Portscan", lambda: self.request_portscan.emit(d.host))
            m.addAction("Sicherheits-Check", lambda: self.request_security.emit(d.host))
            m.addAction("Überwachen", lambda: self.request_monitor.emit(d))
            m.addAction("Adresse kopieren", lambda: QGuiApplication.clipboard().setText(d.host))
        if d.mac:
            m.addAction("Aufwecken (Wake-on-LAN)", lambda: self._wake(d))
        m.addSeparator()
        m.addAction("Bearbeiten …", self.edit)
        m.addAction("Duplizieren …", lambda: self._duplicate(d))
        m.addAction("Löschen", self.delete)
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def _duplicate(self, d: Device):
        """Praktisch für viele gleichartige Geräte (z. B. Bordwächter): nur Name/Adresse anpassen."""
        import uuid
        c = copy.deepcopy(d)
        c.id = uuid.uuid4().hex[:12]
        c.name = d.name + " (Kopie)"
        c.mac = ""
        self.add(c)
