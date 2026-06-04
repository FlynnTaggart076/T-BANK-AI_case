from __future__ import annotations

from typing import Any

from .models import AgentResult, Criteria, ScoredVacancy, VacancyExplanation
from .render import format_salary
from .sources import source_label


def agent_result_to_payload(result: AgentResult, top_n: int, source: str = "trudvsem") -> dict[str, Any]:
    return {
        "criteria": criteria_to_payload(result.criteria),
        "vacancies": [vacancy_to_payload(item, result.explanations) for item in result.top],
        "trace": compact_trace(result.trace, len(result.vacancies), len(result.top)),
        "top_n": top_n,
        "source": source,
        "source_label": source_label(source),
        "report_path": result.report_path,
        "log_path": result.log_path,
    }


def criteria_to_payload(criteria: Criteria) -> dict[str, str | list[str]]:
    return {
        "role": join_or_empty(criteria.role_keywords),
        "level": join_or_empty(criteria.levels),
        "skills": criteria.skill_keywords or criteria.must_have or criteria.nice_to_have,
        "city": join_or_empty(criteria.cities),
        "remote": format_remote(criteria.remote),
        "salary": f"от {criteria.min_salary} ₽" if criteria.min_salary else "не указана",
    }


def vacancy_to_payload(
    item: ScoredVacancy,
    explanations: dict[str, VacancyExplanation],
) -> dict[str, Any]:
    vacancy = item.vacancy
    explanation = explanations.get(vacancy.external_id)
    concerns = ensure_text_list(explanation.concerns) if explanation else item.concerns
    matched = ensure_text_list(explanation.matched_requirements) if explanation else item.matched
    next_step = (
        sanitize_next_step(explanation.next_step)
        if explanation
        else "Открыть вакансию, проверить требования и подготовить короткий отклик."
    )

    return {
        "title": vacancy.title,
        "company": vacancy.company or "Компания не указана",
        "location": format_location(vacancy),
        "salary": format_salary_rub(vacancy.salary_min, vacancy.salary_max),
        "score": round(item.score),
        "why": format_matched(matched),
        "concern": format_concerns(concerns),
        "next": next_step,
        "link": vacancy.url,
        "source": vacancy.source,
        "source_label": source_label(vacancy.source),
    }


def format_location(vacancy: Any) -> str:
    location = vacancy.city or vacancy.region or "Локация не указана"
    format_parts = [part for part in [vacancy.employment, vacancy.schedule] if part]
    if format_parts:
        return f"{location} / {' / '.join(format_parts)}"
    return location


def format_salary_rub(salary_min: int | None, salary_max: int | None) -> str:
    value = format_salary(salary_min, salary_max)
    if value == "not specified":
        return "не указана"
    return value.replace("RUB", "₽")


def format_matched(items: list[str]) -> str:
    labels = [humanize_token(item) for item in items]
    labels = [item for item in labels if item]
    labels = dedupe(labels)
    if not labels:
        return "Есть частичное совпадение с запросом."
    return "Совпали критерии: " + ", ".join(labels[:6]) + "."


def format_concerns(items: list[str]) -> str:
    labels = [humanize_token(item) for item in items]
    labels = [item for item in labels if item]
    labels = dedupe(labels)
    if not labels:
        return "Явных рисков не найдено."
    return ", ".join(labels[:5]) + "."


def ensure_text_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def sanitize_next_step(value: Any) -> str:
    text = str(value or "").strip()
    normalized = text.casefold()
    bad_markers = [
        "не рассматривать",
        "не откликаться",
        "не подавать",
        "не подавайте",
        "искать другие вакансии",
        "рассмотреть другие вакансии",
        "пропустить вакансию",
        "отказаться",
    ]
    if not text or any(marker in normalized for marker in bad_markers):
        return "Открыть вакансию, проверить требования и зарплату, затем решить по отклику."
    return text


def humanize_token(value: Any) -> str:
    text = str(value).strip()
    if not text:
        return ""

    prefixes = {
        "role:": "",
        "title:exact": "название совпало с запросом",
        "title:tokens": "ключевые слова из названия совпали",
        "title-role:": "роль есть в названии: ",
        "skill:": "",
        "nice:": "",
        "level:": "",
        "city:": "город ",
        "format:": "",
        "fresh:7d": "свежая вакансия",
        "fresh:": "свежая вакансия",
        "stop-word:": "стоп-слово: ",
        "hard-mismatch:seniority:": "не junior-уровень: ",
        "hard-mismatch:experience:": "требуется опыт: ",
    }
    for prefix, label in prefixes.items():
        if text.startswith(prefix):
            tail = text[len(prefix) :].strip()
            return label + tail if tail else label.rstrip(": ")

    replacements = {
        "city": "город совпал",
        "remote": "формат совпал",
        "no_experience": "подходит без опыта",
        "роль не совпала явно": "роль не совпала явно",
        "уровень стажер/junior не подтвержден": "junior-уровень не подтвержден",
        "город не совпал явно": "город не совпал",
        "вакансия старше 45 дней": "вакансия старше 45 дней",
        "нет совпадения с целевой ролью": "нет совпадения с целевой ролью",
        "роль найдена только в описании, не в названии": "роль найдена только в описании, не в названии",
        "обязательный стек не найден в описании": "обязательный стек не найден",
    }
    return replacements.get(text, text)


def dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = item.casefold()
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def format_remote(value: bool | None) -> str:
    if value is True:
        return "да"
    if value is False:
        return "нет, нужен город"
    return "не указано"


def join_or_empty(items: list[str]) -> str:
    return ", ".join(items) if items else "не указано"


def clamp_top_n(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 5
    return max(1, min(25, number))


def compact_trace(raw_trace: list[str], considered_count: int, top_count: int) -> list[str]:
    trudvsem_requests = sum(1 for item in raw_trace if item.startswith("Trudvsem request"))
    superjob_requests = sum(1 for item in raw_trace if item.startswith("SuperJob request"))
    returned_items = 0
    duplicate_items = 0
    fallback_used = any("fallback enabled" in item for item in raw_trace)
    sources: list[str] = []

    for item in raw_trace:
        if item.startswith("Pipeline started: source="):
            source = item.split("=", 1)[-1].strip()
            label = source_label(source)
            if label not in sources:
                sources.append(label)
        if item.startswith("Trudvsem returned") or item.startswith("SuperJob returned"):
            parts = item.split()
            if len(parts) >= 3:
                try:
                    returned_items += int(parts[2])
                except ValueError:
                    pass
        if item.startswith("Drop duplicate vacancies:"):
            try:
                duplicate_items += int(item.split(":")[-1].strip().split()[0])
            except (ValueError, IndexError):
                pass
        elif item.startswith("Drop duplicate vacancy:"):
            duplicate_items += 1

    trace = [
        "Запрос принят",
        "Критерии извлечены",
        f"Источник: {', '.join(sources) if sources else 'Работа России'}",
        f"Запросов к Работа России: {trudvsem_requests}; к SuperJob: {superjob_requests}",
        f"Получено вакансий от источников: {returned_items}",
        f"Удалено дублей: {duplicate_items}",
        f"Проверены роль, город, уровень и фактический опыт: {considered_count} кандидатов",
    ]
    if fallback_used:
        trace.append("Локальный fallback подключен, но в топ попали только вакансии после строгой фильтрации")
    trace.append(f"Сформирован топ: {top_count}")
    return trace
