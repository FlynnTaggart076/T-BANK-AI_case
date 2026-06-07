from __future__ import annotations

import concurrent.futures
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import (
    Criteria,
    LLMBatchScoreItem,
    RequirementCheck,
    ScoredVacancy,
    Vacancy,
    VacancyDeepAnalysis,
    VacancyExplanation,
)
from .utils import clamp, load_env_file


DEFAULT_MODEL = "gpt-5.1"

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
    "с",
    "со",
    "для",
    "при",
    "можно",
    "удаленно",
    "удалённо",
    "remote",
    "руб",
    "рублей",
    "зарплата",
    "зарплатой",
    "зарплату",
    "оклад",
    "от",
    "до",
    "знаю",
    "умею",
    "навыки",
    "требования",
    "опытом",
    "категории",
    "категория",
    "разряда",
    "разряд",
    "класса",
    "класс",
    # Geographic suffixes that must never become part of a role name
    "область",
    "области",
    "областью",
    "областной",
    "край",
    "края",
    "крае",
    "краю",
    "округ",
    "округа",
    "округе",
    "округу",
    "район",
    "района",
    "районе",
    "району",
    "республика",
    "республики",
    "республике",
    "республику",
    "регион",
    "региона",
    "регионе",
    "региону",
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
        "Если пользователь вставил точное название вакансии, выдели базовую профессию в role_keywords, "
        "а уточнения из названия оставь для поискового запроса через исходный raw text; не превращай уточнение в город без явного признака города. "
        "Верни только JSON-объект с ключами: role_keywords, skill_keywords, cities, remote, levels, "
        "min_salary, max_age_days, stop_words, must_have, nice_to_have. "
        "role_keywords: конкретная профессия или должность из запроса в нормальной форме, без города, уровня, зарплаты "
        "и слов вакансии/работа/позиция. Например: 'Швея без опыта город Москва' -> ['швея']; "
        "'Ищу junior аналитика данных в Москве, знаю SQL' -> ['аналитик данных']. "
        "cities: города или регионы в **именительном падеже**, например ['Казань'] (не 'Казани'), "
        "['Москва'] (не 'Москве'), ['Липецкая область'] (не 'Липецкой области'), "
        "['Краснодарский край'] (не 'Краснодарском крае'), ['Санкт-Петербург'] (не 'Петербурге'). "
        "Никогда не добавляй город в role_keywords — даже частью: "
        "'Инженер-механик в Санкт-Петербурге' → role_keywords: ['инженер-механик'], cities: ['Санкт-Петербург']. "
        "'Учитель физики в Липецкой области' → role_keywords: ['учитель физики'], cities: ['Липецкая область']. "
        "Слова в скобках, например '(Север)', '(вахта)' или '(смена)', не считай городом, если рядом нет слов город/г./в/во. "
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


def normalize_llm_list_of_dicts(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def generate_search_filters(
    criteria: Criteria,
    llm: LLMClient,
) -> tuple[list[str], dict[str, Any], list[str]]:
    """Ask LLM to generate targeted search queries and API filter hints."""
    if not llm.available:
        return [], {}, ["LLM search filters skipped: no API key"]

    system = (
        "Ты формируешь поисковые запросы для API платформ Работа России и SuperJob. "
        "На основе критериев кандидата сгенерируй 2–4 точных поисковых строки: "
        "основная роль + синонимы + смежные должности (например, 'аналитик данных' → "
        "['аналитик данных', 'data analyst', 'BI-аналитик', 'продуктовый аналитик']). "
        "Строки должны быть короткими (1–3 слова), без города, уровня и зарплаты. "
        "Верни JSON: {\"queries\": [\"...\", ...], \"api_filters\": {"
        "\"experience\": \"noExperience|between1And3|between3And6|moreThan6|null\", "
        "\"employment\": \"full|part|project|volunteer|probation|null\", "
        "\"schedule\": \"fullDay|shift|flexible|remote|flyInFlyOut|null\"}}. "
        "api_filters используй только если явно следует из критериев, иначе null."
    )
    user = json.dumps(asdict(criteria), ensure_ascii=False)
    payload, trace_msg = llm.chat_json(system=system, user=user, fallback={"queries": [], "api_filters": {}})

    queries = _list(payload.get("queries")) if isinstance(payload, dict) else []
    api_filters = payload.get("api_filters") if isinstance(payload, dict) else {}
    if not isinstance(api_filters, dict):
        api_filters = {}

    return queries[:6], api_filters, [f"Search filters: {trace_msg}, generated_queries={queries[:4]}"]


def llm_batch_score(
    criteria: Criteria,
    vacancies: list[Vacancy],
    llm: LLMClient,
    batch_size: int = 15,
) -> tuple[list[ScoredVacancy], list[str]]:
    """Score vacancies in batches via LLM. Falls back to heuristic scoring if LLM unavailable."""
    from .scoring import apply_llm_scores, score_vacancies  # local import avoids circular

    if not vacancies:
        return [], []

    if not llm.available:
        return score_vacancies(criteria, vacancies), ["LLM batch score skipped: using heuristic fallback"]

    system = (
        "Ты рекрутинговый AI-скоринг. Оцени соответствие каждой вакансии профилю кандидата.\n\n"
        "КРИТИЧЕСКИЕ ПРАВИЛА (нарушение недопустимо):\n"
        "1. ГОРОД: если кандидат указал конкретный город (например, Москва), а вакансия явно "
        "находится в другом городе и это НЕ удалённая/дистанционная работа — score ≤ 12.\n"
        "2. РОЛЬ: если базовая должность принципиально отличается от целевой (аналитик ≠ менеджер, "
        "аналитик ≠ экономист, аналитик ≠ дизайнер, разработчик ≠ тестировщик) — score ≤ 20, "
        "даже если есть общие слова в названии. Общее прилагательное ('продуктовый') "
        "не делает роли совместимыми.\n\n"
        "Шкала score (0–100):\n"
        "0–25   — явно не подходит (критические несовпадения: уровень, роль, город)\n"
        "25–40  — слабое совпадение (роль близкая, но есть существенные расхождения)\n"
        "40–65  — хорошее совпадение (основное совпадает, мелкие вопросы)\n"
        "65–100 — отличное совпадение (всё или почти всё соответствует)\n\n"
        "Дополнительно: если вакансия требует 3+ года опыта, а кандидат без опыта — score ≤ 20.\n"
        "В concerns пиши только конкретные несоответствия с деталями из текста вакансии, без общих фраз.\n\n"
        "Верни JSON: {\"scores\": [{\"id\": \"<external_id>\", \"score\": <0-100>, "
        "\"verdict\": \"<одна фраза>\", \"matched\": [\"<конкретный пункт>\"], "
        "\"concerns\": [\"<несоответствие с деталью>\"]}]}"
    )

    batches = [vacancies[i : i + batch_size] for i in range(0, len(vacancies), batch_size)]
    all_score_items: list[LLMBatchScoreItem] = []
    traces: list[str] = []

    for batch_idx, batch in enumerate(batches):
        compact = [
            {
                "id": v.external_id,
                "title": v.title,
                "company": v.company,
                "experience": v.experience,
                "employment": v.employment,
                "schedule": v.schedule,
                "salary_min": v.salary_min,
                "salary_max": v.salary_max,
                "text": clamp(v.text, 600),
            }
            for v in batch
        ]
        user = json.dumps(
            {"candidate_profile": asdict(criteria), "vacancies": compact},
            ensure_ascii=False,
        )
        payload, trace_msg = llm.chat_json(system=system, user=user, fallback={"scores": []})
        traces.append(f"LLM score batch {batch_idx + 1}/{len(batches)}: {trace_msg}")

        raw_scores = payload.get("scores", []) if isinstance(payload, dict) else []
        for raw in raw_scores:
            try:
                item = LLMBatchScoreItem(
                    external_id=str(raw.get("id", "")),
                    score=int(raw.get("score", 0)),
                    verdict=str(raw.get("verdict", "")),
                    matched=normalize_llm_list(raw.get("matched")),
                    concerns=normalize_llm_list(raw.get("concerns")),
                )
                if item.external_id:
                    all_score_items.append(item)
            except (TypeError, ValueError):
                continue

    scored = apply_llm_scores(criteria, vacancies, all_score_items)
    return scored, traces


def deep_analyze_top(
    criteria: Criteria,
    top: list[ScoredVacancy],
    llm: LLMClient,
) -> tuple[dict[str, VacancyDeepAnalysis], list[str]]:
    """Deep-analyze each top vacancy individually (parallel requests) for rich advice."""
    if not top:
        return {}, []

    fallback = {item.vacancy.external_id: _fallback_deep_analysis(item) for item in top}

    if not llm.available:
        return fallback, ["LLM deep analysis skipped: no API key"]

    system = (
        "Ты опытный карьерный консультант. Проведи детальный анализ вакансии "
        "относительно профиля и запроса кандидата.\n\n"
        "Задачи:\n"
        "1. Проверь каждое требование вакансии — соответствует ли оно профилю кандидата.\n"
        "2. Найди скрытые несостыковки (например: вакансия называется 'junior', "
        "но в описании требуют 3 года опыта и знание сложной архитектуры).\n"
        "3. Найди красные флаги (нереалистичные требования, слишком размытые обязанности, "
        "явное несоответствие уровня).\n"
        "4. Дай конкретный персонализированный совет — называй конкретные требования, "
        "не пиши 'изучите требования' или 'подготовьте резюме'.\n\n"
        "Верни JSON: {\"external_id\": \"<id>\", \"overall_match\": <0-100>, "
        "\"requirement_check\": [{\"requirement\": \"<цитата>\", \"met\": true/false, "
        "\"comment\": \"<почему>\"}], "
        "\"red_flags\": [\"<конкретный красный флаг>\"], "
        "\"inconsistencies\": [\"<несостыковка с деталью>\"], "
        "\"specific_advice\": \"<конкретный персонализированный совет>\", "
        "\"final_recommendation\": \"apply|skip|caution\"}"
    )

    def analyze_one(
        item: ScoredVacancy,
    ) -> tuple[str, VacancyDeepAnalysis | None, str]:
        user = json.dumps(
            {
                "candidate_profile": asdict(criteria),
                "vacancy": {
                    "id": item.vacancy.external_id,
                    "title": item.vacancy.title,
                    "company": item.vacancy.company,
                    "experience": item.vacancy.experience,
                    "employment": item.vacancy.employment,
                    "schedule": item.vacancy.schedule,
                    "salary_min": item.vacancy.salary_min,
                    "salary_max": item.vacancy.salary_max,
                    "text": clamp(item.vacancy.text, 3000),
                },
            },
            ensure_ascii=False,
        )
        payload, trace_msg = llm.chat_json(system=system, user=user, fallback={})
        if not isinstance(payload, dict) or not payload:
            return item.vacancy.external_id, None, f"deep_analyze id={item.vacancy.external_id}: {trace_msg}"
        try:
            req_checks = [
                RequirementCheck(
                    requirement=str(r.get("requirement", "")),
                    met=bool(r.get("met", False)),
                    comment=str(r.get("comment", "")),
                )
                for r in normalize_llm_list_of_dicts(payload.get("requirement_check"))
                if r.get("requirement")
            ]
            analysis = VacancyDeepAnalysis(
                external_id=str(payload.get("external_id", item.vacancy.external_id)),
                overall_match=min(100, max(0, int(payload.get("overall_match", 0)))),
                requirement_check=req_checks,
                red_flags=normalize_llm_list(payload.get("red_flags")),
                inconsistencies=normalize_llm_list(payload.get("inconsistencies")),
                specific_advice=str(payload.get("specific_advice", "")),
                final_recommendation=str(payload.get("final_recommendation", "caution")),
            )
            return item.vacancy.external_id, analysis, f"deep_analyze id={item.vacancy.external_id}: {trace_msg}"
        except (TypeError, ValueError) as exc:
            return item.vacancy.external_id, None, f"deep_analyze parse error id={item.vacancy.external_id}: {exc}"

    result: dict[str, VacancyDeepAnalysis] = dict(fallback)
    traces: list[str] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(5, len(top))) as executor:
        future_map = {executor.submit(analyze_one, item): item for item in top}
        for future in concurrent.futures.as_completed(future_map):
            ext_id, analysis, trace_msg = future.result()
            traces.append(trace_msg)
            if analysis:
                result[ext_id] = analysis

    return result, traces


def _fallback_deep_analysis(item: ScoredVacancy) -> VacancyDeepAnalysis:
    hard = [c for c in item.concerns if c.startswith(("hard-mismatch:", "stop-word:"))]
    soft = [c for c in item.concerns if not c.startswith(("hard-mismatch:", "stop-word:"))]
    rec = "skip" if hard else ("caution" if item.score < 50 else "apply")
    return VacancyDeepAnalysis(
        external_id=item.vacancy.external_id,
        overall_match=int(min(item.score, 100)),
        requirement_check=[],
        red_flags=hard,
        inconsistencies=soft,
        specific_advice="Откройте вакансию, проверьте требования к опыту и подготовьте сопроводительное письмо.",
        final_recommendation=rec,
    )


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
        levels=levels,
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
    cities = normalize_extracted_cities(raw_query, _list(data.get("cities")) or fallback.cities)
    levels = _list(data.get("levels")) or fallback.levels

    criteria = Criteria(
        raw_query=raw_query,
        role_keywords=_list(data.get("role_keywords")) or fallback.role_keywords,
        skill_keywords=_list(data.get("skill_keywords")) or fallback.skill_keywords,
        cities=cities,
        remote=data.get("remote") if isinstance(data.get("remote"), bool) else fallback.remote,
        levels=levels,
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
    # Only add seniority stop-words when this is clearly a junior/no-experience search.
    # Otherwise we'd penalize "Главный бухгалтер" or "Старший инженер" queries.
    if _is_junior_query(criteria):
        criteria.stop_words = merge_unique(criteria.stop_words + ["senior", "lead", "middle"])
    else:
        criteria.stop_words = merge_unique(criteria.stop_words)
    return criteria


def _is_junior_query(criteria: Criteria) -> bool:
    text = " ".join(criteria.levels + [criteria.raw_query]).casefold()
    return any(
        m in text
        for m in ["junior", "джуниор", "стажер", "стажёр", "intern", "без опыта", "до года", "начинающий"]
    )


def infer_role_keywords(user_query: str) -> list[str]:
    text = strip_non_role_fragments(user_query).casefold()
    tokens = [normalize_role_token(token) for token in re.findall(r"[a-zа-яё+#-]+", text, flags=re.IGNORECASE)]
    role_tokens = [
        token
        for token in tokens
        if token
        and token not in ROLE_FILLER_WORDS
        and not is_level_term(token)
        and token not in _CITY_TOKEN_FORMS
        and not (len(token) == 1 and re.match(r"[a-z]", token))  # skip standalone letters like 'b', 'c'
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
        if token
        and token not in ROLE_FILLER_WORDS
        and not is_level_term(token)
        and token not in _CITY_TOKEN_FORMS
        and not (len(token) == 1 and re.match(r"[a-z]", token))
    ]
    return " ".join(role_tokens).strip()


# Minimal Russian soft-stem: strip common inflectional endings to approximate
# the nominative form. Used only for role token normalization.
def _soft_stem_ru(token: str) -> str:
    if not re.search(r"[а-яё]", token) or len(token) <= 4:
        return token
    for ending in (
        "ыми", "ими", "ого", "его", "ому", "ему",
        "ах", "ях", "ой", "ый", "ий", "ая", "ое", "ую",
        "ом", "ем", "ым", "им", "ам", "ям",
        "а", "я", "ы", "и", "у", "ю", "е", "о",
    ):
        if token.endswith(ending) and len(token) - len(ending) >= 4:
            return token[: -len(ending)]
    return token


def has_specific_role(roles: list[str]) -> bool:
    return any(role.casefold().strip() not in GENERIC_ROLE_TERMS for role in roles)


def normalize_role_token(value: str) -> str:
    return value.strip("-_ ")


# Words that indicate the captured text is NOT a city name.
_CITY_FALSE_POSITIVE_WORDS = {
    "не", "нет", "любом", "вашем", "любой", "важен", "важно", "указан",
    "указано", "указана", "нужен", "нужно", "разных", "другом",
    "любой", "дома", "месте", "место", "планете",
}


def infer_city_keywords(user_query: str) -> list[str]:
    # Pattern 1: 'город X' or 'г. X'
    pat_explicit = r"\b(?:город|г\.?)\s+([a-zа-яё-]+(?:\s+[a-zа-яё-]+){0,2})"
    # Pattern 2: 'в/во ИмяГорода [область/край/...]'
    pat_prep = (
        r"\b(?:в|во)\s+"
        r"([А-ЯЁ][а-яё-]+(?:[-\s][А-ЯЁ][а-яё-]+){0,2}"
        + _GEO_SUFFIX
        + r")"
    )
    cities: list[str] = []
    for pattern in (pat_explicit, pat_prep):
        for match in re.finditer(pattern, user_query, flags=re.IGNORECASE):
            raw = match.group(1).strip()
            city = clean_city_keyword(raw)
            city = normalize_city_to_nominative(city)
            if not city or len(city) < 3:
                continue
            if any(w in _CITY_FALSE_POSITIVE_WORDS for w in city.casefold().split()):
                continue
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


# Regex pattern for optional geographic suffix (oblast, kray, okrug, etc.)
_GEO_SUFFIX = (
    r"(?:\s+(?:"
    r"область|области|областию|"
    r"край|края|крае|краю|"
    r"округ|округа|округе|округу|"
    r"район|района|районе|району|"
    r"республика|республики|республике|"
    r"регион|региона|регионе"
    r"))?"
)


def strip_non_role_fragments(text: str) -> str:
    text = re.sub(r"\b(?:город|г\.?)\s+[a-zа-яё-]+(?:\s+[a-zа-яё-]+){0,2}", " ", text, flags=re.IGNORECASE)
    # Strip city/region references: 'в Липецкой области', 'в Санкт-Петербурге', 'во Владивостоке'
    text = re.sub(
        r"\b(?:в|во)\s+[А-ЯЁ][а-яё-]+(?:[-\s][А-ЯЁ][а-яё-]+){0,2}" + _GEO_SUFFIX,
        " ", text, flags=re.IGNORECASE,
    )
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


def normalize_extracted_cities(raw_query: str, cities: list[str]) -> list[str]:
    if not cities:
        return []

    query_without_parentheses = re.sub(r"\([^)]*\)", " ", raw_query)
    normalized_without_parentheses = query_without_parentheses.casefold()
    normalized_raw = raw_query.casefold()
    result: list[str] = []

    for city in cities:
        city = normalize_city_to_nominative(city)
        normalized_city = city.casefold().strip()
        if not normalized_city:
            continue

        city_only_in_parentheses = (
            normalized_city in normalized_raw
            and normalized_city not in normalized_without_parentheses
        )
        if city_only_in_parentheses:
            continue
        result.append(city)

    return merge_unique(result)


# Maps common Russian city forms (genitive, prepositional, dative) → nominative.
# Covers all cities with population 100k+.
CITY_OBLIQUE_TO_NOMINATIVE: dict[str, str] = {
    "москве": "Москва",
    "москвы": "Москва",
    "москву": "Москва",
    "санкт-петербурге": "Санкт-Петербург",
    "санкт-петербурга": "Санкт-Петербург",
    "петербурге": "Санкт-Петербург",
    "петербурга": "Санкт-Петербург",
    "питере": "Санкт-Петербург",
    "новосибирске": "Новосибирск",
    "новосибирска": "Новосибирск",
    "екатеринбурге": "Екатеринбург",
    "екатеринбурга": "Екатеринбург",
    "нижнем новгороде": "Нижний Новгород",
    "нижнего новгорода": "Нижний Новгород",
    "казани": "Казань",
    "челябинске": "Челябинск",
    "челябинска": "Челябинск",
    "омске": "Омск",
    "омска": "Омск",
    "самаре": "Самара",
    "самары": "Самара",
    "ростове-на-дону": "Ростов-на-Дону",
    "ростове": "Ростов-на-Дону",
    "уфе": "Уфа",
    "уфы": "Уфа",
    "красноярске": "Красноярск",
    "красноярска": "Красноярск",
    "воронеже": "Воронеж",
    "воронежа": "Воронеж",
    "перми": "Пермь",
    "волгограде": "Волгоград",
    "волгограда": "Волгоград",
    "краснодаре": "Краснодар",
    "краснодара": "Краснодар",
    "саратове": "Саратов",
    "саратова": "Саратов",
    "тюмени": "Тюмень",
    "ижевске": "Ижевск",
    "ижевска": "Ижевск",
    "барнауле": "Барнаул",
    "барнаула": "Барнаул",
    "ульяновске": "Ульяновск",
    "ульяновска": "Ульяновск",
    "иркутске": "Иркутск",
    "иркутска": "Иркутск",
    "хабаровске": "Хабаровск",
    "хабаровска": "Хабаровск",
    "владивостоке": "Владивосток",
    "владивостока": "Владивосток",
    "ярославле": "Ярославль",
    "ярославля": "Ярославль",
    "томске": "Томск",
    "томска": "Томск",
    "оренбурге": "Оренбург",
    "оренбурга": "Оренбург",
    "новокузнецке": "Новокузнецк",
    "новокузнецка": "Новокузнецк",
    "рязани": "Рязань",
    "астрахани": "Астрахань",
    "набережных челнах": "Набережные Челны",
    "набережных челнов": "Набережные Челны",
    "пензе": "Пенза",
    "пензы": "Пенза",
    "липецке": "Липецк",
    "липецка": "Липецк",
    "кирове": "Киров",
    "кирова": "Киров",
    "чебоксарах": "Чебоксары",
    "чебоксар": "Чебоксары",
    "туле": "Тула",
    "тулы": "Тула",
    "балашихе": "Балашиха",
    "балашихи": "Балашиха",
    "твери": "Тверь",
    "калуге": "Калуга",
    "калуги": "Калуга",
    "ставрополе": "Ставрополь",
    "ставрополя": "Ставрополь",
    "белгороде": "Белгород",
    "белгорода": "Белгород",
    "владимире": "Владимир",
    "владимира": "Владимир",
    "симферополе": "Симферополь",
    "симферополя": "Симферополь",
    "брянске": "Брянск",
    "брянска": "Брянск",
    "курске": "Курск",
    "курска": "Курск",
    "магнитогорске": "Магнитогорск",
    "магнитогорска": "Магнитогорск",
    "нижнем тагиле": "Нижний Тагил",
    "нижнего тагила": "Нижний Тагил",
    "иванове": "Иваново",
    "иванова": "Иваново",
    "химках": "Химки",
    "химок": "Химки",
    "сургуте": "Сургут",
    "сургута": "Сургут",
    "архангельске": "Архангельск",
    "архангельска": "Архангельск",
    "чите": "Чита",
    "читы": "Чита",
    "смоленске": "Смоленск",
    "смоленска": "Смоленск",
    "калининграде": "Калининград",
    "калининграда": "Калининград",
    "мурманске": "Мурманск",
    "мурманска": "Мурманск",
    "пскове": "Псков",
    "пскова": "Псков",
    "великом новгороде": "Великий Новгород",
    "великого новгорода": "Великий Новгород",
    "якутске": "Якутск",
    "якутска": "Якутск",
    "петрозаводске": "Петрозаводск",
    "петрозаводска": "Петрозаводск",
    "южно-сахалинске": "Южно-Сахалинск",
    "южно-сахалинска": "Южно-Сахалинск",
    "комсомольске-на-амуре": "Комсомольск-на-Амуре",
    "улан-удэ": "Улан-Удэ",
    "махачкале": "Махачкала",
    "махачкалы": "Махачкала",
    "грозном": "Грозный",
    "грозного": "Грозный",
    "астане": "Астана",
    "алматы": "Алматы",
    "минске": "Минск",
    "минска": "Минск",
}


def normalize_city_to_nominative(city: str) -> str:
    """Convert a city/region name from any oblique case to nominative."""
    if not city:
        return city
    key = city.casefold().strip()
    # Direct dictionary lookup (covers city forms)
    nominative = CITY_OBLIQUE_TO_NOMINATIVE.get(key)
    if nominative:
        return nominative
    # Region pattern: adjective + geographic noun (e.g. 'Липецкой области')
    region = _normalize_region_phrase(city)
    if region != city:
        return region
    return city


_REGION_NOUNS: dict[str, str] = {
    "область": "область",
    "области": "область",
    "областью": "область",
    "край": "край",
    "края": "край",
    "крае": "край",
    "краю": "край",
    "округ": "округ",
    "округа": "округ",
    "округе": "округ",
    "округу": "округ",
    "район": "район",
    "района": "район",
    "районе": "район",
    "республика": "республика",
    "республики": "республика",
    "республике": "республика",
    "регион": "регион",
    "региона": "регион",
    "регионе": "регион",
}

# Adjective ending conversions: oblique (gen/prep/dat) -> nominative
_ADJ_ENDINGS: list[tuple[str, str]] = [
    ("ского", "ский"),  # Краснодарского → Краснодарский
    ("ском", "ский"),   # Краснодарском → Краснодарский
    ("ской", "ская"),   # Липецкой → Липецкая
    ("скому", "ский"), # dative
    ("ного", "ный"),    # Ижевсконого → ... (fallback)
    ("ном", "ный"),     # препозициональ
    ("ной", "ная"),     # Липецконой → Липецконая (редко)
    ("ого", "ый"),      # Нижегородского... fallback
    ("ом", "ый"),       # prepositional masc/neut
    ("ой", "ая"),       # Липецкой → Липецкая (short fallback)
    ("ей", "яя"),       # Тверской / Тверская
]


def _adj_to_nominative(adj: str) -> str:
    """Convert Russian adjective from any oblique case to approximate nominative."""
    lower = adj.casefold()
    prefix = adj[0].upper() + adj[1:] if adj else adj  # preserve capitalisation
    for old, new in _ADJ_ENDINGS:
        if lower.endswith(old) and len(lower) - len(old) >= 3:
            stem = adj[: len(adj) - len(old)]
            return stem[0].upper() + stem[1:] + new
    return prefix


def _normalize_region_phrase(text: str) -> str:
    """Convert region phrase like 'Липецкой области' -> 'Липецкая область'."""
    parts = text.strip().split()
    if len(parts) < 2:
        return text
    noun_lower = parts[-1].casefold()
    noun_nom = _REGION_NOUNS.get(noun_lower)
    if not noun_nom:
        return text
    adj_nom = _adj_to_nominative(" ".join(parts[:-1]))
    return f"{adj_nom} {noun_nom}"


# Flat set of all city token forms (keys + lowercased values) for fast role-token filtering.
_CITY_TOKEN_FORMS: frozenset[str] = frozenset(
    list(CITY_OBLIQUE_TO_NOMINATIVE.keys())
    + [v.casefold() for v in CITY_OBLIQUE_TO_NOMINATIVE.values()]
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
