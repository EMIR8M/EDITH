from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)


class SettingsDialog(QDialog):
    """Tabbed editor for model, local shortcuts, and E.D.I.T.H. state phrases."""

    PROVIDER_PRESETS = {
        "OpenRouter": "https://openrouter.ai/api/v1",
        "Groq": "https://api.groq.com/openai/v1",
        "Google Gemini (OpenAI compatible)": "https://generativelanguage.googleapis.com/v1beta/openai/",
    }
    STATE_ACTIONS = (
        ("awake", "بیداری / فعال"),
        ("sleep", "خواب / سکوت"),
        ("quit", "خروج کامل"),
    )

    def __init__(self, config_data: dict, save_callback, custom_command_manager, parent=None):
        super().__init__(parent)
        self.config_data = config_data
        self.save_callback = save_callback
        self.custom_command_manager = custom_command_manager
        self.selected_trigger = None
        self.state_inputs = {}
        self.setWindowTitle("E.D.I.T.H Settings")
        self.setModal(True)
        self.setMinimumSize(600, 460)
        self._build_ui()
        self._apply_style()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        title = QLabel("E.D.I.T.H // SETTINGS")
        title.setObjectName("SettingsTitle")
        layout.addWidget(title)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_ai_tab(), "هوش مصنوعی")
        self.tabs.addTab(self._build_commands_tab(), "دستورات شخصی")
        self.tabs.addTab(self._build_system_tab(), "وضعیت دستیار")
        layout.addWidget(self.tabs)
        self.message_label = QLabel()
        self.message_label.setObjectName("SettingsMessage")
        layout.addWidget(self.message_label)
        buttons = QHBoxLayout(); buttons.addStretch()
        cancel = QPushButton("انصراف"); cancel.setObjectName("SecondaryButton"); cancel.clicked.connect(self.reject); buttons.addWidget(cancel)
        save = QPushButton("ذخیرهٔ تنظیمات"); save.setObjectName("SaveButton"); save.clicked.connect(self.save_settings); buttons.addWidget(save)
        layout.addLayout(buttons)

    def _build_ai_tab(self) -> QWidget:
        tab = QWidget(); form = QFormLayout(tab); form.setContentsMargins(12, 16, 12, 12); form.setSpacing(12)
        self.api_key_input = QLineEdit(str(self.config_data.get("api_key", ""))); self.api_key_input.setObjectName("SettingsInput"); self.api_key_input.setEchoMode(QLineEdit.Password)
        key_row = QHBoxLayout(); key_row.addWidget(self.api_key_input)
        reveal = QPushButton("نمایش"); reveal.setObjectName("SmallButton"); reveal.clicked.connect(lambda: self._toggle_key(reveal)); key_row.addWidget(reveal)
        form.addRow("API Key:", key_row)
        self.base_url_input = QLineEdit(str(self.config_data.get("base_url", ""))); self.base_url_input.setObjectName("SettingsInput"); form.addRow("Base URL:", self.base_url_input)
        preset = QComboBox(); preset.setObjectName("SettingsCombo"); preset.addItem("انتخاب سریع سرویس‌دهنده…", None)
        for name, url in self.PROVIDER_PRESETS.items(): preset.addItem(name, url)
        preset.currentIndexChanged.connect(lambda index: self._apply_preset(preset, index)); form.addRow("پیشنهاد سریع:", preset)
        self.model_name_input = QLineEdit(str(self.config_data.get("model_name", ""))); self.model_name_input.setObjectName("SettingsInput"); form.addRow("Model Name:", self.model_name_input)
        self.history_spin = QSpinBox(); self.history_spin.setRange(5, 50); self.history_spin.setValue(int(self.config_data.get("context_history_messages", 12))); self.history_spin.setObjectName("SettingsInput")
        form.addRow("پیام‌های حافظه:", self.history_spin)
        return tab

    def _build_commands_tab(self) -> QWidget:
        tab = QWidget(); layout = QVBoxLayout(tab); layout.setContentsMargins(12, 16, 12, 12)
        layout.addWidget(QLabel("این دستورات پیش از AI و به‌صورت آفلاین اجرا می‌شوند."))
        self.command_list = QListWidget(); self.command_list.itemSelectionChanged.connect(self._select_command); layout.addWidget(self.command_list)
        self.trigger_input = QLineEdit(); self.trigger_input.setObjectName("SettingsInput"); self.trigger_input.setPlaceholderText("نام یا کلمهٔ محرک، مثلاً: باز کردن پروژه")
        self.command_input = QLineEdit(); self.command_input.setObjectName("SettingsInput"); self.command_input.setPlaceholderText("مسیر فایل/پوشه یا دستور سیستم")
        layout.addWidget(self.trigger_input); layout.addWidget(self.command_input)
        actions = QHBoxLayout()
        add_update = QPushButton("افزودن / ویرایش"); add_update.setObjectName("SaveButton"); add_update.clicked.connect(self.upsert_command); actions.addWidget(add_update)
        delete = QPushButton("حذف"); delete.setObjectName("SecondaryButton"); delete.clicked.connect(self.delete_command); actions.addWidget(delete); actions.addStretch(); layout.addLayout(actions)
        self.refresh_command_list(); return tab

    def _build_system_tab(self) -> QWidget:
        tab = QWidget(); form = QFormLayout(tab); form.setContentsMargins(12, 16, 12, 12); form.setSpacing(14)
        form.addRow(QLabel("برای هر وضعیت حداکثر ۴ عبارت را با «،» یا «,» جدا کنید. تطبیق نزدیک فعال است."))
        keywords = self.config_data.get("assistant_state_keywords", {})
        for action, label in self.STATE_ACTIONS:
            field = QLineEdit(", ".join(str(item) for item in keywords.get(action, [])[:4])); field.setObjectName("SettingsInput")
            self.state_inputs[action] = field; form.addRow(f"{label}:", field)
        return tab

    def _toggle_key(self, button: QPushButton) -> None:
        hidden = self.api_key_input.echoMode() == QLineEdit.Password
        self.api_key_input.setEchoMode(QLineEdit.Normal if hidden else QLineEdit.Password); button.setText("پنهان" if hidden else "نمایش")

    def _apply_preset(self, combo: QComboBox, index: int) -> None:
        if url := combo.itemData(index): self.base_url_input.setText(url)

    def refresh_command_list(self) -> None:
        self.command_list.clear()
        for command in self.custom_command_manager.list_commands(): self.command_list.addItem(f"{command['trigger']}  →  {command['command']}")

    def _select_command(self) -> None:
        row = self.command_list.currentRow(); commands = self.custom_command_manager.list_commands()
        if 0 <= row < len(commands):
            command = commands[row]; self.selected_trigger = command["trigger"]; self.trigger_input.setText(command["trigger"]); self.command_input.setText(command["command"])

    def upsert_command(self) -> None:
        try:
            self.custom_command_manager.upsert(self.trigger_input.text(), self.command_input.text(), self.selected_trigger)
        except (OSError, ValueError) as error:
            self.message_label.setText(str(error)); return
        self.selected_trigger = None; self.trigger_input.clear(); self.command_input.clear(); self.refresh_command_list(); self.message_label.setText("دستور شخصی ذخیره شد.")

    def delete_command(self) -> None:
        trigger = self.selected_trigger or self.trigger_input.text()
        if not trigger: self.message_label.setText("ابتدا یک دستور را انتخاب کنید."); return
        try: self.custom_command_manager.delete(trigger)
        except OSError as error: self.message_label.setText(str(error)); return
        self.selected_trigger = None; self.trigger_input.clear(); self.command_input.clear(); self.refresh_command_list(); self.message_label.setText("دستور شخصی حذف شد.")

    def save_settings(self) -> None:
        base_url, model_name = self.base_url_input.text().strip(), self.model_name_input.text().strip()
        if not base_url or not model_name: self.message_label.setText("Base URL و Model Name الزامی هستند."); return
        state_keywords = {}
        for action, field in self.state_inputs.items():
            phrases = [part.strip() for part in field.text().replace("،", ",").split(",") if part.strip()]
            state_keywords[action] = phrases[:4]
        if not state_keywords["awake"]:
            self.message_label.setText("حداقل یک کلمهٔ بیداری وارد کنید."); return
        self.config_data.update({"api_key": self.api_key_input.text().strip(), "base_url": base_url, "model_name": model_name, "context_history_messages": self.history_spin.value(), "assistant_state_keywords": state_keywords, "wake_words": state_keywords["awake"], "standby_phrases": state_keywords["sleep"]})
        self.config_data.pop("model", None)
        self.config_data.pop("power_keywords", None)
        try: self.save_callback(self.config_data)
        except OSError as error: self.message_label.setText(f"ذخیره‌سازی ناموفق بود: {error}"); return
        self.accept()

    def _apply_style(self) -> None:
        self.setStyleSheet("""
            QDialog { background:#0a101a; border:2px solid #00eaff; border-radius:12px; }
            #SettingsTitle { color:#00f0ff; font-size:16px; font-weight:bold; letter-spacing:1px; }
            QTabWidget::pane { border:1px solid #146779; border-radius:6px; } QTabBar::tab { background:#101b29; color:#b9dce1; padding:9px 16px; } QTabBar::tab:selected { color:#00f0ff; border-bottom:2px solid #00f0ff; }
            QLabel { color:#ccecf0; } #SettingsInput, #SettingsCombo { background:#050a12; color:white; border:1px solid #00a9bd; border-radius:5px; padding:8px; min-height:20px; }
            #SettingsInput:focus, #SettingsCombo:focus { border-color:#00ffaa; } QListWidget { background:#050a12; color:white; border:1px solid #00a9bd; border-radius:5px; padding:5px; }
            #SmallButton, #SecondaryButton { background:rgba(0,240,255,.1); color:#00eaff; border:1px solid #00a9bd; border-radius:5px; padding:8px 10px; } #SaveButton { background:rgba(0,255,170,.18); color:#00ffaa; border:1px solid #00ffaa; border-radius:5px; padding:8px 14px; font-weight:bold; } #SettingsMessage { color:#ff8a8a; min-height:16px; }
        """)
