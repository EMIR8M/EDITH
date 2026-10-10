import json
import os
from pathlib import Path
from threading import RLock


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CUSTOM_COMMANDS_FILE = PROJECT_ROOT / "custom_commands.json"


def normalize_trigger(value: str) -> str:
    """Normalize triggers for predictable offline exact matching."""
    return " ".join(str(value).casefold().strip().split())


class CustomCommandManager:
    """Persistent, user-managed shortcuts that run before any online AI request."""

    def __init__(self, storage_path: Path = CUSTOM_COMMANDS_FILE):
        self.storage_path = Path(storage_path)
        self._lock = RLock()
        self._commands = self._load()
        if not self.storage_path.exists():
            self._save()

    def _load(self) -> list[dict[str, str]]:
        try:
            with self.storage_path.open("r", encoding="utf-8") as file:
                data = json.load(file)
            if isinstance(data, list):
                return [
                    {"trigger": str(item["trigger"]), "command": str(item["command"])}
                    for item in data
                    if isinstance(item, dict)
                    and str(item.get("trigger", "")).strip()
                    and str(item.get("command", "")).strip()
                ]
        except (OSError, json.JSONDecodeError, KeyError):
            pass
        return []

    def _save(self) -> None:
        temporary_path = self.storage_path.with_suffix(".tmp")
        with temporary_path.open("w", encoding="utf-8") as file:
            json.dump(self._commands, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(temporary_path, self.storage_path)

    def list_commands(self) -> list[dict[str, str]]:
        with self._lock:
            return [command.copy() for command in self._commands]

    def upsert(self, trigger: str, command: str, original_trigger: str | None = None) -> None:
        trigger = str(trigger).strip()
        command = str(command).strip()
        if not trigger or not command:
            raise ValueError("کلمهٔ محرک و دستور هر دو الزامی هستند.")

        normalized = normalize_trigger(trigger)
        original_normalized = normalize_trigger(original_trigger or trigger)
        with self._lock:
            for index, existing in enumerate(self._commands):
                existing_normalized = normalize_trigger(existing["trigger"])
                if existing_normalized == original_normalized:
                    self._commands[index] = {"trigger": trigger, "command": command}
                    self._save()
                    return
                if existing_normalized == normalized:
                    raise ValueError("این کلمهٔ محرک قبلاً ثبت شده است.")
            self._commands.append({"trigger": trigger, "command": command})
            self._save()

    def delete(self, trigger: str) -> None:
        normalized = normalize_trigger(trigger)
        with self._lock:
            self._commands = [
                command
                for command in self._commands
                if normalize_trigger(command["trigger"]) != normalized
            ]
            self._save()

    def execute_if_matched(self, user_text: str) -> str | None:
        """Run a matching local shortcut and return None when no shortcut matches."""
        normalized_input = normalize_trigger(user_text)
        with self._lock:
            matching_command = next(
                (
                    command["command"]
                    for command in self._commands
                    if normalize_trigger(command["trigger"]) == normalized_input
                ),
                None,
            )
        if not matching_command:
            return None
        return self._run(matching_command)

    @staticmethod
    def _run(command: str) -> str:
        path = Path(os.path.expandvars(os.path.expanduser(command))).resolve()
        try:
            if path.exists():
                os.system(f'start "" "{path}"')
            else:
                os.system(command)
            return "دستور شخصی با موفقیت اجرا شد."
        except OSError as error:
            return f"خطا در اجرای دستور شخصی: {error}"
