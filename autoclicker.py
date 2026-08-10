"""Modern autoclicker — PySide6 UI with pynput for clicking and hotkeys."""

import sys
import threading
import time
from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QFont, QIcon, QPalette, QColor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from pynput import keyboard
from pynput.mouse import Button, Controller


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MOUSE_BUTTONS: dict[str, Button] = {
    "Left": Button.left,
    "Right": Button.right,
    "Middle": Button.middle,
}


def key_to_label(key) -> str | None:
    """Convert a pynput key object to a human-readable label."""
    if key is None:
        return None
    if hasattr(key, "char") and key.char:
        return key.char.upper()
    if hasattr(key, "name") and key.name:
        name = key.name.replace("_", " ").title()
        return name
    return str(key)


def label_to_key(label: str):
    """Best-effort reverse mapping for common keys stored as labels."""
    if len(label) == 1:
        return keyboard.KeyCode.from_char(label.lower())
    name = label.lower().replace(" ", "_")
    try:
        return keyboard.Key[name]
    except KeyError:
        return None


@dataclass
class ClickerState:
    active: bool = False
    cps: int = 10
    button: Button = Button.left
    hotkey_label: str = "F6"
    hotkey: object = keyboard.Key.f6


# ---------------------------------------------------------------------------
# Thread-safe bridge between pynput listeners and Qt UI
# ---------------------------------------------------------------------------

class Signaler(QObject):
    toggled = Signal(bool)
    hotkey_captured = Signal(str)
    status = Signal(str)


# ---------------------------------------------------------------------------
# Autoclicker engine (runs in background threads)
# ---------------------------------------------------------------------------

class AutoClickerEngine:
    def __init__(self, signaler: Signaler):
        self.signaler = signaler
        self.state = ClickerState()
        self._mouse = Controller()
        self._click_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._kb_listener: keyboard.Listener | None = None
        self._capture_listener: keyboard.Listener | None = None
        self._capturing = False
        self._lock = threading.Lock()

    # -- public API called from UI thread -----------------------------------

    def set_cps(self, cps: int) -> None:
        with self._lock:
            self.state.cps = max(1, min(1000, cps))

    def set_button(self, name: str) -> None:
        with self._lock:
            self.state.button = MOUSE_BUTTONS.get(name, Button.left)

    def set_hotkey_label(self, label: str) -> None:
        key = label_to_key(label)
        if key is None:
            return
        with self._lock:
            self.state.hotkey_label = label
            self.state.hotkey = key

    def start_hotkey_listener(self) -> None:
        if self._kb_listener and self._kb_listener.running:
            return

        def on_press(key):
            if self._capturing:
                label = key_to_label(key)
                if label:
                    self._stop_capture()
                    self.set_hotkey_label(label)
                    self.signaler.hotkey_captured.emit(label)
                return

            with self._lock:
                hotkey = self.state.hotkey

            if self._keys_match(key, hotkey):
                self.toggle()

        self._kb_listener = keyboard.Listener(on_press=on_press)
        self._kb_listener.daemon = True
        self._kb_listener.start()

    def begin_hotkey_capture(self) -> None:
        self._capturing = True
        self.signaler.status.emit("Press any key to set hotkey…")

    def _stop_capture(self) -> None:
        self._capturing = False
        self.signaler.status.emit("Ready")

    def toggle(self) -> None:
        with self._lock:
            if self.state.active:
                self._deactivate_locked()
            else:
                self._activate_locked()

    def stop(self) -> None:
        with self._lock:
            self._deactivate_locked()
        if self._kb_listener:
            self._kb_listener.stop()

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _keys_match(pressed, target) -> bool:
        if pressed == target:
            return True
        p_label = key_to_label(pressed)
        t_label = key_to_label(target)
        return p_label is not None and p_label == t_label

    def _activate_locked(self) -> None:
        self.state.active = True
        self._stop_event.clear()
        self._click_thread = threading.Thread(target=self._click_loop, daemon=True)
        self._click_thread.start()
        self.signaler.toggled.emit(True)
        self.signaler.status.emit("Autoclicker running")

    def _deactivate_locked(self) -> None:
        self.state.active = False
        self._stop_event.set()
        self.signaler.toggled.emit(False)
        self.signaler.status.emit("Autoclicker stopped")

    def _click_loop(self) -> None:
        while not self._stop_event.is_set():
            with self._lock:
                cps = self.state.cps
                button = self.state.button
                active = self.state.active

            if not active:
                break

            self._mouse.click(button)

            interval = 1.0 / cps
            # For very high CPS, busy-wait the remainder for better accuracy.
            if cps >= 200:
                deadline = time.perf_counter() + interval
                while time.perf_counter() < deadline:
                    if self._stop_event.is_set():
                        return
            else:
                self._stop_event.wait(interval)


# ---------------------------------------------------------------------------
# UI widgets
# ---------------------------------------------------------------------------

STYLESHEET = """
QMainWindow, QWidget {
    background-color: #0f1117;
    color: #e8eaed;
    font-family: "Segoe UI", "SF Pro Display", sans-serif;
}

QFrame#card {
    background-color: #181b24;
    border: 1px solid #2a2f3d;
    border-radius: 14px;
}

QLabel#title {
    font-size: 22px;
    font-weight: 700;
    color: #ffffff;
}

QLabel#subtitle {
    font-size: 12px;
    color: #8b93a7;
}

QLabel#section {
    font-size: 11px;
    font-weight: 600;
    color: #6b7289;
    letter-spacing: 0.6px;
}

QLabel#cpsValue {
    font-size: 28px;
    font-weight: 700;
    color: #7c6cff;
}

QLabel#statusPill {
    background-color: #1e2330;
    border: 1px solid #2f3547;
    border-radius: 20px;
    padding: 6px 14px;
    font-size: 12px;
    color: #9aa3b8;
}

QLabel#statusPill[active="true"] {
    background-color: #14261c;
    border-color: #1f6b45;
    color: #4ade80;
}

QComboBox, QSpinBox {
    background-color: #12151d;
    border: 1px solid #2a3040;
    border-radius: 10px;
    padding: 10px 14px;
    font-size: 13px;
    color: #e8eaed;
    min-height: 20px;
}

QComboBox:hover, QSpinBox:hover {
    border-color: #7c6cff;
}

QComboBox::drop-down {
    border: none;
    width: 28px;
}

QComboBox QAbstractItemView {
    background-color: #181b24;
    border: 1px solid #2a2f3d;
    selection-background-color: #7c6cff;
    outline: none;
}

QSlider::groove:horizontal {
    height: 6px;
    background: #252a38;
    border-radius: 3px;
}

QSlider::handle:horizontal {
    width: 18px;
    height: 18px;
    margin: -6px 0;
    background: #7c6cff;
    border-radius: 9px;
}

QSlider::sub-page:horizontal {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #5b4fe0, stop:1 #7c6cff);
    border-radius: 3px;
}

QPushButton {
    background-color: #1e2230;
    border: 1px solid #2f3547;
    border-radius: 10px;
    padding: 11px 18px;
    font-size: 13px;
    font-weight: 600;
    color: #e8eaed;
}

QPushButton:hover {
    background-color: #262b3a;
    border-color: #7c6cff;
}

QPushButton#primary {
    background-color: #7c6cff;
    border: none;
    color: #ffffff;
}

QPushButton#primary:hover {
    background-color: #6a59f0;
}

QPushButton#danger {
    background-color: #2a1518;
    border-color: #5c2a32;
    color: #f87171;
}

QPushButton#danger[active="true"] {
    background-color: #ef4444;
    border: none;
    color: #ffffff;
}

QPushButton#hotkeyBtn[capturing="true"] {
    border-color: #f59e0b;
    color: #fbbf24;
}
"""


class Card(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AutoClicker")
        self.setFixedSize(420, 560)
        self.setStyleSheet(STYLESHEET)

        self.signaler = Signaler()
        self.engine = AutoClickerEngine(self.signaler)

        self._build_ui()
        self._wire_signals()
        self.engine.start_hotkey_listener()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Header
        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel("AutoClicker")
        title.setObjectName("title")
        subtitle = QLabel("Fast, precise mouse automation")
        subtitle.setObjectName("subtitle")
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        header.addLayout(title_col)
        header.addStretch()
        self.status_pill = QLabel("Stopped")
        self.status_pill.setObjectName("statusPill")
        self.status_pill.setProperty("active", False)
        self.status_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self.status_pill)
        layout.addLayout(header)

        # CPS card
        cps_card = Card()
        cps_layout = QVBoxLayout(cps_card)
        cps_layout.setContentsMargins(18, 16, 18, 16)
        cps_layout.setSpacing(12)

        cps_header = QHBoxLayout()
        cps_label = QLabel("CLICKS PER SECOND")
        cps_label.setObjectName("section")
        cps_header.addWidget(cps_label)
        cps_header.addStretch()
        self.cps_value = QLabel("10")
        self.cps_value.setObjectName("cpsValue")
        cps_header.addWidget(self.cps_value)
        cps_layout.addLayout(cps_header)

        self.cps_slider = QSlider(Qt.Orientation.Horizontal)
        self.cps_slider.setRange(1, 1000)
        self.cps_slider.setValue(10)
        cps_layout.addWidget(self.cps_slider)

        spin_row = QHBoxLayout()
        spin_row.addWidget(QLabel("Exact CPS"))
        self.cps_spin = QSpinBox()
        self.cps_spin.setRange(1, 1000)
        self.cps_spin.setValue(10)
        self.cps_spin.setSuffix(" cps")
        spin_row.addWidget(self.cps_spin)
        cps_layout.addLayout(spin_row)
        layout.addWidget(cps_card)

        # Mouse button card
        btn_card = Card()
        btn_layout = QVBoxLayout(btn_card)
        btn_layout.setContentsMargins(18, 16, 18, 16)
        btn_layout.setSpacing(10)
        mouse_label = QLabel("MOUSE BUTTON")
        mouse_label.setObjectName("section")
        btn_layout.addWidget(mouse_label)
        self.button_combo = QComboBox()
        self.button_combo.addItems(list(MOUSE_BUTTONS.keys()))
        btn_layout.addWidget(self.button_combo)
        layout.addWidget(btn_card)

        # Hotkey card
        hotkey_card = Card()
        hotkey_layout = QVBoxLayout(hotkey_card)
        hotkey_layout.setContentsMargins(18, 16, 18, 16)
        hotkey_layout.setSpacing(10)
        hk_label = QLabel("TOGGLE HOTKEY")
        hk_label.setObjectName("section")
        hotkey_layout.addWidget(hk_label)
        hk_row = QHBoxLayout()
        self.hotkey_btn = QPushButton("F6")
        self.hotkey_btn.setObjectName("hotkeyBtn")
        self.hotkey_btn.setMinimumWidth(120)
        hk_row.addWidget(self.hotkey_btn)
        hk_hint = QLabel("Click to rebind · works globally")
        hk_hint.setObjectName("subtitle")
        hk_row.addWidget(hk_hint)
        hk_row.addStretch()
        hotkey_layout.addLayout(hk_row)
        layout.addWidget(hotkey_card)

        layout.addStretch()

        # Action buttons
        actions = QHBoxLayout()
        self.toggle_btn = QPushButton("Start")
        self.toggle_btn.setObjectName("primary")
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("danger")
        actions.addWidget(self.toggle_btn)
        actions.addWidget(self.stop_btn)
        layout.addLayout(actions)

        footer = QLabel("Press the hotkey anywhere to toggle · Close window to exit")
        footer.setObjectName("subtitle")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(footer)

    def _wire_signals(self) -> None:
        self.cps_slider.valueChanged.connect(self._on_cps_changed)
        self.cps_spin.valueChanged.connect(self._on_cps_changed)
        self.button_combo.currentTextChanged.connect(self.engine.set_button)
        self.hotkey_btn.clicked.connect(self._begin_hotkey_capture)
        self.toggle_btn.clicked.connect(self.engine.toggle)
        self.stop_btn.clicked.connect(self._handle_stop)

        self.signaler.toggled.connect(self._set_active)
        self.signaler.hotkey_captured.connect(self._on_hotkey_captured)
        self.signaler.status.connect(self._on_status)

        self.engine.set_cps(10)
        self.engine.set_button("Left")

    def _on_cps_changed(self, value: int) -> None:
        self.cps_slider.blockSignals(True)
        self.cps_spin.blockSignals(True)
        self.cps_slider.setValue(value)
        self.cps_spin.setValue(value)
        self.cps_slider.blockSignals(False)
        self.cps_spin.blockSignals(False)
        self.cps_value.setText(str(value))
        self.engine.set_cps(value)

    def _begin_hotkey_capture(self) -> None:
        self.hotkey_btn.setProperty("capturing", True)
        self.hotkey_btn.setText("Press a key…")
        self.hotkey_btn.style().unpolish(self.hotkey_btn)
        self.hotkey_btn.style().polish(self.hotkey_btn)
        self.engine.begin_hotkey_capture()

    def _on_hotkey_captured(self, label: str) -> None:
        self.hotkey_btn.setProperty("capturing", False)
        self.hotkey_btn.setText(label)
        self.hotkey_btn.style().unpolish(self.hotkey_btn)
        self.hotkey_btn.style().polish(self.hotkey_btn)

    def _on_status(self, text: str) -> None:
        if "running" in text.lower():
            self.status_pill.setText("Running")
            self.status_pill.setProperty("active", True)
        elif "stopped" in text.lower():
            self.status_pill.setText("Stopped")
            self.status_pill.setProperty("active", False)
        else:
            self.status_pill.setText(text)
        self.status_pill.style().unpolish(self.status_pill)
        self.status_pill.style().polish(self.status_pill)

    def _handle_stop(self) -> None:
        if self.engine.state.active:
            self.engine.toggle()

    def _set_active(self, active: bool) -> None:
        self.toggle_btn.setText("Running…" if active else "Start")
        self.toggle_btn.setEnabled(not active)
        self.stop_btn.setProperty("active", active)
        self.stop_btn.style().unpolish(self.stop_btn)
        self.stop_btn.style().polish(self.stop_btn)
        self.status_pill.setText("Running" if active else "Stopped")
        self.status_pill.setProperty("active", active)
        self.status_pill.style().unpolish(self.status_pill)
        self.status_pill.style().polish(self.status_pill)

    def closeEvent(self, event) -> None:
        self.engine.stop()
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))

    # Dark palette fallback for native widgets
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#0f1117"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#e8eaed"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#12151d"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#e8eaed"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#1e2230"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#e8eaed"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#7c6cff"))
    app.setPalette(palette)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
