from __future__ import annotations

from datetime import date
import re

from .models import Criteria, ScoredVacancy, Vacancy
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


def validate_and_dedupe(vacancies: list[Vacancy]) -> tuple[list[Vacancy], list[str]]:
    trace: list[str] = []
    valid: list[Vacancy] = []
    seen: set[str] = set()
    broken_count = 0
    duplicate_count = 0

    for vacancy in vacancies:
        if not vacancy.title or not vacancy.url:
            broken_count += 1
            if broken_count <= 5:
                trace.append(f"Drop broken vacancy: id={vacancy.external_id or '<empty>'}")
            continue
        key = vacancy.url.casefold() or f"{vacancy.source}:{vacancy.external_id}"
        if key in seen:
            duplicate_count += 1
            if duplicate_count <= 5:
                trace.append(f"Drop duplicate vacancy: {vacancy.title} ({vacancy.url})")
            continue
        seen.add(key)
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


def _score_one(criteria: Criteria, vacancy: Vacancy) -> ScoredVacancy:
    text = vacancy.text.casefold()
    matched: list[str] = []
    concerns: list[str] = []
    score = 0.0
    required_skill_words = _unique(criteria.skill_keywords + criteria.must_have)
    nice_skill_words = _unique(criteria.nice_to_have)
    specific_role_words = _specific_role_words(criteria.role_keywords)
    city_matches: list[str] = []

    role_matches = contains_any(text, criteria.role_keywords)
    if role_matches:
        score += 30 + min(len(role_matches), 3) * 6
        matched.extend(f"role:{item}" for item in role_matches[:3])
    else:
        score -= 30
        concerns.append("роль не совпала явно")

    required_skill_matches = contains_any(text, required_skill_words)
    nice_skill_matches = contains_any(text, nice_skill_words)
    if required_skill_words and required_skill_matches:
        score += min(len(required_skill_matches), 6) * 10
        matched.extend(f"skill:{item}" for item in required_skill_matches[:6])
    elif required_skill_words:
        score -= 25
        concerns.append("обязательный стек не найден в описании")

    if nice_skill_matches:
        score += min(len(nice_skill_matches), 5) * 4
        matched.extend(f"nice:{item}" for item in nice_skill_matches[:5])

    level_matches = contains_any(text, criteria.levels + JUNIOR_WORDS)
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

    stop_matches = contains_any(text, criteria.stop_words + DEFAULT_STOP_WORDS)
    if stop_matches:
        score -= min(len(stop_matches), 4) * 12
        concerns.extend(f"stop-word:{item}" for item in stop_matches[:4])

    if criteria.remote is True:
        remote_matches = contains_any(text, REMOTE_WORDS)
        if remote_matches:
            score += 14
            matched.append("format:remote")
        else:
            concerns.append("удаленный формат не подтвержден")
    elif criteria.remote is False and criteria.cities:
        city_matches = contains_any(text, criteria.cities)
        if city_matches:
            score += 10
            matched.append(f"city:{city_matches[0]}")
        else:
            concerns.append("город не совпал явно")
    elif criteria.cities and contains_any(text, criteria.cities):
        score += 8
        matched.append("city")

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

    if specific_role_words and not contains_any(text, specific_role_words):
        concerns.append("нет совпадения с целевой ролью")

    filtered_out = (
        score < 10
        or any(c.startswith("stop-word:") for c in concerns)
        or any(c.startswith("hard-mismatch:") for c in concerns)
        or (bool(specific_role_words) and not contains_any(text, specific_role_words))
        or (bool(required_skill_words) and not required_skill_matches)
        or (bool(criteria.levels) and not level_matches and not role_matches)
        or (criteria.remote is False and bool(criteria.cities) and not city_matches)
    )
    return ScoredVacancy(
        vacancy=vacancy,
        score=round(max(score, 0.0), 2),
        matched=matched,
        concerns=concerns,
        filtered_out=filtered_out,
    )


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
