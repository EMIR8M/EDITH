import json
import os
import sys

from PyQt5.QtWidgets import QApplication

from core.brain import ensure_system_prompt_file
from ui.main_window import EdithMainWindow


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

DEFAULT_CONFIG = {
    "api_key": "",
    "base_url": "https://openrouter.ai/api/v1",
    "model_name": "openai/gpt-4o-mini",
    "wake_words": ["ادیت", "edith"],
    "auto_listen": True,
    "voice_enabled": True,
    "listening_session_minutes": 5,
    "context_history_messages": 12,
    "assistant_state_keywords": {
        "awake": ["ادیت", "edith"],
        "sleep": ["بخواب", "برو تو استراحت", "فعلاً سکوت کن"],
        "quit": ["خداحافظ", "برنامه رو ببند"],
    },
    "standby_phrases": ["برو رو استندبای", "غیرفعال شو", "خداحافظ"],
}


def save_config(config: dict) -> None:
    """Create or update the local configuration file using UTF-8 JSON."""
    with open(CONFIG_PATH, "w", encoding="utf-8") as config_file:
        json.dump(config, config_file, ensure_ascii=False, indent=2)
        config_file.write("\n")


def load_config() -> dict:
    """Load config and automatically create missing default settings."""
    config = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as config_file:
                loaded_config = json.load(config_file)
                if isinstance(loaded_config, dict):
                    config = loaded_config
        except (OSError, json.JSONDecodeError):
            config = {}

    merged_config = DEFAULT_CONFIG.copy()
    merged_config.update(config)

    # Migrate the former `model` setting without retaining two sources of truth.
    if "model_name" not in config and config.get("model"):
        merged_config["model_name"] = config["model"]
    merged_config.pop("model", None)

    # Migrate old state settings. They controlled the assistant, not Windows power.
    if "assistant_state_keywords" not in config:
        old_power = config.get("power_keywords", {})
        old_standby = old_power.get("standby", []) if isinstance(old_power, dict) else []
        merged_config["assistant_state_keywords"] = {
            "awake": config.get("wake_words", DEFAULT_CONFIG["wake_words"]),
            "sleep": old_standby or config.get("standby_phrases", []),
            "quit": ["خداحافظ", "برنامه رو ببند"],
        }
    if not isinstance(merged_config.get("assistant_state_keywords"), dict):
        merged_config["assistant_state_keywords"] = DEFAULT_CONFIG[
            "assistant_state_keywords"
        ].copy()
    merged_config.pop("power_keywords", None)
    merged_config["wake_words"] = merged_config["assistant_state_keywords"].get(
        "awake", DEFAULT_CONFIG["wake_words"]
    )
    merged_config["standby_phrases"] = merged_config["assistant_state_keywords"].get(
        "sleep", DEFAULT_CONFIG["assistant_state_keywords"]["sleep"]
    )

    if config != merged_config:
        try:
            save_config(merged_config)
        except OSError as error:
            print(f"[Config Setup Error]: {error}")

    return merged_config


def main() -> None:
    ensure_system_prompt_file()
    from core.brain import reset_conversation_history

    reset_conversation_history()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    config = load_config()
    window = EdithMainWindow(config, config_save_callback=save_config)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
