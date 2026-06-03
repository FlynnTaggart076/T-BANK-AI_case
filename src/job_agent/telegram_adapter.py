from __future__ import annotations

from .service import run_agent


def build_telegram_message(user_query: str) -> str:
    """Framework-agnostic helper for a future aiogram handler."""
    result = run_agent(user_query)
    lines = ["Топ вакансий:"]
    for index, item in enumerate(result.top, start=1):
        vacancy = item.vacancy
        lines.append(f"{index}. {vacancy.title} — {vacancy.company}")
        lines.append(f"Score: {item.score}")
        lines.append(vacancy.url)
    lines.append(f"Полный отчет: {result.report_path}")
    return "\n".join(lines)
