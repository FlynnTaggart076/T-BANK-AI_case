from __future__ import annotations

from datetime import date
import re

from .models import Criteria, LLMBatchScoreItem, ScoredVacancy, Vacancy
from .utils import contains_any


DEFAULT_STOP_WORDS = [
    "senior",
    "сеньор",
    "lead",
    "лид",
    "principal",
    "middle",
    "мидл",
    "руководитель",
    "тимлид",
    "team lead",
    "ведущий",
    "главный",
    "старший",
    "3 года",
    "от 3 лет",
    "5 лет",
]

JUNIOR_WORDS = [
    "стажер",
    "стажёр",
    "intern",
    "internship",
    "junior",
    "джуниор",
    "младший",
    "без опыта",
    "начинающий",
]

REMOTE_WORDS = ["удален", "удалён", "remote", "дистанц", "на дому"]

SENIORITY_MARKERS = [
    "senior",
    "сеньор",
    "middle",
    "мидл",
    "lead",
    "лид",
    "principal",
    "ведущий",
    "старший",
    "главный",
    "руководитель",
]

MATCH_FILLER_WORDS = {
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
    "город",
    "в",
    "во",
    "на",
    "по",
    "для",
    "с",
    "со",
    "без",
    "опыта",
    "работы",
    "можно",
    "удаленно",
    "удалённо",
    "remote",
}


def validate_and_dedupe(vacancies: list[Vacancy]) -> tuple[list[Vacancy], list[str]]:
    trace: list[str] = []
    valid: list[Vacancy] = []
    seen: set[str] = set()
    seen_fingerprints: set[str] = set()
    broken_count = 0
    duplicate_count = 0

    for vacancy in vacancies:
        if not vacancy.title or not vacancy.url:
            broken_count += 1
            if broken_count <= 5:
                trace.append(f"Drop broken vacancy: id={vacancy.external_id or '<empty>'}")
            continue
        key = vacancy.url.casefold() or f"{vacancy.source}:{vacancy.external_id}"
        fingerprint = vacancy_fingerprint(vacancy)
        if key in seen or (fingerprint and fingerprint in seen_fingerprints):
            duplicate_count += 1
            if duplicate_count <= 5:
                trace.append(f"Drop duplicate vacancy: {vacancy.title} ({vacancy.url})")
            continue
        seen.add(key)
        if fingerprint:
            seen_fingerprints.add(fingerprint)
        valid.append(vacancy)

    if broken_count > 5:
        trace.append(f"Drop broken vacancies: {broken_count - 5} more")
    if duplicate_count > 5:
        trace.append(f"Drop duplicate vacancies: {duplicate_count - 5} more")
    trace.append(f"Validation kept {len(valid)} of {len(vacancies)} vacancies")
    return valid, trace


def score_vacancies(criteria: Criteria, vacancies: list[Vacancy]) -> list[ScoredVacancy]:
    scored = [_score_one(criteria, vacancy) for vacancy in vacancies]
    scored.sort(key=lambda item: item.score, reverse=True)
    return scored


def apply_llm_scores(
    criteria: Criteria,
    vacancies: list[Vacancy],
    llm_scores: list[LLMBatchScoreItem],
) -> list[ScoredVacancy]:
    """Merge LLM batch scores with vacancies. Falls back to heuristic for any vacancy not covered."""
    score_map = {item.external_id: item for item in llm_scores}
    result: list[ScoredVacancy] = []
    for vacancy in vacancies:
        llm_item = score_map.get(vacancy.external_id)
        if llm_item:
            result.append(
                ScoredVacancy(
                    vacancy=vacancy,
                    score=float(llm_item.score),
                    matched=llm_item.matched,
                    concerns=llm_item.concerns,
                    filtered_out=llm_item.score < 40,
                )
            )
        else:
            result.append(_score_one(criteria, vacancy))
    result.sort(key=lambda item: item.score, reverse=True)
    return result


def _score_one(criteria: Criteria, vacancy: Vacancy) -> ScoredVacancy:
    text = normalize_match_text(vacancy.text)
    title_text = normalize_match_text(vacancy.title)
    # Use role_keywords (not raw_query) for title matching to avoid city/level contamination
    role_query_text = normalize_match_text(" ".join(criteria.role_keywords))
    matched: list[str] = []
    concerns: list[str] = []
    score = 0.0
    required_skill_words = _unique(criteria.skill_keywords + criteria.must_have)
    nice_skill_words = _unique(criteria.nice_to_have)
    specific_role_words = _specific_role_words(criteria.role_keywords)
    city_matches: list[str] = []

    role_matches = contains_any_match(text, criteria.role_keywords)
    role_title_matches = contains_any_match(title_text, criteria.role_keywords)
    if role_matches:
        score += 30 + min(len(role_matches), 3) * 6
        matched.extend(f"role:{item}" for item in role_matches[:3])
        if role_title_matches:
            score += 8
            matched.append(f"title-role:{role_title_matches[0]}")
    else:
        score -= 30
        concerns.append("роль не совпала явно")

    title_match = title_query_match(role_query_text, title_text)
    if title_match == "exact":
        score += 28
        matched.append("title:exact")
    elif title_match == "tokens":
        score += 18
        matched.append("title:tokens")

    if role_matches and not role_title_matches and not title_match:
        score -= 18
        concerns.append("роль найдена только в описании, не в названии")

    required_skill_matches = contains_any_match(text, required_skill_words)
    nice_skill_matches = contains_any_match(text, nice_skill_words)
    if required_skill_words and required_skill_matches:
        score += min(len(required_skill_matches), 6) * 10
        matched.extend(f"skill:{item}" for item in required_skill_matches[:6])
    elif required_skill_words:
        score -= 25
        concerns.append("обязательный стек не найден в описании")

    if nice_skill_matches:
        score += min(len(nice_skill_matches), 5) * 4
        matched.extend(f"nice:{item}" for item in nice_skill_matches[:5])

    level_terms = criteria.levels + (JUNIOR_WORDS if wants_junior_or_no_experience(criteria) else [])
    level_matches = contains_any_match(text, level_terms)
    if criteria.levels:
        if level_matches:
            score += 18
            matched.append(f"level:{level_matches[0]}")
        else:
            score -= 8
            concerns.append("уровень стажер/junior не подтвержден")

    hard_mismatches = experience_reality_check(text, criteria)
    if hard_mismatches:
        score -= 40 + 10 * min(len(hard_mismatches), 3)
        concerns.extend(f"hard-mismatch:{item}" for item in hard_mismatches[:4])

    # Exclude stop words that the user intentionally included in their role query
    # (e.g. "Главный бухгалтер" should not be penalized for containing "главный")
    role_lower_tokens = set(normalize_match_text(" ".join(criteria.role_keywords)).split())
    effective_stops = [
        w for w in (criteria.stop_words + DEFAULT_STOP_WORDS)
        if not any(t in role_lower_tokens for t in normalize_match_text(w).split())
    ]
    stop_matches = contains_any_match(text, effective_stops)
    if stop_matches:
        score -= min(len(stop_matches), 4) * 12
        concerns.extend(f"stop-word:{item}" for item in stop_matches[:4])

    if criteria.remote is True:
        remote_matches = contains_any_match(text, REMOTE_WORDS)
        if remote_matches:
            score += 14
            matched.append("format:remote")
        else:
            concerns.append("удаленный формат не подтвержден")
    elif criteria.cities:
        city_matches = contains_any_match(text, criteria.cities)
        vacancy_is_remote = bool(contains_any_match(text, REMOTE_WORDS))
        if city_matches:
            score += 10
            matched.append(f"city:{city_matches[0]}")
        elif vacancy_is_remote:
            # Remote vacancy — city mismatch is acceptable
            score += 4
            matched.append("format:remote-ok")
        else:
            score -= 35
            concerns.append("город не совпал")

    if criteria.min_salary:
        if vacancy.salary_min and vacancy.salary_min >= criteria.min_salary:
            score += 8
            matched.append("salary")
        elif vacancy.salary_max and vacancy.salary_max >= criteria.min_salary:
            score += 4
            matched.append("salary-range")
        else:
            concerns.append("зарплата ниже желаемой или не указана")

    if vacancy.published_at:
        days_old = (date.today() - vacancy.published_at).days
        if days_old <= 7:
            score += 8
            matched.append("fresh:7d")
        elif days_old <= criteria.max_age_days:
            score += 3
            matched.append("fresh")
        else:
            score -= 8
            concerns.append(f"вакансия старше {criteria.max_age_days} дней")
    else:
        concerns.append("дата публикации не найдена")

    if vacancy.requirements or vacancy.responsibilities:
        score += 4

    has_specific_role_match = contains_any_match(text, specific_role_words)
    if specific_role_words and not has_specific_role_match and not role_matches:
        concerns.append("нет совпадения с целевой ролью")

    filtered_out = (
        score < 10
        or any(c.startswith("stop-word:") for c in concerns)
        or any(c.startswith("hard-mismatch:") for c in concerns)
        or (bool(specific_role_words) and not has_specific_role_match and not role_matches)
        or (bool(required_skill_words) and not required_skill_matches)
        or (bool(criteria.levels) and not level_matches and not role_matches)
        or (
            bool(criteria.cities)
            and criteria.remote is not True
            and not city_matches
            and not contains_any_match(text, REMOTE_WORDS)
        )
    )
    return ScoredVacancy(
        vacancy=vacancy,
        score=round(max(score, 0.0), 2),
        matched=matched,
        concerns=concerns,
        filtered_out=filtered_out,
    )


def contains_any_match(text: str, words: list[str]) -> list[str]:
    normalized_text = normalize_match_text(text)
    normalized_text_tokens = set(normalized_text.split())
    soft_text_tokens = {soft_match_token(token) for token in normalized_text_tokens}
    result: list[str] = []
    for word in words:
        normalized_word = normalize_match_text(word)
        if not normalized_word:
            continue
        word_tokens = normalized_word.split()
        soft_word_tokens = [soft_match_token(token) for token in word_tokens]
        if normalized_word in normalized_text or (
            len(word_tokens) > 1 and all(token in normalized_text_tokens for token in word_tokens)
        ) or (
            len(soft_word_tokens) > 1 and all(token in soft_text_tokens for token in soft_word_tokens)
        ) or (
            len(word_tokens) == 1 and soft_word_tokens[0] in soft_text_tokens
        ):
            result.append(word)
    return result


def title_query_match(raw_query_text: str, title_text: str) -> str:
    if not raw_query_text or not title_text:
        return ""
    title_tokens = title_text.split()
    if raw_query_text in title_text or (len(title_tokens) > 1 and title_text in raw_query_text):
        return "exact"

    query_tokens = [
        token
        for token in raw_query_text.split()
        if token not in MATCH_FILLER_WORDS and len(token) > 2
    ]
    soft_title_tokens = {soft_match_token(token) for token in title_text.split()}
    soft_query_tokens = [soft_match_token(token) for token in query_tokens[:5]]
    if len(query_tokens) >= 2 and all(token in soft_title_tokens for token in soft_query_tokens):
        return "tokens"
    return ""


def normalize_match_text(value: str) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"[^0-9a-zа-яё+#]+", " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def soft_match_token(token: str) -> str:
    token = token.casefold().strip()
    if not re.search(r"[а-яё]", token) or len(token) <= 5:
        return token
    for ending in (
        "ыми",
        "ими",
        "ого",
        "его",
        "ому",
        "ему",
        "иях",
        "ах",
        "ях",
        "ой",
        "ый",
        "ий",
        "ая",
        "ое",
        "ую",
        "юю",
        "ом",
        "ем",
        "ым",
        "им",
        "ам",
        "ям",
        "а",
        "я",
        "ы",
        "и",
        "у",
        "ю",
        "е",
        "о",
    ):
        if token.endswith(ending) and len(token) - len(ending) >= 4:
            return token[: -len(ending)]
    return token


def vacancy_fingerprint(vacancy: Vacancy) -> str:
    parts = [
        normalize_match_text(vacancy.title),
        normalize_match_text(vacancy.company),
    ]
    return "|".join(parts).strip("|")


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.casefold().strip()
        if key and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _specific_role_words(role_keywords: list[str]) -> list[str]:
    generic = {
        "junior",
        "джуниор",
        "стажер",
        "стажёр",
        "intern",
        "internship",
        "аналитик",
        "разработчик",
        "backend",
        "qa",
        "тестировщик",
    }
    return [role for role in _unique(role_keywords) if role.casefold().strip() not in generic]


def experience_reality_check(text: str, criteria: Criteria) -> list[str]:
    if not wants_junior_or_no_experience(criteria):
        return []

    mismatches: list[str] = []

    seniority = contains_any(text, SENIORITY_MARKERS)
    mismatches.extend(f"seniority:{item}" for item in seniority[:3])

    for years, fragment in extract_experience_requirements(text):
        if years >= max_allowed_years(criteria):
            mismatches.append(f"experience:{fragment}")

    return _unique(mismatches)


def wants_junior_or_no_experience(criteria: Criteria) -> bool:
    text = " ".join(criteria.levels + [criteria.raw_query]).casefold()
    return any(
        marker in text
        for marker in [
            "junior",
            "джуниор",
            "стажер",
            "стажёр",
            "intern",
            "без опыта",
            "до года",
            "начинающий",
        ]
    )


def max_allowed_years(criteria: Criteria) -> int:
    text = " ".join(criteria.levels + [criteria.raw_query]).casefold()
    if "без опыта" in text:
        return 1
    return 2


def extract_experience_requirements(text: str) -> list[tuple[int, str]]:
    patterns = [
        r"(?:опыт(?: работы)?[^.;:,]{0,40})?(?:от|не менее|минимум)\s*(\d{1,2})\s*(?:\+)?\s*(?:лет|года|год)",
        r"(\d{1,2})\s*\+\s*(?:лет|года|год)",
        r"(\d{1,2})\s*(?:лет|года|год)\s+(?:релевантного|коммерческого|практического)?\s*опыта",
        r"опыт работы:\s*(\d{1,2})\s*(?:лет|года|год)",
    ]
    found: list[tuple[int, str]] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            years = int(match.group(1))
            fragment = re.sub(r"\s+", " ", match.group(0)).strip()
            found.append((years, fragment))
    return found
