"""Seite „Fernhilfe“: RustDesk-Verbindung per ID, Einladung für das Gegenüber,
Prüfung des eigenen RustDesk-Servers."""
from __future__ import annotations

from urllib.parse import quote

from PySide6.QtCore import QSettings, QThread, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from . import launch, net
from .storage import Device

DEFAULT_DOWNLOAD = "https://github.com/rustdesk/rustdesk/releases/latest"
SERVER_PORTS = [(21115, "NAT-Test"), (21116, "ID-Server"), (21117, "Relay")]
MAX_RECENT = 15


class ServerCheck(QThread):
    result = Signal(list)

    def __init__(self, host: str, parent=None):
        super().__init__(parent)
        self.host = host

    def run(self):
        self.result.emit([(p, name, net.tcp_check(self.host, p, 2.0)) for p, name in SERVER_PORTS])


class SupportPage(QWidget):
    add_device = Signal(object)
    message = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings = QSettings("BSB", "Netzentrale")
        self._check: ServerCheck | None = None
        lay = QVBoxLayout(self)

        # --- 1. Verbinden ---
        box = QGroupBox("Mit Gegenüber verbinden")
        v = QVBoxLayout(box)
        row = QHBoxLayout()
        row.addWidget(QLabel("RustDesk-ID:"))
        self.rid = QLineEdit()
        self.rid.setPlaceholderText("ID, die das Gegenüber im RustDesk-Fenster sieht, z. B. 123 456 789")
        self.rid.returnPressed.connect(self.connect_id)
        row.addWidget(self.rid, 1)
        self.mode = QComboBox()
        for k, label in launch.RUSTDESK_MODES.items():
            self.mode.addItem(label, k)
        row.addWidget(self.mode)
        b = QPushButton("Verbinden")
        b.setDefault(True)
        b.clicked.connect(self.connect_id)
        row.addWidget(b)
        b = QPushButton("Als Gerät speichern")
        b.clicked.connect(self.save_device)
        row.addWidget(b)
        v.addLayout(row)
        v.addWidget(QLabel("Zuletzt verwendet (Doppelklick verbindet):"))
        self.recent = QListWidget()
        self.recent.setMaximumHeight(80)
        self.recent.itemDoubleClicked.connect(lambda it: (self.rid.setText(it.data(Qt.UserRole)), self.connect_id()))
        v.addWidget(self.recent)
        lay.addWidget(box)

        # --- 2. Server ---
        box = QGroupBox("Eigener RustDesk-Server")
        f = QFormLayout(box)
        self.server = QLineEdit(self.settings.value("rustdesk/server", ""))
        self.server.setPlaceholderText("z. B. fernhilfe.stadtwerke-konstanz.de – leer = öffentliche RustDesk-Server")
        self.key = QLineEdit(self.settings.value("rustdesk/key", ""))
        self.key.setPlaceholderText("Inhalt von id_ed25519.pub vom Server")
        self.download = QLineEdit(self.settings.value("rustdesk/download", DEFAULT_DOWNLOAD))
        for w in (self.server, self.key, self.download):
            w.editingFinished.connect(self._save_settings)
        chk = QHBoxLayout()
        self.server_state = QLabel("")
        bt = QPushButton("Server prüfen")
        bt.clicked.connect(self.check_server)
        chk.addWidget(bt)
        chk.addWidget(self.server_state, 1)
        f.addRow("Server-Adresse:", self.server)
        f.addRow("Schlüssel (Key):", self.key)
        f.addRow("Download-Link:", self.download)
        f.addRow("", chk)
        lay.addWidget(box)

        # --- 3. Einladung ---
        box = QGroupBox("Einladung für das Gegenüber")
        v = QVBoxLayout(box)
        nr = QHBoxLayout()
        nr.addWidget(QLabel("Name des Gegenübers (optional):"))
        self.person = QLineEdit()
        self.person.textChanged.connect(self.update_invite)
        nr.addWidget(self.person, 1)
        v.addLayout(nr)
        self.invite = QPlainTextEdit()
        self.invite.setMinimumHeight(170)
        v.addWidget(self.invite, 1)
        br = QHBoxLayout()
        b = QPushButton("Text kopieren")
        b.clicked.connect(self.copy_invite)
        br.addWidget(b)
        b = QPushButton("Per E-Mail senden …")
        b.clicked.connect(self.mail_invite)
        br.addWidget(b)
        br.addStretch(1)
        v.addLayout(br)
        lay.addWidget(box, 1)

        self._load_recent()
        self.update_invite()
        for w in (self.server, self.key, self.download):
            w.textChanged.connect(self.update_invite)

    # -- Einstellungen / Verlauf --
    def _save_settings(self):
        self.settings.setValue("rustdesk/server", self.server.text().strip())
        self.settings.setValue("rustdesk/key", self.key.text().strip())
        self.settings.setValue("rustdesk/download", self.download.text().strip() or DEFAULT_DOWNLOAD)

    def _recent_list(self) -> list[str]:
        val = self.settings.value("rustdesk/recent", [])
        return [val] if isinstance(val, str) else list(val or [])

    def _load_recent(self):
        self.recent.clear()
        for rid in self._recent_list():
            from PySide6.QtWidgets import QListWidgetItem
            it = QListWidgetItem(" ".join(rid[i:i + 3] for i in range(0, len(rid), 3)) if rid.isdigit() else rid)
            it.setData(Qt.UserRole, rid)
            self.recent.addItem(it)

    def _remember(self, rid: str):
        lst = [r for r in self._recent_list() if r != rid]
        self.settings.setValue("rustdesk/recent", [rid] + lst[:MAX_RECENT - 1])
        self._load_recent()

    # -- Aktionen --
    def connect_id(self):
        rid = launch.clean_rustdesk_id(self.rid.text())
        try:
            launch.open_rustdesk_id(rid, self.mode.currentData())
        except launch.LaunchError as e:
            QMessageBox.warning(self, "Fernhilfe", str(e))
            return
        self._remember(rid)
        self.message.emit(f"RustDesk: {self.mode.currentText()} mit {rid} …")

    def save_device(self):
        rid = launch.clean_rustdesk_id(self.rid.text())
        if rid:
            self.add_device.emit(Device(name="", host="", group="Fernhilfe", rustdesk={"id": rid}))

    def check_server(self):
        host = self.server.text().strip()
        if not host:
            self.server_state.setText("Kein eigener Server eingetragen – es werden die öffentlichen RustDesk-Server genutzt.")
            return
        self._save_settings()
        self.server_state.setText("prüfe …")
        self._check = ServerCheck(host, self)
        self._check.result.connect(self._show_check)
        self._check.start()

    def _show_check(self, res):
        parts = [f"{'✅' if ms is not None else '❌'} {p} {name}" for p, name, ms in res]
        ok = all(ms is not None for _, _, ms in res)
        self.server_state.setText("   ".join(parts) + ("" if ok else "   → Firewall/Portfreigabe prüfen"))

    # -- Einladungstext --
    def invite_text(self) -> str:
        name = self.person.text().strip()
        server, key = self.server.text().strip(), self.key.text().strip()
        dl = self.download.text().strip() or DEFAULT_DOWNLOAD
        lines = [f"Hallo {name}," if name else "Hallo,", "",
                 "damit ich dir am Rechner helfen kann, bitte einmal kurz Folgendes machen:", "",
                 f"1. RustDesk herunterladen und öffnen: {dl}",
                 "   (Mac: die .dmg-Datei für Apple Silicon oder Intel, RustDesk in „Programme“ ziehen und öffnen.",
                 "    Windows: die .exe starten – eine Installation ist nicht nötig.)"]
        if server:
            lines += ["",
                      "2. Einmalig unseren Server eintragen: im RustDesk-Fenster auf die drei Punkte neben der ID",
                      "   → Netzwerk → „ID/Relay-Server“ und dort eintragen:",
                      f"   ID-Server:  {server}",
                      f"   Relay-Server: {server}",
                      f"   Key:  {key or '(bitte nachfragen)'}",
                      "   (API-Server bleibt leer.) Mit OK bestätigen."]
            n = 3
        else:
            n = 2
        lines += ["",
                  f"{n}. Mir am Telefon die angezeigte ID und das Einmal-Passwort durchgeben.", "",
                  "Auf dem Mac fragt macOS beim ersten Mal nach den Berechtigungen „Bildschirmaufnahme“ und",
                  "„Bedienungshilfen“ – bitte beide in den Systemeinstellungen für RustDesk erlauben,",
                  "sonst kann ich den Bildschirm nicht sehen bzw. nicht bedienen.", "",
                  "Die Verbindung ist nur möglich, solange RustDesk bei dir geöffnet ist und du mir das Passwort gibst.",
                  "Du siehst jederzeit, dass ich verbunden bin, und kannst die Sitzung mit einem Klick beenden.", "",
                  "Danke!"]
        return "\n".join(lines)

    def update_invite(self):
        self.invite.setPlainText(self.invite_text())

    def copy_invite(self):
        QGuiApplication.clipboard().setText(self.invite.toPlainText())
        self.message.emit("Einladungstext kopiert")

    def mail_invite(self):
        url = "mailto:?subject=" + quote("Fernhilfe – kurze Vorbereitung") + "&body=" + quote(self.invite.toPlainText())
        QDesktopServices.openUrl(QUrl(url))
