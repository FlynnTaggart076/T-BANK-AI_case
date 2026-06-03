from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import Criteria, ScoredVacancy, VacancyExplanation
from .utils import clamp, load_env_file


DEFAULT_MODEL = "gpt-4o-mini"

LEVEL_TERMS = {
    "junior",
    "джуниор",
    "стажер",
    "стажёр",
    "intern",
    "internship",
    "без опыта",
    "без опыта работы",
    "до года",
}

PRODUCT_ANALYST_ROLES = [
    "продуктовый аналитик",
    "аналитик продукта",
    "product analyst",
]

PRODUCT_ANALYST_NICE_SKILLS = [
    "sql",
    "python",
    "excel",
    "метрики",
    "a/b",
    "ab-тест",
    "дашборд",
]


class LLMClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: int = 45,
    ) -> None:
        load_env_file()
        self.api_key = api_key or load_openai_key()
        self.model = model or os.getenv("OPENAI_MODEL") or DEFAULT_MODEL
        self.timeout_seconds = timeout_seconds

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def chat_json(self, system: str, user: str, fallback: Any) -> tuple[Any, str]:
        if not self.api_key:
            return fallback, "LLM skipped: OpenAI API key not found"

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            return fallback, f"LLM error: {exc}"

        try:
            content = raw["choices"][0]["message"]["content"]
            return json.loads(content), f"LLM ok: model={self.model}"
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            return fallback, f"LLM parse error: {exc}"


def load_openai_key(path: Path | None = None) -> str | None:
    load_env_file(path)
    env_value = os.getenv("OPENAI_API_KEY")
    if env_value:
        return env_value.strip()
    return None


def extract_criteria(user_query: str, llm: LLMClient) -> tuple[Criteria, list[str]]:
    fallback = heuristic_criteria(user_query)
    system = (
        "Ты извлекаешь критерии поиска вакансий из свободного запроса кандидата. "
        "Верни только JSON-объект с ключами: role_keywords, skill_keywords, cities, "
        "remote, levels, min_salary, max_age_days, stop_words, must_have, nice_to_have. "
        "remote должен быть true, false или null. min_salary может быть null."
    )
    payload, trace = llm.chat_json(system=system, user=user_query, fallback=asdict(fallback))
    criteria = criteria_from_dict(user_query, payload)
    return criteria, [trace]


def explain_top(
    criteria: Criteria,
    top: list[ScoredVacancy],
    llm: LLMClient,
) -> tuple[dict[str, VacancyExplanation], list[str]]:
    fallback = fallback_explanations(top)
    compact = [
        {
            "id": item.vacancy.external_id,
            "title": item.vacancy.title,
            "company": item.vacancy.company,
            "score": item.score,
            "matched": item.matched,
            "concerns": item.concerns,
            "text": clamp(item.vacancy.text, 1200),
            "url": item.vacancy.url,
        }
        for item in top
    ]
    system = (
        "Ты карьерный ассистент. Объясни, почему вакансии подходят или не подходят "
        "кандидату. Верни только JSON: {\"items\":[...]} с полями external_id, "
        "suitability, matched_requirements, concerns, next_step, priority. "
        "Пиши кратко, по делу, на русском."
    )
    user = json.dumps(
        {"criteria": asdict(criteria), "vacancies": compact},
        ensure_ascii=False,
    )
    payload, trace = llm.chat_json(system=system, user=user, fallback={"items": []})
    items = payload.get("items", []) if isinstance(payload, dict) else []
    explanations: dict[str, VacancyExplanation] = {}

    for item in items:
        try:
            explanation = VacancyExplanation(
                external_id=str(item.get("external_id", "")),
                suitability=str(item.get("suitability", "")),
                matched_requirements=normalize_llm_list(item.get("matched_requirements")),
                concerns=normalize_llm_list(item.get("concerns")),
                next_step=str(item.get("next_step", "")),
                priority=str(item.get("priority", "")),
            )
        except TypeError:
            continue
        if explanation.external_id:
            explanations[explanation.external_id] = explanation

    fallback.update(explanations)
    return fallback, [trace]


def normalize_llm_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def heuristic_criteria(user_query: str) -> Criteria:
    text = user_query.casefold()
    skills = _find_keywords(
        text,
        [
            "python",
            "sql",
            "postgresql",
            "postgres",
            "fastapi",
            "django",
            "flask",
            "pandas",
            "numpy",
            "airflow",
            "docker",
            "git",
            "excel",
            "tableau",
            "power bi",
            "product analytics",
            "data analysis",
            "ml",
        ],
    )
    roles = _find_keywords(
        text,
        [
            "продуктовый аналитик",
            "аналитик продукта",
            "product analyst",
            "backend",
            "python backend",
            "разработчик",
            "аналитик",
            "data analyst",
            "qa",
            "тестировщик",
        ],
    )
    cities = _find_keywords(text, ["москва", "санкт-петербург", "спб"])
    levels = _find_keywords(text, ["стажер", "стажёр", "junior", "джуниор", "без опыта"])
    remote = None
    if any(word in text for word in ["удален", "удалён", "remote", "дистанц"]):
        remote = True
    salary_match = re.search(r"(?:от|salary|min)?\s*(\d{2,3})\s*(?:к|k|тыс|000)", text)
    min_salary = None
    if salary_match:
        min_salary = int(salary_match.group(1)) * 1000

    criteria = Criteria(
        raw_query=user_query,
        role_keywords=roles or ["стажер", "junior"],
        skill_keywords=skills,
        cities=cities,
        remote=remote,
        levels=levels or ["стажер", "junior", "без опыта"],
        min_salary=min_salary,
        max_age_days=45,
        stop_words=["senior", "lead", "middle", "ведущий", "руководитель"],
        must_have=skills[:3],
        nice_to_have=skills[3:],
    )
    return normalize_criteria(criteria)


def criteria_from_dict(raw_query: str, data: Any) -> Criteria:
    if not isinstance(data, dict):
        return heuristic_criteria(raw_query)

    fallback = heuristic_criteria(raw_query)
    criteria = Criteria(
        raw_query=raw_query,
        role_keywords=_list(data.get("role_keywords")) or fallback.role_keywords,
        skill_keywords=_list(data.get("skill_keywords")) or fallback.skill_keywords,
        cities=_list(data.get("cities")) or fallback.cities,
        remote=data.get("remote") if isinstance(data.get("remote"), bool) else fallback.remote,
        levels=_list(data.get("levels")) or fallback.levels,
        min_salary=_optional_int(data.get("min_salary")) or fallback.min_salary,
        max_age_days=_optional_int(data.get("max_age_days")) or fallback.max_age_days,
        stop_words=_list(data.get("stop_words")) or fallback.stop_words,
        must_have=_list(data.get("must_have")) or fallback.must_have,
        nice_to_have=_list(data.get("nice_to_have")) or fallback.nice_to_have,
    )
    return normalize_criteria(criteria)


def normalize_criteria(criteria: Criteria) -> Criteria:
    raw = criteria.raw_query.casefold()
    roles: list[str] = []
    levels = list(criteria.levels)
    skill_keywords, extra_levels = split_level_terms(criteria.skill_keywords)
    must_have, more_levels = split_level_terms(criteria.must_have)
    nice_to_have, nice_levels = split_level_terms(criteria.nice_to_have)
    levels.extend(extra_levels + more_levels + nice_levels)

    for role in criteria.role_keywords:
        normalized = role.casefold().strip()
        if is_level_term(normalized):
            levels.append(role)
        else:
            roles.append(role)

    if is_product_analyst_intent(raw, roles):
        roles = merge_unique(PRODUCT_ANALYST_ROLES + roles)
        if not skill_keywords and not must_have:
            nice_to_have = merge_unique(nice_to_have + PRODUCT_ANALYST_NICE_SKILLS)

    if not roles:
        roles = ["стажер", "junior"]

    criteria.role_keywords = merge_unique(roles)
    criteria.skill_keywords = merge_unique(skill_keywords)
    criteria.must_have = merge_unique(must_have)
    criteria.nice_to_have = merge_unique(nice_to_have)
    criteria.levels = merge_unique(levels)
    criteria.stop_words = merge_unique(criteria.stop_words + ["senior", "lead", "middle"])
    return criteria


def is_product_analyst_intent(raw_query: str, roles: list[str]) -> bool:
    role_text = " ".join(roles).casefold()
    text = f"{raw_query} {role_text}"
    return (
        "продукт" in text
        and "аналит" in text
        or "product analyst" in text
        or "аналитик продукта" in text
    )


def fallback_explanations(top: list[ScoredVacancy]) -> dict[str, VacancyExplanation]:
    result: dict[str, VacancyExplanation] = {}
    for item in top:
        result[item.vacancy.external_id] = VacancyExplanation(
            external_id=item.vacancy.external_id,
            suitability=(
                "Подходит по базовому скорингу: есть совпадения с ролью, стеком или форматом."
            ),
            matched_requirements=item.matched[:6],
            concerns=item.concerns[:5],
            next_step="Открыть вакансию, проверить требования к опыту и подготовить короткий отклик.",
            priority="high" if item.score >= 60 else "medium" if item.score >= 35 else "low",
        )
    return result


def _find_keywords(text: str, keywords: list[str]) -> list[str]:
    return [keyword for keyword in keywords if keyword in text]


def split_level_terms(values: list[str]) -> tuple[list[str], list[str]]:
    kept: list[str] = []
    levels: list[str] = []
    for value in values:
        normalized = str(value).casefold().strip()
        if is_level_term(normalized):
            levels.append(str(value).strip())
        else:
            kept.append(str(value).strip())
    return kept, levels


def is_level_term(value: str) -> bool:
    return value in LEVEL_TERMS or ("без опыта" in value) or ("до года" in value)


def merge_unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = str(value).strip()
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
