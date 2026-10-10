import asyncio
import os
import tempfile
import threading
import time

import edge_tts
import pygame


try:
    pygame.mixer.init()
except Exception:
    pass


_speech_lock = threading.Lock()
_speech_state_lock = threading.Lock()
_current_thread = None
_active_speech_count = 0


def _mark_speech_started() -> None:
    """Record a TTS session so STT can mute itself while audio is audible."""
    global _active_speech_count
    with _speech_state_lock:
        _active_speech_count += 1


def _mark_speech_finished() -> None:
    global _active_speech_count
    with _speech_state_lock:
        _active_speech_count = max(0, _active_speech_count - 1)


def is_speaking() -> bool:
    """Return whether E.D.I.T.H. is generating or playing speech right now."""
    with _speech_state_lock:
        return _active_speech_count > 0


def _speak_worker(text: str, voice: str) -> None:
    temp_file_path = None
    try:
        with _speech_lock:
            clean_text = text.replace("*", "").replace("#", "").strip()
            if not clean_text:
                return

            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as file:
                temp_file_path = file.name

            communicate = edge_tts.Communicate(clean_text, voice)
            asyncio.run(communicate.save(temp_file_path))

            if pygame.mixer.get_init():
                pygame.mixer.music.load(temp_file_path)
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    pygame.time.Clock().tick(10)
                pygame.mixer.music.unload()
    except Exception as error:
        print(f"[TTS Error]: {error}")
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except OSError:
                pass
        # Keep STT muted briefly after playback, allowing room echo to dissipate.
        time.sleep(0.5)
        _mark_speech_finished()


def speak_async(
    text: str, voice: str = "fa-IR-FaridNeural", enabled: bool = True
) -> None:
    """Speak in the background and expose a shared STT-mute state."""
    global _current_thread
    if not enabled or not text or not text.strip():
        return

    stop_speaking()
    _mark_speech_started()
    _current_thread = threading.Thread(
        target=_speak_worker,
        args=(text, voice),
        daemon=True,
    )
    _current_thread.start()


def stop_speaking() -> None:
    """Immediately stop current playback; STT resumes when its worker exits."""
    try:
        if pygame.mixer.get_init() and pygame.mixer.music.get_busy():
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()
    except Exception:
        pass
