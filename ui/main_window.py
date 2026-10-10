import random
import sys

from PyQt5.QtCore import QPoint, QThread, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QPainter, QPixmap, QRadialGradient
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPushButton,
    QScrollArea,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from core.brain import custom_command_manager, get_ai_response, handle_offline_input, system_ctrl
from core.stt_engine import VoiceListenerWorker
from core.tts_engine import speak_async, stop_speaking
from ui.settings_dialog import SettingsDialog


class AIWorker(QThread):
    finished = pyqtSignal(str)

    def __init__(self, prompt: str, config_data: dict):
        super().__init__()
        self.prompt = prompt
        self.config_data = config_data

    def run(self) -> None:
        self.finished.emit(get_ai_response(self.prompt, self.config_data))


class FloatingHudOrb(QWidget):
    """A compact always-on-top control and listening-state indicator."""

    activated = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.is_awake = False
        self.pulse_phase = 0
        self._drag_origin = None
        self._initial_position = None
        self._dragged = False

        self.setFixedSize(78, 78)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("E.D.I.T.H — برای باز کردن پنجره کلیک کنید")

        self.pulse_timer = QTimer(self)
        self.pulse_timer.setInterval(60)
        self.pulse_timer.timeout.connect(self._advance_pulse)
        self.click_timer = QTimer(self)
        self.click_timer.setSingleShot(True)
        self.click_timer.setInterval(180)
        self.click_timer.timeout.connect(self.activated.emit)

    def set_awake(self, awake: bool) -> None:
        if self.is_awake == awake:
            return
        self.is_awake = awake
        if awake:
            self.pulse_timer.start()
        else:
            self.pulse_timer.stop()
            self.pulse_phase = 0
        self.update()

    def set_listening(self, listening: bool) -> None:
        """Expose an explicit listening state for the orb's visual feedback."""
        self.set_awake(listening)

    def place_in_corner(self) -> None:
        screen = QApplication.screenAt(self.pos()) or QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.right() - self.width() - 24, area.bottom() - self.height() - 36)

    def _advance_pulse(self) -> None:
        self.pulse_phase = (self.pulse_phase + 1) % 24
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt callback name
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        center = self.rect().center()
        radius = min(self.width(), self.height()) / 2 - 8

        if self.is_awake:
            pulse = abs(12 - self.pulse_phase) / 12
            glow_radius = radius + 4 + int((1 - pulse) * 3)
            glow = QRadialGradient(center, glow_radius)
            glow.setColorAt(0.0, QColor(0, 240, 255, 42))
            glow.setColorAt(0.55, QColor(0, 190, 255, 14))
            glow.setColorAt(1.0, QColor(0, 190, 255, 0))
            painter.setPen(Qt.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(center, glow_radius, glow_radius)

            core = QRadialGradient(center, radius)
            core.setColorAt(0.0, QColor(225, 255, 255, 235))
            core.setColorAt(0.18, QColor(40, 244, 255, 220))
            core.setColorAt(0.50, QColor(0, 185, 215, 180))
            core.setColorAt(0.78, QColor(0, 70, 98, 175))
            core.setColorAt(1.0, QColor(2, 22, 34))
            painter.setBrush(core)
            painter.setPen(QColor(72, 255, 255, 230))
            painter.drawEllipse(center, radius, radius)
        else:
            core = QRadialGradient(center, radius)
            core.setColorAt(0.0, QColor(70, 77, 85))
            core.setColorAt(0.65, QColor(32, 38, 45))
            core.setColorAt(1.0, QColor(15, 18, 23))
            painter.setBrush(core)
            painter.setPen(QColor(93, 101, 110, 180))
            painter.drawEllipse(center, radius, radius)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt callback name
        if event.button() == Qt.LeftButton:
            self._drag_origin = event.globalPos()
            self._initial_position = self.pos()
            self._dragged = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt callback name
        if self._drag_origin is not None and event.buttons() & Qt.LeftButton:
            delta = event.globalPos() - self._drag_origin
            if delta.manhattanLength() > 3:
                self._dragged = True
                self.move(self._initial_position + delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt callback name
        if event.button() == Qt.LeftButton:
            if not self._dragged:
                self.click_timer.start()
            self._drag_origin = None
            self._initial_position = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt callback name
        if event.button() == Qt.LeftButton:
            self.click_timer.stop()
            self.activated.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class EdithMainWindow(QMainWindow):
    reminder_due = pyqtSignal(str)

    WAKE_RESPONSES = [
        "بله قربان؟",
        "من اینجام قربان.",
        "در خدمتم.",
        "سیستم آماده است.",
    ]

    def __init__(self, config_data: dict, config_save_callback=None):
        super().__init__()
        self.config_data = config_data
        self.config_save_callback = config_save_callback
        self.voice_enabled = bool(self.config_data.get("voice_enabled", True))
        self.auto_listen = bool(self.config_data.get("auto_listen", True))
        self.assistant_muted = False
        self.listening_session_active = False
        self.old_pos = QPoint()
        self.ai_worker = None
        self.listener_worker = None
        self.pending_queries: list[str] = []

        self.init_ui()
        self.init_tray()
        self.init_orb()
        self.reminder_due.connect(self.on_reminder_due)
        system_ctrl.start_reminder_service(self.reminder_due.emit)

        if self.auto_listen:
            self.start_listener()

    def init_orb(self) -> None:
        self.hud_orb = FloatingHudOrb()
        self.hud_orb.activated.connect(self.toggle_main_window)
        self.hud_orb.place_in_corner()
        self.hud_orb.set_listening(self.auto_listen)
        self.hud_orb.show()

    def init_ui(self) -> None:
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(480, 650)
        self.setMinimumSize(420, 520)

        self.central_widget = QWidget(self)
        self.central_widget.setObjectName("CentralWidget")
        self.setCentralWidget(self.central_widget)

        main_layout = QVBoxLayout(self.central_widget)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(10)

        header_layout = QHBoxLayout()
        self.title_label = QLabel("E.D.I.T.H. // OS_ONLINE")
        self.title_label.setObjectName("HeaderTitle")
        header_layout.addWidget(self.title_label)
        header_layout.addStretch()

        self.mic_btn = QPushButton("🎙" if self.auto_listen else "⏸")
        self.mic_btn.setObjectName("HeaderBtn")
        self.mic_btn.setToolTip("روشن/خاموش کردن شنود خودکار")
        self.mic_btn.clicked.connect(self.toggle_listener)
        header_layout.addWidget(self.mic_btn)

        self.voice_btn = QPushButton("🔊" if self.voice_enabled else "🔇")
        self.voice_btn.setObjectName("HeaderBtn")
        self.voice_btn.setToolTip("روشن/خاموش کردن صدای E.D.I.T.H")
        self.voice_btn.clicked.connect(self.toggle_voice)
        header_layout.addWidget(self.voice_btn)

        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setObjectName("HeaderBtn")
        self.settings_btn.setToolTip("تنظیمات مدل و سرویس‌دهنده")
        self.settings_btn.clicked.connect(self.open_settings)
        header_layout.addWidget(self.settings_btn)

        self.min_btn = QPushButton("—")
        self.min_btn.setObjectName("HeaderBtn")
        self.min_btn.setToolTip("مخفی کردن پنجره")
        self.min_btn.clicked.connect(self.hide_to_tray)
        header_layout.addWidget(self.min_btn)

        self.close_btn = QPushButton("×")
        self.close_btn.setObjectName("CloseBtn")
        self.close_btn.clicked.connect(self.close_application)
        header_layout.addWidget(self.close_btn)
        main_layout.addLayout(header_layout)

        self.status_label = QLabel("● سیستم آماده‌به‌کار است.")
        self.status_label.setObjectName("StatusLabel")
        main_layout.addWidget(self.status_label)

        self.chat_scroll = QScrollArea()
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setObjectName("ChatScroll")
        self.chat_container = QWidget()
        self.chat_container.setObjectName("ChatContainer")
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.addStretch()
        self.chat_scroll.setWidget(self.chat_container)
        main_layout.addWidget(self.chat_scroll)

        input_layout = QHBoxLayout()
        input_layout.setSpacing(8)
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("دستور متنی یا صوتی؛ بگویید «ادیت»...")
        self.input_field.setObjectName("InputField")
        self.input_field.returnPressed.connect(self.handle_text_submit)
        input_layout.addWidget(self.input_field)

        self.send_btn = QPushButton("ارسال")
        self.send_btn.setObjectName("SendBtn")
        self.send_btn.clicked.connect(self.handle_text_submit)
        input_layout.addWidget(self.send_btn)
        main_layout.addLayout(input_layout)

        self.apply_edith_style()
        welcome_text = "سلام! سیستم E.D.I.T.H آماده است. برای شروع بگویید «ادیت»."
        self.add_message(welcome_text, is_user=False)

    def apply_edith_style(self) -> None:
        self.setStyleSheet(
            """
            #CentralWidget { background-color: rgba(10, 16, 26, 0.96); border: 2px solid #00f0ff; border-radius: 12px; }
            #HeaderTitle { color: #00f0ff; font-family: 'Segoe UI'; font-size: 13px; font-weight: bold; letter-spacing: 1px; }
            #StatusLabel { color: #00ffaa; font-size: 11px; font-weight: bold; padding-bottom: 4px; border-bottom: 1px solid rgba(0, 240, 255, 0.2); }
            #HeaderBtn, #CloseBtn { background: transparent; color: #00f0ff; border: 1px solid rgba(0, 240, 255, 0.4); border-radius: 4px; min-width: 26px; max-width: 26px; min-height: 22px; font-weight: bold; }
            #CloseBtn:hover { background-color: #ff0055; color: white; border-color: #ff0055; }
            #HeaderBtn:hover { background-color: rgba(0, 240, 255, 0.2); }
            #ChatScroll { background: transparent; border: 1px solid rgba(0, 240, 255, 0.15); border-radius: 6px; }
            #ChatContainer { background: transparent; }
            #InputField { background-color: rgba(5, 10, 18, 0.85); border: 1px solid #00f0ff; border-radius: 6px; color: #ffffff; padding: 8px 12px; font-size: 13px; }
            #InputField:focus { border: 1px solid #00ffaa; }
            #SendBtn { background-color: rgba(0, 240, 255, 0.15); border: 1px solid #00f0ff; border-radius: 6px; color: #00f0ff; font-weight: bold; padding: 8px 16px; }
            #SendBtn:hover { background-color: #00f0ff; color: #050a12; }
            .UserBubble { background-color: rgba(0, 240, 255, 0.15); border: 1px solid #00f0ff; border-radius: 8px; color: #ffffff; padding: 8px 12px; font-size: 13px; }
            .EdithBubble { background-color: rgba(20, 35, 55, 0.75); border: 1px solid rgba(0, 255, 170, 0.6); border-radius: 8px; color: #e0f7fa; padding: 8px 12px; font-size: 13px; }
            """
        )

    def start_listener(self) -> None:
        if self.listener_worker and self.listener_worker.isRunning():
            return
        self.listener_worker = VoiceListenerWorker(self.config_data)
        self.listener_worker.wake_word_detected.connect(self.on_wake_word_detected)
        self.listener_worker.transcription_ready.connect(self.process_user_query)
        self.listener_worker.status_changed.connect(self.update_status_label)
        self.listener_worker.session_state_changed.connect(self.on_session_state_changed)
        self.listener_worker.assistant_sleep_requested.connect(
            self.on_assistant_sleep_requested
        )
        self.listener_worker.start()

    def stop_listener(self) -> None:
        if not self.listener_worker:
            return
        self.listener_worker.stop()
        self.listener_worker.wait(4000)
        self.listener_worker = None
        self.on_session_state_changed(False)
        self.update_status_label("● شنود غیرفعال شد.", "#7d8790")

    def toggle_listener(self) -> None:
        self.auto_listen = not self.auto_listen
        self.mic_btn.setText("🎙" if self.auto_listen else "⏸")
        if self.auto_listen:
            self.assistant_muted = False
            self.hud_orb.set_listening(True)
            self.start_listener()
        else:
            self.assistant_muted = True
            self.hud_orb.set_listening(False)
            self.stop_listener()

    def update_status_label(self, text: str, color: str) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color};")

    def on_session_state_changed(self, active: bool) -> None:
        self.listening_session_active = active
        if active and not self.assistant_muted:
            self.hud_orb.set_listening(True)
        if not active and self.auto_listen:
            self.update_status_label("● در حالت آماده‌باش؛ بگویید «ادیت».", "#7d8790")

    def on_wake_word_detected(self) -> None:
        self.assistant_muted = False
        self.hud_orb.set_listening(True)
        if self.voice_enabled:
            speak_async(random.choice(self.WAKE_RESPONSES), enabled=True)

    def on_assistant_sleep_requested(self) -> None:
        """Reflect an STT-detected assistant sleep phrase in the floating orb."""
        self.assistant_muted = True
        self.hud_orb.set_listening(False)

    def on_reminder_due(self, message: str) -> None:
        """Show a Windows tray notification and announce a due reminder aloud."""
        notification_text = f"یادآوری شما: {message}"
        self.tray_icon.showMessage(
            "E.D.I.T.H — یادآوری",
            notification_text,
            QSystemTrayIcon.Information,
            10000,
        )
        self.add_message(notification_text, is_user=False)
        if self.voice_enabled:
            speak_async(f"قربان، یادآوری شما: {message}", enabled=True)

    def toggle_voice(self) -> None:
        self.voice_enabled = not self.voice_enabled
        self.voice_btn.setText("🔊" if self.voice_enabled else "🔇")
        if not self.voice_enabled:
            stop_speaking()

    def open_settings(self) -> None:
        if not self.config_save_callback:
            self.update_status_label("● ذخیرهٔ تنظیمات در دسترس نیست.", "#ff7b7b")
            return
        dialog = SettingsDialog(
            self.config_data,
            self.config_save_callback,
            custom_command_manager,
            parent=self,
        )
        if dialog.exec_():
            # Reload listener phrases immediately so new standby/power settings apply.
            if self.auto_listen:
                self.stop_listener()
                self.start_listener()
            self.update_status_label(
                "● تنظیمات مدل و سرویس‌دهنده به‌روزرسانی شد.", "#00ffaa"
            )

    def add_message(self, text: str, is_user: bool = False) -> None:
        bubble = QLabel(text)
        bubble.setWordWrap(True)
        bubble.setProperty("class", "UserBubble" if is_user else "EdithBubble")

        wrapper = QHBoxLayout()
        if is_user:
            wrapper.addStretch()
            wrapper.addWidget(bubble)
        else:
            wrapper.addWidget(bubble)
            wrapper.addStretch()
        self.chat_layout.insertLayout(max(0, self.chat_layout.count() - 1), wrapper)
        QTimer.singleShot(
            0,
            lambda: self.chat_scroll.verticalScrollBar().setValue(
                self.chat_scroll.verticalScrollBar().maximum()
            ),
        )

    def handle_text_submit(self) -> None:
        text = self.input_field.text().strip()
        if not text:
            return
        self.input_field.clear()
        self.process_user_query(text)

    def process_user_query(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self.add_message(text, is_user=True)

        # Offline custom commands and assistant-state phrases always win over AI.
        offline_result = handle_offline_input(text, self.config_data)
        if offline_result:
            response, action = offline_result
            self.add_message(f"E.D.I.T.H: {response}", is_user=False)
            if action == "sleep":
                self.on_assistant_sleep_requested()
                if self.listener_worker:
                    self.listener_worker.request_standby()
            elif action == "awake":
                self.assistant_muted = False
                self.hud_orb.set_listening(True)
                if not self.auto_listen:
                    self.auto_listen = True
                    self.mic_btn.setText("🎙")
                    self.start_listener()
            elif action == "quit":
                if self.voice_enabled:
                    speak_async(response, enabled=True)
                QTimer.singleShot(700, self.close_application)
                return
            if self.voice_enabled:
                speak_async(response, enabled=True)
            return

        if self.ai_worker and self.ai_worker.isRunning():
            self.pending_queries.append(text)
            self.update_status_label("● دستور در صف پردازش قرار گرفت.", "#ffaa00")
            return
        self._start_ai_worker(text)

    def _start_ai_worker(self, text: str) -> None:
        self.input_field.setEnabled(False)
        self.send_btn.setEnabled(False)
        self.update_status_label("● در حال پردازش با E.D.I.T.H...", "#ffaa00")
        self.ai_worker = AIWorker(text, self.config_data)
        self.ai_worker.finished.connect(self.on_ai_response)
        self.ai_worker.start()

    def on_ai_response(self, response: str) -> None:
        self.add_message(f"E.D.I.T.H: {response}", is_user=False)
        if self.voice_enabled:
            speak_async(response, enabled=True)

        if self.pending_queries:
            next_query = self.pending_queries.pop(0)
            self._start_ai_worker(next_query)
            return

        self.input_field.setEnabled(True)
        self.send_btn.setEnabled(True)
        self.input_field.setFocus()
        if self.listening_session_active:
            self.update_status_label("● جلسهٔ شنود فعال است؛ در خدمتم.", "#00f0ff")
        elif self.auto_listen:
            self.update_status_label("● در حالت آماده‌باش؛ بگویید «ادیت».", "#7d8790")

    def init_tray(self) -> None:
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor(0, 240, 255))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(4, 4, 24, 24)
        painter.end()
        self.tray_icon = QSystemTrayIcon(QIcon(pixmap), self)

        tray_menu = QMenu()
        show_action = QAction("نمایش پنجرهٔ E.D.I.T.H", self)
        show_action.triggered.connect(self.show_normal)
        tray_menu.addAction(show_action)
        tray_menu.addSeparator()
        quit_action = QAction("خروج کامل", self)
        quit_action.triggered.connect(self.close_application)
        tray_menu.addAction(quit_action)
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.tray_icon_activated)
        self.tray_icon.show()

    def tray_icon_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_normal()

    def show_normal(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def toggle_main_window(self) -> None:
        if self.isVisible():
            self.hide_to_tray()
        else:
            self.show_normal()

    def hide_to_tray(self) -> None:
        self.hide()

    def close_application(self) -> None:
        self.stop_listener()
        system_ctrl.stop_reminder_service()
        stop_speaking()
        self.hud_orb.hide()
        self.tray_icon.hide()
        QApplication.instance().quit()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt callback name
        if event.button() == Qt.LeftButton:
            self.old_pos = event.globalPos()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt callback name
        if not self.old_pos.isNull():
            delta = QPoint(event.globalPos() - self.old_pos)
            self.move(self.x() + delta.x(), self.y() + delta.y())
            self.old_pos = event.globalPos()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt callback name
        del event
        self.old_pos = QPoint()
