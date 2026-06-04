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

GENERIC_ROLE_TERMS = {
    "junior",
    "джуниор",
    "стажер",
    "стажёр",
    "стажировка",
    "intern",
    "internship",
}

ROLE_FILLER_WORDS = {
    "ищу",
    "ищем",
    "найти",
    "нужна",
    "нужен",
    "нужно",
    "вакансию",
    "вакансия",
    "работу",
    "работа",
    "позицию",
    "должность",
    "без",
    "опыта",
    "работы",
    "город",
    "в",
    "во",
    "на",
    "и",
    "или",
    "можно",
    "удаленно",
    "удалённо",
    "remote",
    "руб",
    "рублей",
    "зарплата",
    "от",
    "до",
    "знаю",
    "умею",
    "навыки",
    "требования",
    "junior",
    "джуниор",
    "стажер",
    "стажёр",
    "стажировка",
    "intern",
    "internship",
}


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
        "Профессия может быть любой: IT, производство, медицина, сервис, офис, рабочие специальности. "
        "Не используй закрытый список профессий и не подменяй профессию общими словами вроде junior или стажер. "
        "Верни только JSON-объект с ключами: role_keywords, skill_keywords, cities, remote, levels, "
        "min_salary, max_age_days, stop_words, must_have, nice_to_have. "
        "role_keywords: конкретная профессия или должность из запроса в нормальной форме, без города, уровня, зарплаты "
        "и слов вакансии/работа/позиция. Например: 'Швея без опыта город Москва' -> ['швея']; "
        "'Ищу junior аналитика данных в Москве, знаю SQL' -> ['аналитик данных']. "
        "cities: города или регионы в нормальной форме, например ['Москва']; не добавляй город в role_keywords. "
        "levels: junior/стажер/без опыта/до года и похожие ограничения по опыту. "
        "skill_keywords, must_have и nice_to_have заполняй только навыками, явно указанными в запросе. "
        "remote должен быть true, false или null. min_salary может быть null. max_age_days по умолчанию 45."
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
    roles = infer_role_keywords(user_query)
    cities = infer_city_keywords(user_query)
    levels = infer_level_keywords(text)
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
            cleaned_role = clean_role_keyword(role)
            if cleaned_role:
                roles.append(cleaned_role)

    inferred_roles = infer_role_keywords(criteria.raw_query)
    if inferred_roles and (not roles or not has_specific_role(roles)):
        roles = merge_unique(inferred_roles + roles)

    if not roles:
        roles = ["стажер", "junior"]

    criteria.role_keywords = merge_unique(roles)
    criteria.skill_keywords = merge_unique(skill_keywords)
    criteria.must_have = merge_unique(must_have)
    criteria.nice_to_have = merge_unique(nice_to_have)
    criteria.levels = merge_unique(levels)
    criteria.stop_words = merge_unique(criteria.stop_words + ["senior", "lead", "middle"])
    return criteria


def infer_role_keywords(user_query: str) -> list[str]:
    text = strip_non_role_fragments(user_query).casefold()
    tokens = [normalize_role_token(token) for token in re.findall(r"[a-zа-яё+#-]+", text, flags=re.IGNORECASE)]
    role_tokens = [
        token
        for token in tokens
        if token and token not in ROLE_FILLER_WORDS and not is_level_term(token)
    ]
    if not role_tokens:
        return []

    return [" ".join(role_tokens[:3])]


def clean_role_keyword(value: Any) -> str:
    text = strip_non_role_fragments(str(value or "")).casefold()
    tokens = [normalize_role_token(token) for token in re.findall(r"[a-zа-яё+#-]+", text, flags=re.IGNORECASE)]
    role_tokens = [
        token
        for token in tokens
        if token and token not in ROLE_FILLER_WORDS and not is_level_term(token)
    ]
    return " ".join(role_tokens).strip()


def has_specific_role(roles: list[str]) -> bool:
    return any(role.casefold().strip() not in GENERIC_ROLE_TERMS for role in roles)


def normalize_role_token(value: str) -> str:
    return value.strip("-_ ")


def infer_city_keywords(user_query: str) -> list[str]:
    patterns = [
        r"\b(?:город|г\.?)\s+([a-zа-яё-]+(?:\s+[a-zа-яё-]+){0,2})",
        r"\b(?:в|во)\s+([А-ЯЁ][а-яё-]+(?:[-\s][А-ЯЁ][а-яё-]+){0,2})",
    ]
    cities: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, user_query, flags=re.IGNORECASE):
            city = clean_city_keyword(match.group(1))
            if city:
                cities.append(city)
    return merge_unique(cities)


def infer_level_keywords(text: str) -> list[str]:
    result: list[str] = []
    if "без опыта" in text:
        result.append("без опыта")
    if "до года" in text:
        result.append("до года")
    for term in LEVEL_TERMS:
        if term in text:
            result.append(term)
    return merge_unique(result)


def strip_non_role_fragments(text: str) -> str:
    text = re.sub(r"\b(?:город|г\.?)\s+[a-zа-яё-]+(?:\s+[a-zа-яё-]+){0,2}", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:в|во)\s+[А-ЯЁ][а-яё-]+(?:[-\s][А-ЯЁ][а-яё-]+){0,2}", " ", text)
    text = re.sub(r"\b(?:знаю|умею|навыки|стек|технологии)\b.*", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:без опыта|без опыта работы|до года|junior|джуниор|стаж[её]р|стажировка|intern(?:ship)?|remote)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:удал[её]нн?о|дистанционно|зарплата|оклад|от|до)\b\s*\d*[\s\d]*(?:к|k|тыс|000|руб(?:лей)?)?", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b\d+[\s\d]*(?:к|k|тыс|000|руб(?:лей)?)?\b", " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def clean_city_keyword(value: str) -> str:
    text = re.split(
        r"\b(?:без|знаю|умею|навыки|зарплата|оклад|от|до|можно|удал[её]нн?о|remote|junior|стаж[её]р)\b",
        value,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    return re.sub(r"\s+", " ", text).strip(" ,.;:-")


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
