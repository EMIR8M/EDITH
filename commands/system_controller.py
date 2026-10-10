import ctypes
import os
import shutil
import webbrowser
from pathlib import Path
from typing import Optional

from commands.reminder_manager import ReminderManager

try:
    import winreg
except ImportError:
    winreg = None


class SystemController:
    """Execute the allow-listed Windows actions used by E.D.I.T.H."""

    APP_MAP = {
        "notepad": "notepad.exe", "calculator": "calc.exe", "calc": "calc.exe",
        "cmd": "cmd.exe", "taskmgr": "taskmgr.exe", "paint": "mspaint.exe",
        "explorer": "explorer.exe", "chrome": "chrome.exe",
        "google chrome": "chrome.exe", "edge": "msedge.exe", "excel": "excel.exe",
        "microsoft excel": "excel.exe", "word": "winword.exe",
        "microsoft word": "winword.exe",
    }

    def __init__(self):
        self.pending_dangerous_action: Optional[str] = None
        self.reminder_manager = ReminderManager()

    def start_reminder_service(self, notification_callback) -> None:
        self.reminder_manager.start(notification_callback)

    def stop_reminder_service(self) -> None:
        self.reminder_manager.stop()

    def execute(self, action_data: dict) -> str:
        action = str(action_data.get("action", "")).casefold().strip()
        target = str(action_data.get("target", "")).strip()
        if action == "open_app": return self._open_app(target)
        if action == "open_url": return self._open_url(target)
        if action == "volume_up": return self._change_volume(0xAF, "صدا افزایش یافت.")
        if action == "volume_down": return self._change_volume(0xAE, "صدا کاهش یافت.")
        if action == "volume_mute": return self._change_volume(0xAD, "وضعیت بی‌صدا تغییر کرد.")
        if action == "set_reminder": return self._set_reminder(action_data)
        if action == "lock_pc":
            ctypes.windll.user32.LockWorkStation()
            return "سیستم قفل شد قربان."
        if action in {"shutdown", "restart", "sleep"}:
            self.pending_dangerous_action = action
            return "آیا مطمئن هستید؟ (بگویید بله یا خیر)"
        if action == "standby": return "دستیار در حالت آماده‌باش قرار گرفت."
        if action == "confirm_yes": return self._execute_pending_action(True)
        if action == "confirm_no": return self._execute_pending_action(False)
        return "دستور سیستمی شناخته نشد."

    @staticmethod
    def _change_volume(virtual_key: int, success_message: str) -> str:
        try:
            user32 = ctypes.windll.user32
            user32.keybd_event(virtual_key, 0, 0, 0)
            user32.keybd_event(virtual_key, 0, 0x0002, 0)
            return success_message
        except (AttributeError, OSError) as error:
            return f"کنترل صدای ویندوز در دسترس نیست: {error}"

    def _set_reminder(self, action_data: dict) -> str:
        message = action_data.get("message") or action_data.get("text") or action_data.get("target")
        remind_at = action_data.get("remind_at") or action_data.get("datetime") or action_data.get("time")
        if not remind_at: return "زمان یادآوری مشخص نشده است."
        try:
            scheduled = self.reminder_manager.add_reminder(message, remind_at)
            return f"یادآوری ثبت شد برای {scheduled}."
        except (TypeError, ValueError) as error:
            return f"یادآوری ثبت نشد: {error}"

    @staticmethod
    def _registry_app_path(executable: str) -> Optional[str]:
        if winreg is None: return None
        key_path = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{executable}"
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for access in (winreg.KEY_READ, winreg.KEY_READ | winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, key_path, 0, access) as key:
                        value, _ = winreg.QueryValueEx(key, None)
                    candidate = os.path.expandvars(str(value).strip().strip('"'))
                    if Path(candidate).is_file(): return candidate
                except OSError: continue
        return None

    @staticmethod
    def _common_install_paths(executable: str) -> list[Path]:
        roots = [Path(value) for value in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")) if value]
        candidates: list[Path] = []
        if executable.casefold() == "chrome.exe":
            candidates = [root / "Google" / "Chrome" / "Application" / executable for root in roots]
        elif executable.casefold() in {"excel.exe", "winword.exe"}:
            for root in roots:
                for version in ("Office16", "Office15", "Office14"):
                    candidates += [root / "Microsoft Office" / "root" / version / executable.upper(), root / "Microsoft Office" / version / executable.upper()]
        return candidates

    def _resolve_executable(self, executable: str) -> Optional[str]:
        if Path(executable).is_file(): return executable
        if found := shutil.which(executable): return found
        if found := self._registry_app_path(executable): return found
        for candidate in self._common_install_paths(executable):
            if candidate.is_file(): return str(candidate)
        if executable.casefold() in {"notepad.exe", "calc.exe", "cmd.exe", "taskmgr.exe", "mspaint.exe", "explorer.exe", "msedge.exe"}:
            return executable
        return None

    def _open_app(self, app_name: str) -> str:
        normalized = app_name.casefold().strip()
        executable = self.APP_MAP.get(normalized)
        if not executable: return f"برنامهٔ «{app_name or 'درخواست‌شده'}» در فهرست مجاز نیست."
        app_exec = self._resolve_executable(executable)
        if not app_exec: return f"مسیر اجرای {normalized} پیدا نشد."
        try:
            return f"برنامهٔ {normalized} با موفقیت اجرا شد." if os.system(f'start "" "{app_exec}"') == 0 else f"اجرای {normalized} با خطا مواجه شد."
        except OSError as error:
            return f"خطا در اجرای برنامه: {error}"

    @staticmethod
    def _open_url(url: str) -> str:
        if not url: return "نشانی وب نامعتبر است."
        if not url.startswith(("http://", "https://")): url = "https://" + url
        try:
            webbrowser.open(url)
            return "وب‌سایت باز شد قربان."
        except OSError as error:
            return f"خطا در باز کردن مرورگر: {error}"

    def _execute_pending_action(self, confirmed: bool) -> str:
        if not self.pending_dangerous_action: return "هیچ عملیات حساسی در انتظار تأیید نیست."
        action, self.pending_dangerous_action = self.pending_dangerous_action, None
        if not confirmed: return "عملیات لغو شد قربان."
        if action == "shutdown": os.system("shutdown /s /t 5"); return "سیستم تا ۵ ثانیهٔ دیگر خاموش می‌شود."
        if action == "restart": os.system("shutdown /r /t 5"); return "سیستم در حال ری‌استارت است."
        if action == "sleep": os.system("rundll32.exe powrprof.dll,SetSuspendState 0,1,0"); return "سیستم به حالت خواب می‌رود."
        return "دستور نامشخص است."
