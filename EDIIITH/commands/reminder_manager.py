import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent
REMINDERS_FILE = PROJECT_ROOT / "reminders.json"


class ReminderManager:
    """Persist reminders locally and dispatch due reminders from a daemon thread."""

    def __init__(self, storage_path: Path = REMINDERS_FILE):
        self.storage_path = Path(storage_path)
        self._lock = threading.RLock()
        self._wake_event = threading.Event()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._notification_callback: Optional[Callable[[str], None]] = None
        self._reminders = self._load_reminders()

    def _load_reminders(self) -> list[dict]:
        if not self.storage_path.exists():
            return []
        try:
            with self.storage_path.open("r", encoding="utf-8") as file:
                data = json.load(file)
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
        except (OSError, json.JSONDecodeError) as error:
            print(f"[Reminder Load Error]: {error}")
        return []

    def _save_reminders(self) -> None:
        """Atomically save reminders so an interrupted write cannot corrupt data."""
        temporary_path = self.storage_path.with_suffix(".tmp")
        try:
            with temporary_path.open("w", encoding="utf-8") as file:
                json.dump(self._reminders, file, ensure_ascii=False, indent=2)
                file.write("\n")
            os.replace(temporary_path, self.storage_path)
        except OSError as error:
            print(f"[Reminder Save Error]: {error}")
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _parse_reminder_time(value: str) -> datetime:
        """Accept an ISO-8601 datetime and normalize naive values to local time."""
        normalized_value = str(value).strip().replace("Z", "+00:00")
        due_time = datetime.fromisoformat(normalized_value)
        if due_time.tzinfo is None:
            due_time = due_time.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return due_time.astimezone()

    def add_reminder(self, message: str, remind_at: str) -> str:
        message = str(message).strip()
        if not message:
            raise ValueError("متن یادآوری خالی است.")

        due_time = self._parse_reminder_time(remind_at)
        if due_time <= datetime.now().astimezone():
            raise ValueError("زمان یادآوری باید در آینده باشد.")

        reminder = {
            "id": str(uuid.uuid4()),
            "message": message,
            "remind_at": due_time.isoformat(),
            "created_at": datetime.now().astimezone().isoformat(),
        }
        with self._lock:
            self._reminders.append(reminder)
            self._save_reminders()
        self._wake_event.set()
        return due_time.strftime("%Y-%m-%d %H:%M")

    def start(self, notification_callback: Callable[[str], None]) -> None:
        """Start the background scheduler once and set the Windows-notification hook."""
        self._notification_callback = notification_callback
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run,
            name="edith-reminder-manager",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._wake_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None

    def _pop_due_reminders(self) -> list[dict]:
        now = datetime.now().astimezone()
        due_reminders: list[dict] = []
        pending_reminders: list[dict] = []

        with self._lock:
            for reminder in self._reminders:
                try:
                    due_time = self._parse_reminder_time(reminder.get("remind_at", ""))
                except (TypeError, ValueError):
                    continue
                if due_time <= now:
                    due_reminders.append(reminder)
                else:
                    pending_reminders.append(reminder)

            if len(pending_reminders) != len(self._reminders):
                self._reminders = pending_reminders
                self._save_reminders()

        return due_reminders

    def _run(self) -> None:
        while self._running:
            for reminder in self._pop_due_reminders():
                message = str(reminder.get("message", "یادآوری بدون متن"))
                callback = self._notification_callback
                if callback:
                    try:
                        callback(message)
                    except Exception as error:
                        print(f"[Reminder Notification Error]: {error}")
            self._wake_event.wait(timeout=1)
            self._wake_event.clear()
