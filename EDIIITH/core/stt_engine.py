import time

import speech_recognition as sr
from PyQt5.QtCore import QThread, pyqtSignal

from core.tts_engine import is_speaking


class VoiceListenerWorker(QThread):
    """Continuous STT with wake, assistant-sleep, and TTS echo-protection states."""

    wake_word_detected = pyqtSignal()
    transcription_ready = pyqtSignal(str)
    status_changed = pyqtSignal(str, str)
    session_state_changed = pyqtSignal(bool)
    assistant_sleep_requested = pyqtSignal()

    def __init__(self, config_data: dict):
        super().__init__()
        self.config_data = config_data
        state_words = config_data.get("assistant_state_keywords", {})
        awake_words = state_words.get("awake", config_data.get("wake_words", []))
        sleep_words = state_words.get("sleep", config_data.get("standby_phrases", []))
        self.wake_words = {
            str(word).casefold().strip() for word in awake_words if str(word).strip()
        }
        self.wake_words.update({"ادیت", "edith"})
        self.sleep_phrases = {
            str(word).casefold().strip() for word in sleep_words if str(word).strip()
        }
        self.is_running = True
        self.session_active = False
        self.session_expires_at = 0.0
        self._force_standby_requested = False
        try:
            self.session_duration_seconds = max(
                0.1, float(config_data.get("listening_session_minutes", 5))
            ) * 60
        except (TypeError, ValueError):
            self.session_duration_seconds = 300

        self.recognizer = sr.Recognizer()
        self.recognizer.energy_threshold = 300
        self.recognizer.dynamic_energy_threshold = True
        self.recognizer.pause_threshold = 0.8

    def _set_session_active(self, active: bool) -> None:
        if self.session_active != active:
            self.session_active = active
            self.session_state_changed.emit(active)

    def _enter_standby(self, message: str) -> None:
        self.session_expires_at = 0.0
        self._set_session_active(False)
        self.status_changed.emit(message, "#7d8790")

    def _activate_session(self) -> None:
        self.session_expires_at = time.monotonic() + self.session_duration_seconds
        self._set_session_active(True)
        self.wake_word_detected.emit()
        self.status_changed.emit("● جلسهٔ شنود فعال است؛ در خدمتم.", "#00f0ff")

    def _remove_wake_words(self, text: str) -> str:
        for wake_word in sorted(self.wake_words, key=len, reverse=True):
            text = text.replace(wake_word, " ")
        return " ".join(text.split())

    def _wait_for_tts(self) -> None:
        """Do not open or process microphone audio until TTS plus echo tail is done."""
        while self.is_running and is_speaking():
            self.status_changed.emit("● شنود تا پایان پخش صدا متوقف است.", "#7d8790")
            time.sleep(0.15)

    def run(self) -> None:
        try:
            with sr.Microphone() as source:
                self.status_changed.emit("● در حالت آماده‌باش؛ بگویید «ادیت».", "#00f0ff")
                self.recognizer.adjust_for_ambient_noise(source, duration=1)
                while self.is_running:
                    if self._force_standby_requested:
                        self._force_standby_requested = False
                        self._enter_standby("● دستیار در حالت استراحت است.")
                    if self.session_active and time.monotonic() >= self.session_expires_at:
                        self._enter_standby("● زمان جلسه تمام شد؛ بازگشت به آماده‌باش.")

                    self._wait_for_tts()
                    if not self.is_running:
                        break
                    try:
                        audio = self.recognizer.listen(
                            source,
                            timeout=3,
                            phrase_time_limit=10 if self.session_active else 5,
                        )
                    except sr.WaitTimeoutError:
                        continue
                    except Exception as error:
                        print(f"[STT Listen Error]: {error}")
                        continue

                    # TTS may have started while listen() was blocked. Discard this
                    # audio instead of recognizing E.D.I.T.H.'s own voice.
                    if is_speaking() or not self.is_running:
                        continue
                    try:
                        text = self.recognizer.recognize_google(audio, language="fa-IR").strip()
                    except sr.UnknownValueError:
                        continue
                    except sr.RequestError as error:
                        print(f"[Google STT Error]: {error}")
                        self.status_changed.emit("⚠ خطای اتصال اینترنت در تشخیص صدا.", "#ff0055")
                        continue

                    # Never act on a recognized phrase if TTS began meanwhile.
                    if is_speaking() or not text:
                        continue
                    normalized = text.casefold()
                    if self.session_active:
                        if any(phrase in normalized for phrase in self.sleep_phrases):
                            self.assistant_sleep_requested.emit()
                            self._enter_standby("● دستیار در حالت استراحت است.")
                            continue
                        self.session_expires_at = time.monotonic() + self.session_duration_seconds
                        self.transcription_ready.emit(text)
                    elif any(wake_word in normalized for wake_word in self.wake_words):
                        self._activate_session()
                        if command := self._remove_wake_words(normalized):
                            self.transcription_ready.emit(command)
        except Exception as error:
            print(f"[Mic Error]: {error}")
            self.status_changed.emit("⚠ خطا در دسترسی به میکروفون.", "#ff0055")
        finally:
            if self.session_active:
                self._enter_standby("● در حالت آماده‌باش؛ بگویید «ادیت».")

    def stop(self) -> None:
        self.is_running = False
        self.requestInterruption()

    def request_standby(self) -> None:
        self._force_standby_requested = True
