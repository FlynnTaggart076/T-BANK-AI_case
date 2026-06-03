from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
TELEGRAM_MESSAGE_LIMIT = 4096

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from job_agent.payload import agent_result_to_payload, clamp_top_n  # noqa: E402
from job_agent.pipeline import VacancyAgentPipeline  # noqa: E402
from job_agent.utils import load_env_file  # noqa: E402


class TelegramApiError(RuntimeError):
    """Raised when Telegram Bot API returns an unsuccessful response."""


class UserFacingError(ValueError):
    """A validation error that can be safely shown to the Telegram user."""


class JuniorScoutTelegramBot:
    def __init__(self, token: str, pipeline: VacancyAgentPipeline | None = None) -> None:
        self.token = token
        self.pipeline = pipeline or VacancyAgentPipeline()

    def run_forever(self) -> None:
        offset: int | None = None
        print("Junior Scout AI Telegram bot started. Press Ctrl+C to stop.")

        while True:
            try:
                updates = telegram_request(
                    self.token,
                    "getUpdates",
                    {
                        "offset": offset,
                        "timeout": 30,
                        "allowed_updates": ["message"],
                    },
                    timeout=45,
                )
            except Exception as exc:  # noqa: BLE001 - polling loop must survive network hiccups.
                print(f"Telegram polling failed: {exc}", file=sys.stderr)
                time.sleep(5)
                continue

            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = update_id + 1
                self.handle_update(update)

    def handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        if not isinstance(message, dict):
            return

        chat = message.get("chat")
        if not isinstance(chat, dict) or chat.get("id") is None:
            return

        chat_id = chat["id"]
        text = str(message.get("text") or "").strip()
        if not text:
            send_message(self.token, chat_id, "Пока умею работать только с текстовыми запросами.")
            return

        try:
            command = command_name(text)
            if command == "/start":
                send_message(self.token, chat_id, start_message())
                return
            if command == "/help":
                send_message(self.token, chat_id, help_message())
                return
            if command == "/top":
                query, top_n = parse_top_command(text)
                self.search_and_reply(chat_id, query, top_n)
                return
            if command == "/search":
                query = parse_search_command(text)
                self.search_and_reply(chat_id, query, top_n=5)
                return
            if command:
                send_message(self.token, chat_id, unknown_command_message())
                return

            self.search_and_reply(chat_id, text, top_n=5)
        except UserFacingError as exc:
            send_message(self.token, chat_id, str(exc))
        except Exception as exc:  # noqa: BLE001 - protect bot loop from one bad update.
            print(f"Update handling failed: {exc}", file=sys.stderr)
            send_message(
                self.token,
                chat_id,
                "Не получилось обработать запрос. Попробуй переформулировать его короче.",
            )

    def search_and_reply(self, chat_id: int | str, query: str, top_n: int) -> None:
        query = query.strip()
        if not query:
            raise UserFacingError("Напиши запрос после команды, например: /top 10 junior analyst Москва")

        top_n = clamp_top_n(top_n)
        send_chat_action(self.token, chat_id, "typing")
        send_message(
            self.token,
            chat_id,
            f"Принял запрос. Ищу топ-{top_n} и проверяю фактический опыт в карточках.",
        )

        try:
            result = self.pipeline.run(query, top_n=top_n)
            payload = agent_result_to_payload(result, top_n=top_n)
            response = format_telegram_response(payload)
        except Exception as exc:  # noqa: BLE001 - API boundary for Telegram user.
            print(f"Pipeline failed: {exc}", file=sys.stderr)
            send_message(
                self.token,
                chat_id,
                "Поиск сломался на стороне агента. Детали записаны в консоль процесса бота.",
            )
            return

        for chunk in split_message(response):
            send_message(self.token, chat_id, chunk)


def telegram_request(
    token: str,
    method: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 30,
) -> Any:
    payload = {key: value for key, value in (payload or {}).items() if value is not None}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        TELEGRAM_API.format(token=token, method=method),
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            envelope = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise TelegramApiError(f"HTTP {exc.code}: {details}") from exc
    except urllib.error.URLError as exc:
        raise TelegramApiError(f"network error: {exc.reason}") from exc

    if not envelope.get("ok"):
        raise TelegramApiError(str(envelope))
    return envelope.get("result")


def send_message(token: str, chat_id: int | str, text: str) -> None:
    telegram_request(
        token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        },
    )


def send_chat_action(token: str, chat_id: int | str, action: str) -> None:
    telegram_request(token, "sendChatAction", {"chat_id": chat_id, "action": action})


def command_name(text: str) -> str:
    first_token = text.split(maxsplit=1)[0].casefold()
    if not first_token.startswith("/"):
        return ""
    return first_token.split("@", maxsplit=1)[0]


def parse_top_command(text: str) -> tuple[str, int]:
    parts = text.split(maxsplit=2)
    if len(parts) < 3:
        raise UserFacingError("Формат команды: /top 10 junior аналитик Москва без опыта")

    try:
        top_n = int(parts[1])
    except ValueError as exc:
        raise UserFacingError("После /top нужно число от 1 до 25.") from exc

    return parts[2].strip(), clamp_top_n(top_n)


def parse_search_command(text: str) -> str:
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        raise UserFacingError("Формат команды: /search junior аналитик Москва без опыта")
    return parts[1].strip()


def start_message() -> str:
    return (
        "Junior Scout AI готов искать junior-вакансии и стажировки.\n\n"
        "Напиши запрос обычным текстом:\n"
        "Ищу junior позицию продуктового аналитика в Москве без опыта\n\n"
        "Команды:\n"
        "/top 10 <запрос> - вернуть от 1 до 25 вакансий\n"
        "/help - показать подсказку"
    )


def help_message() -> str:
    return (
        "Как пользоваться:\n"
        "1. Напиши роль, город, формат, уровень и важные навыки одним сообщением.\n"
        "2. Для другого размера выдачи используй /top N <запрос>, где N от 1 до 25.\n"
        "3. Агент проверяет не только фильтры источника, но и текст карточки: уровень, опыт, "
        "senior/middle-маркеры и явные противоречия."
    )


def unknown_command_message() -> str:
    return (
        "Такой команды пока нет. Напиши запрос обычным текстом или используй "
        "/top 10 <запрос>."
    )


def format_telegram_response(payload: dict[str, Any]) -> str:
    criteria = payload.get("criteria") or {}
    vacancies = payload.get("vacancies") or []
    trace = payload.get("trace") or []

    lines = ["Результат поиска", "", "Критерии:"]
    lines.extend(format_criteria(criteria))
    lines.append("")

    if not vacancies:
        lines.extend(
            [
                "Топ вакансий пуст.",
                "Строгие фильтры не пропустили вакансии: агент отсек неподходящую роль, город, "
                "уровень или фактический опыт из текста карточки.",
            ]
        )
    else:
        lines.append(f"Топ-{len(vacancies)} вакансий:")
        for index, vacancy in enumerate(vacancies, start=1):
            lines.extend(format_vacancy(index, vacancy))

    if trace:
        lines.extend(["", "Trace:"])
        for item in trace[:8]:
            lines.append(f"- {item}")

    report_path = payload.get("report_path")
    if report_path:
        lines.extend(["", f"Полный отчет: {report_path}"])

    return "\n".join(lines)


def format_criteria(criteria: dict[str, Any]) -> list[str]:
    return [
        f"Роль: {format_value(criteria.get('role'))}",
        f"Уровень: {format_value(criteria.get('level'))}",
        f"Город: {format_value(criteria.get('city'))}",
        f"Формат: {format_value(criteria.get('remote'))}",
        f"Навыки: {format_value(criteria.get('skills'))}",
        f"ЗП: {format_value(criteria.get('salary'))}",
    ]


def format_vacancy(index: int, vacancy: dict[str, Any]) -> list[str]:
    lines = [
        "",
        f"{index}. {format_value(vacancy.get('title'))}",
        f"{format_value(vacancy.get('company'))} | {format_value(vacancy.get('location'))}",
        f"ЗП: {format_value(vacancy.get('salary'))}",
        f"Score: {format_value(vacancy.get('score'))}%",
        f"Почему подходит: {shorten(format_value(vacancy.get('why')), 450)}",
        f"Риск: {shorten(format_value(vacancy.get('concern')), 350)}",
        f"Следующий шаг: {shorten(format_value(vacancy.get('next')), 350)}",
    ]
    link = str(vacancy.get("link") or "").strip()
    if link:
        lines.append(link)
    return lines


def format_value(value: Any) -> str:
    if isinstance(value, list):
        values = [str(item).strip() for item in value if str(item).strip()]
        return ", ".join(values) if values else "не указано"
    text = str(value or "").strip()
    return text if text else "не указано"


def shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT - 200) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_length = 0

    for line in text.splitlines():
        line_length = len(line) + 1
        if line_length > limit:
            if current:
                chunks.append("\n".join(current).strip())
                current = []
                current_length = 0
            chunks.extend(line[start : start + limit] for start in range(0, len(line), limit))
            continue

        if current and current_length + line_length > limit:
            chunks.append("\n".join(current).strip())
            current = [line]
            current_length = line_length
        else:
            current.append(line)
            current_length += line_length

    if current:
        chunks.append("\n".join(current).strip())

    return [chunk for chunk in chunks if chunk]


def load_token(env_file: Path | None = None) -> str:
    load_env_file(env_file)
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if token:
        return token

    raise RuntimeError(
        "Telegram token not found. Set TELEGRAM_BOT_TOKEN in the environment or .env file."
    )


def build_dry_run_response(query: str, top_n: int) -> str:
    pipeline = VacancyAgentPipeline()
    result = pipeline.run(query, top_n=clamp_top_n(top_n))
    payload = agent_result_to_payload(result, top_n=clamp_top_n(top_n))
    return format_telegram_response(payload)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Junior Scout AI Telegram bot")
    parser.add_argument("--env-file", default=str(PROJECT_ROOT / ".env"), help="Path to .env file")
    parser.add_argument("--dry-run", metavar="QUERY", help="Run pipeline once and print bot response")
    parser.add_argument("--top", default=5, type=int, help="Top size for --dry-run, from 1 to 25")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    if args.dry_run:
        print(build_dry_run_response(args.dry_run, args.top))
        return

    token = load_token(Path(args.env_file))
    JuniorScoutTelegramBot(token).run_forever()


if __name__ == "__main__":
    main()
