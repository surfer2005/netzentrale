"""DALI-Bedienelemente (gemeinsam mit der eigenständigen App „DALI Control“)."""
from __future__ import annotations

import traceback
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSlider, QVBoxLayout, QWidget,
)

from .gateway import GatewayError, control_payload

REFRESH_MS = 3000          # Status-Polling
SLIDER_THROTTLE_MS = 180   # max. Senderate beim Ziehen des Reglers

DALI_TYPE_NAMES = {
    0: "Leuchtstofflampe", 1: "Notlicht", 2: "HID", 3: "Halogen NV",
    4: "Glühlampe", 5: "0-10V", 6: "LED", 7: "Relais", 8: "Farbe (DT8)",
}

STYLE = """
QMainWindow, QWidget { font-size: 13px; }
QFrame#card { background: palette(base); border: 1px solid palette(mid); border-radius: 8px; }
QLabel#title { font-weight: 600; font-size: 14px; }
QLabel#sub { color: palette(placeholder-text); font-size: 11px; }
QLabel#pct { min-width: 42px; }
QPushButton#toggle { min-width: 64px; padding: 4px 10px; border-radius: 6px; }
QPushButton#toggle:checked { background: #f5b301; color: #222; font-weight: 600; }
QPushButton#big { padding: 10px 20px; font-size: 14px; }
"""


# ---------------------------------------------------------------------------
# Hintergrund-Ausführung, damit die Oberfläche nie blockiert
# ---------------------------------------------------------------------------
class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Job(QRunnable):
    def __init__(self, fn: Callable, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = _Signals()

    def run(self):
        try:
            self.signals.done.emit(self.fn(*self.args, **self.kwargs))
        except GatewayError as e:
            self.signals.failed.emit(str(e))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.signals.failed.emit(f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# Wiederverwendbares Bedienelement: Ein/Aus + Dimmer + Szene
# ---------------------------------------------------------------------------
class ControlRow(QWidget):
    """Sendet ControlData-Dicts über das Signal `command`."""
    command = Signal(dict)

    def __init__(self, dimmable=True, switchable=True, scenes=True, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        self.toggle = QPushButton("Aus")
        self.toggle.setObjectName("toggle")
        self.toggle.setCheckable(True)
        self.toggle.clicked.connect(self._on_toggle)
        self.toggle.setVisible(switchable or dimmable)
        lay.addWidget(self.toggle)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setMinimumWidth(180)
        self.slider.setVisible(dimmable)
        self.pct = QLabel("–")
        self.pct.setObjectName("pct")
        self.pct.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.pct.setVisible(dimmable)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.pct)

        self.scene = QComboBox()
        self.scene.addItem("Szene …", None)
        for i in range(16):
            self.scene.addItem(f"Szene {i}", i)
        self.scene.setVisible(scenes)
        self.scene.activated.connect(self._on_scene)
        lay.addWidget(self.scene)

        # Throttling: beim Ziehen höchstens alle SLIDER_THROTTLE_MS senden
        self._pending: int | None = None
        self._throttle = QTimer(self, singleShot=True, interval=SLIDER_THROTTLE_MS)
        self._throttle.timeout.connect(self._flush)
        self.slider.valueChanged.connect(self._on_slide)
        self.slider.sliderReleased.connect(self._flush)
        self._updating = False

    # -- Benutzeraktionen --
    def _on_toggle(self, checked: bool):
        self.toggle.setText("Ein" if checked else "Aus")
        self.command.emit(control_payload(on=checked))

    def _on_slide(self, v: int):
        self.pct.setText(f"{v} %")
        if self._updating:
            return
        self._set_toggle(v > 0)
        self._pending = v
        if not self._throttle.isActive():
            self._throttle.start()

    def _flush(self):
        if self._pending is not None:
            v, self._pending = self._pending, None
            self.command.emit(control_payload(level=v))

    def _on_scene(self, idx: int):
        nr = self.scene.itemData(idx)
        self.scene.setCurrentIndex(0)
        if nr is not None:
            self.command.emit(control_payload(scene=nr))

    # -- Status vom Gateway übernehmen (ohne erneut zu senden) --
    def _set_toggle(self, on: bool):
        self.toggle.blockSignals(True)
        self.toggle.setChecked(on)
        self.toggle.setText("Ein" if on else "Aus")
        self.toggle.blockSignals(False)

    def show_state(self, on: bool | None, level: float | None):
        if self.slider.isSliderDown() or self._pending is not None:
            return  # Benutzer bedient gerade – nicht überschreiben
        self._updating = True
        if level is not None:
            self.slider.setValue(round(level))
            self.pct.setText(f"{round(level)} %")
        if on is not None:
            self._set_toggle(on)
        elif level is not None:
            self._set_toggle(level > 0)
        self._updating = False


def card(*widgets_or_layouts) -> QFrame:
    f = QFrame()
    f.setObjectName("card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(12, 10, 12, 10)
    for w in widgets_or_layouts:
        (lay.addLayout if hasattr(w, "addWidget") and not isinstance(w, QWidget) else lay.addWidget)(w)
    return f


def scroll_page() -> tuple[QScrollArea, QVBoxLayout]:
    inner = QWidget()
    lay = QVBoxLayout(inner)
    lay.setSpacing(8)
    lay.addStretch(1)
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.NoFrame)
    sa.setWidget(inner)
    return sa, lay


# ---------------------------------------------------------------------------
# Hauptfenster
# ---------------------------------------------------------------------------
