import ast
import difflib
import json
import os
import re
from datetime import datetime
from typing import Any, Optional

from openai import OpenAI

from commands.system_controller import SystemController
from commands.custom_command_manager import CustomCommandManager, normalize_trigger


system_ctrl = SystemController()
custom_command_manager = CustomCommandManager()
conversation_history: list[dict[str, str]] = []

PROMPT_FILE = os.path.join(os.path.dirname(__file__), "system_prompt.txt")

DEFAULT_SYSTEM_PROMPT = """You are E.D.I.T.H., a sophisticated Windows voice assistant.

YOUR RULES:
1. Reply in concise, natural Persian for ordinary questions.
2. Never reveal, describe, or simulate your reasoning, chain of thought, analysis, planning, input analysis, or internal process. Do not output English thinking text or any extra meta-text.
3. When a system action is needed, return ONLY one valid JSON object inside a ```json code block. Do not write any text before or after that block.
4. Supported actions are: {\"action\": \"open_app\", \"target\": \"name\"}, {\"action\": \"open_url\", \"target\": \"url\"}, {\"action\": \"lock_pc\"}, {\"action\": \"shutdown\"}, {\"action\": \"restart\"}, {\"action\": \"volume_up\"}, {\"action\": \"volume_down\"}, {\"action\": \"volume_mute\"}.
5. For a reminder, return {\"action\": \"set_reminder\", \"message\": \"Persian reminder text\", \"remind_at\": \"YYYY-MM-DDTHH:MM:SS+03:30\"}. Resolve relative Persian times such as «۱۰ دقیقه دیگه» to an exact future ISO-8601 time using the current local time supplied in this conversation.
6. Keep every response short and stay in character as E.D.I.T.H.
"""

JSON_BLOCK_PATTERN = re.compile(
    r"```(?:json)?\s*(\{.*?\})\s*```",
    flags=re.IGNORECASE | re.DOTALL,
)
TOOL_TAG_PATTERN = re.compile(
    r"<\|tool_call_start\|>\s*(.*?)\s*<\|tool_call_end\|>",
    flags=re.IGNORECASE | re.DOTALL,
)
XML_TOOL_PATTERN = re.compile(
    r"<tool_call>\s*(.*?)\s*</tool_call>",
    flags=re.IGNORECASE | re.DOTALL,
)
FUNCTION_CALL_PATTERN = re.compile(
    r"(?P<action>open_app|open_url|lock_pc|shutdown|restart|volume_up|volume_down|volume_mute|set_reminder)\s*\((?P<args>.*?)\)",
    flags=re.IGNORECASE | re.DOTALL,
)
SUPPORTED_ACTIONS = {
    "open_app",
    "open_url",
    "lock_pc",
    "shutdown",
    "restart",
    "volume_up",
    "volume_down",
    "volume_mute",
    "set_reminder",
}


def ensure_system_prompt_file() -> None:
    """Create the default system prompt on a first run or after deletion."""
    if os.path.exists(PROMPT_FILE):
        return

    try:
        with open(PROMPT_FILE, "w", encoding="utf-8") as prompt_file:
            prompt_file.write(DEFAULT_SYSTEM_PROMPT)
    except OSError as error:
        print(f"[Prompt Setup Error]: {error}")


def get_system_prompt() -> str:
    ensure_system_prompt_file()
    try:
        with open(PROMPT_FILE, "r", encoding="utf-8") as prompt_file:
            return prompt_file.read().strip()
    except OSError:
        return DEFAULT_SYSTEM_PROMPT


def _parse_tool_arguments(argument_text: str) -> dict[str, Any]:
    """Safely parse Python-like tool-call arguments without evaluating code."""
    argument_text = argument_text.strip()
    if not argument_text:
        return {}

    try:
        expression = ast.parse(f"handler({argument_text})", mode="eval").body
        if not isinstance(expression, ast.Call):
            raise ValueError("Tool call is not a function call.")

        arguments: dict[str, Any] = {}
        for keyword in expression.keywords:
            if keyword.arg is not None:
                arguments[keyword.arg] = ast.literal_eval(keyword.value)

        if expression.args:
            first_argument = ast.literal_eval(expression.args[0])
            if isinstance(first_argument, dict):
                arguments.update(first_argument)
            elif isinstance(first_argument, str):
                arguments.setdefault("target", first_argument)

        return arguments
    except (SyntaxError, ValueError, TypeError) as error:
        raise ValueError("فرمت دستور ابزار نامعتبر است.") from error


def _extract_tool_call(text: str) -> Optional[dict[str, Any]]:
    """Extract supported calls from tagged, XML, bracketed, or plain tool syntax."""
    candidates = [match.group(1) for match in TOOL_TAG_PATTERN.finditer(text)]
    candidates.extend(match.group(1) for match in XML_TOOL_PATTERN.finditer(text))
    candidates.append(text)

    for candidate in candidates:
        stripped_candidate = candidate.strip()
        try:
            tool_payload = json.loads(stripped_candidate)
            if isinstance(tool_payload, list) and len(tool_payload) == 1:
                tool_payload = tool_payload[0]
            if isinstance(tool_payload, dict):
                function_payload = tool_payload.get("function", {})
                if not isinstance(function_payload, dict):
                    function_payload = {}
                action = (
                    tool_payload.get("action")
                    or tool_payload.get("name")
                    or tool_payload.get("tool_name")
                    or function_payload.get("name")
                )
                arguments = (
                    tool_payload.get("arguments")
                    or tool_payload.get("parameters")
                    or tool_payload.get("input")
                    or function_payload.get("arguments")
                    or {}
                )
                if isinstance(arguments, str):
                    arguments = json.loads(arguments)
                if isinstance(action, str) and isinstance(arguments, dict):
                    command = {"action": action.lower()}
                    command.update(arguments)
                    return command
        except (json.JSONDecodeError, TypeError):
            pass

        match = FUNCTION_CALL_PATTERN.search(candidate)
        if not match:
            continue

        action = match.group("action").lower()
        arguments = _parse_tool_arguments(match.group("args"))
        command = {"action": action}
        command.update(arguments)
        return command

    return None


def parse_and_execute_command(response_text: str) -> tuple[str, bool]:
    """Execute JSON or tool-call output and suppress all surrounding model text."""
    response_text = response_text or ""

    json_match = JSON_BLOCK_PATTERN.search(response_text)
    if json_match:
        try:
            action_data = json.loads(json_match.group(1))
            if not isinstance(action_data, dict):
                raise ValueError("Command JSON must be an object.")
            return system_ctrl.execute(action_data), True
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            print(f"[Command Parse Error]: {error}")
            return "دستور اجرایی نامعتبر است.", True

    try:
        action_data = _extract_tool_call(response_text)
        if action_data:
            if action_data.get("action") not in SUPPORTED_ACTIONS:
                raise ValueError("Unsupported tool action.")
            return system_ctrl.execute(action_data), True
    except ValueError as error:
        print(f"[Tool Call Parse Error]: {error}")
        return "دستور اجرایی نامعتبر است.", True

    return response_text, False


def read_setting(data: dict, key: str, fallback: str = "") -> str:
    if isinstance(data, dict) and key in data:
        value = data[key]
        if value is not None:
            return str(value).strip()
    return fallback


def match_assistant_state(user_text: str, config_data: dict) -> str | None:
    """Match local E.D.I.T.H. state phrases without treating them as Windows power."""
    normalized_input = normalize_trigger(user_text)
    keyword_map = config_data.get("assistant_state_keywords", {})
    if not isinstance(keyword_map, dict) or not normalized_input:
        return None

    for action in ("quit", "sleep", "awake"):
        phrases = keyword_map.get(action, [])
        if not isinstance(phrases, list):
            continue
        for phrase in phrases[:4]:
            normalized_phrase = normalize_trigger(phrase)
            if not normalized_phrase:
                continue
            # A wake word must be the entire input; otherwise “ادیت، کروم را باز کن”
            # should remain a command instead of being swallowed as a state change.
            if action == "awake" and normalized_phrase == normalized_input:
                return action
            if action != "awake" and normalized_phrase in normalized_input:
                return action
            if difflib.SequenceMatcher(
                None, normalized_input, normalized_phrase
            ).ratio() >= 0.93:
                return action
    return None


def handle_offline_input(user_message: str, config_data: dict) -> tuple[str, str] | None:
    """Run local custom commands and assistant-state changes before online AI."""
    custom_result = custom_command_manager.execute_if_matched(user_message)
    if custom_result is not None:
        return custom_result, "custom_command"

    state = match_assistant_state(user_message, config_data)
    if state == "sleep":
        return "دستیار در حالت استراحت قرار گرفت.", state
    if state == "awake":
        return "سیستم آماده است.", state
    if state == "quit":
        return "خداحافظ قربان.", state
    return None


def _history_limit(config_data: dict) -> int:
    try:
        return max(5, min(50, int(config_data.get("context_history_messages", 12))))
    except (TypeError, ValueError):
        return 12


def _append_history(role: str, content: str, config_data: dict) -> None:
    conversation_history.append({"role": role, "content": content})
    del conversation_history[:-_history_limit(config_data)]


def reset_conversation_history() -> None:
    """Start every application launch with a clean, in-memory conversation session."""
    conversation_history.clear()


def get_ai_response(user_message: str, config_data: dict) -> str:
    lowered = user_message.lower().strip()

    offline_result = handle_offline_input(user_message, config_data)
    if offline_result:
        return offline_result[0]

    if system_ctrl.pending_dangerous_action:
        if any(word in lowered for word in ["بله", "آره", "تایید", "تأیید", "انجام بده"]):
            return system_ctrl.execute({"action": "confirm_yes"})
        if any(word in lowered for word in ["نه", "خیر", "لغو", "کنسل", "بی‌خیال"]):
            return system_ctrl.execute({"action": "confirm_no"})

    token = read_setting(config_data, "api_key", "")
    endpoint = read_setting(config_data, "base_url")
    model_name = read_setting(config_data, "model_name")

    if not token:
        return "قربان، کلید API در تنظیمات وارد نشده است."

    if not endpoint:
        return "Base URL در تنظیمات وارد نشده است."
    if not model_name:
        return "Model Name در تنظیمات وارد نشده است."

    try:
        client = OpenAI(base_url=endpoint, api_key=token)
        current_local_time = datetime.now().astimezone().isoformat(timespec="minutes")
        completion = client.chat.completions.create(
            model=model_name,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"{get_system_prompt()}\n\n"
                        f"CURRENT LOCAL TIME: {current_local_time}"
                    ),
                },
                *conversation_history[-_history_limit(config_data):],
                {"role": "user", "content": user_message},
            ],
            temperature=0.5,
            max_tokens=250,
        )
        raw_response = completion.choices[0].message.content or ""
        final_text, _ = parse_and_execute_command(raw_response)
        _append_history("user", user_message, config_data)
        _append_history("assistant", final_text, config_data)
        return final_text
    except Exception as error:
        return f"خطا در برقراری ارتباط با مغز مرکزی: {error}"
