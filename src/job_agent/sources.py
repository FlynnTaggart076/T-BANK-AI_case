from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import Criteria, Vacancy
from .utils import ROOT, clean_text, load_env_file, parse_date


MOSCOW_REGION_CODE = "7700000000000"
SPB_REGION_CODE = "7800000000000"

SOURCE_TRUDVSEM = "trudvsem"
SOURCE_SUPERJOB = "superjob"
SOURCE_ALL = "all"
SOURCE_LOCAL = "local"

SOURCE_LABELS = {
    SOURCE_TRUDVSEM: "Работа России",
    SOURCE_SUPERJOB: "SuperJob",
    SOURCE_ALL: "Работа России + SuperJob",
    SOURCE_LOCAL: "Локальный файл",
}

REGION_CODES = {
    "москва": MOSCOW_REGION_CODE,
    "moscow": MOSCOW_REGION_CODE,
    "санкт-петербург": SPB_REGION_CODE,
    "санкт петербург": SPB_REGION_CODE,
    "питер": SPB_REGION_CODE,
    "спб": SPB_REGION_CODE,
    "saint petersburg": SPB_REGION_CODE,
}


@dataclass(slots=True)
class SourceResult:
    vacancies: list[Vacancy]
    trace: list[str]


def normalize_source(value: Any) -> str:
    text = str(value or "").strip().casefold()
    aliases = {
        "": SOURCE_TRUDVSEM,
        "trudvsem": SOURCE_TRUDVSEM,
        "работа россии": SOURCE_TRUDVSEM,
        "rabota_rossii": SOURCE_TRUDVSEM,
        "superjob": SOURCE_SUPERJOB,
        "super_job": SOURCE_SUPERJOB,
        "sj": SOURCE_SUPERJOB,
        "all": SOURCE_ALL,
        "both": SOURCE_ALL,
        "оба": SOURCE_ALL,
        "local": SOURCE_LOCAL,
    }
    return aliases.get(text, SOURCE_TRUDVSEM)


def source_label(value: str) -> str:
    return SOURCE_LABELS.get(normalize_source(value), SOURCE_LABELS[SOURCE_TRUDVSEM])


class TrudvsemSource:
    name = SOURCE_TRUDVSEM
    base_url = "https://opendata.trudvsem.ru/api/v1/vacancies"

    def __init__(self, limit_per_query: int = 50, timeout_seconds: int = 15) -> None:
        self.limit_per_query = limit_per_query
        self.timeout_seconds = timeout_seconds

    def fetch(self, criteria: Criteria) -> SourceResult:
        queries = build_search_queries(criteria)
        region_codes = [REGION_CODES[c.casefold()] for c in criteria.cities if c.casefold() in REGION_CODES]
        trace: list[str] = []
        vacancies: list[Vacancy] = []

        if not queries:
            queries = [criteria.raw_query]

        for query in queries:
            vacancies.extend(self._request(query=query, region_code=None, criteria=criteria, trace=trace))
            for region_code in region_codes:
                vacancies.extend(
                    self._request(query=query, region_code=region_code, criteria=criteria, trace=trace)
                )

        return SourceResult(vacancies=vacancies, trace=trace)

    def _request(
        self,
        query: str,
        region_code: str | None,
        criteria: Criteria,
        trace: list[str],
    ) -> list[Vacancy]:
        path = self.base_url if region_code is None else f"{self.base_url}/region/{region_code}"
        params: dict[str, str | int] = {
            "text": query,
            "limit": self.limit_per_query,
            "offset": 0,
        }
        if criteria.max_age_days:
            modified_from = datetime.now(timezone.utc) - timedelta(days=criteria.max_age_days)
            params["modifiedFrom"] = modified_from.strftime("%Y-%m-%dT%H:%M:%SZ")

        url = f"{path}?{urllib.parse.urlencode(params)}"
        trace.append(f"Trudvsem request: query='{query}', region='{region_code or 'all'}'")

        req = urllib.request.Request(
            url,
            headers={"User-Agent": "t-bank-vacancy-agent/0.1"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            trace.append(f"Trudvsem error for query='{query}': {exc}")
            return []

        items = payload.get("results", {}).get("vacancies", [])
        total = payload.get("meta", {}).get("total", 0)
        trace.append(f"Trudvsem returned {len(items)} items, total={total}")
        return [normalize_trudvsem_item(item.get("vacancy", {})) for item in items]


class SuperJobSource:
    name = SOURCE_SUPERJOB
    base_url = "https://api.superjob.ru/2.0/vacancies/"

    def __init__(
        self,
        api_key: str | None = None,
        token_path: Path | None = None,
        limit_per_query: int = 50,
        timeout_seconds: int = 15,
    ) -> None:
        load_env_file(token_path)
        self.api_key = api_key or load_superjob_key(token_path)
        self.limit_per_query = min(max(limit_per_query, 1), 100)
        self.timeout_seconds = timeout_seconds

    def fetch(self, criteria: Criteria) -> SourceResult:
        if not self.api_key:
            return SourceResult(
                vacancies=[],
                trace=["SuperJob skipped: API key not found"],
            )

        queries = build_search_queries(criteria) or [criteria.raw_query]
        cities = criteria.cities or [""]
        trace: list[str] = []
        vacancies: list[Vacancy] = []

        for query in queries:
            for city in cities:
                vacancies.extend(self._request(query=query, city=city, criteria=criteria, trace=trace))

        return SourceResult(vacancies=vacancies, trace=trace)

    def _request(
        self,
        query: str,
        city: str,
        criteria: Criteria,
        trace: list[str],
    ) -> list[Vacancy]:
        params: dict[str, str | int] = {
            "keyword": query,
            "count": self.limit_per_query,
            "page": 0,
            "order_field": "date",
            "order_direction": "desc",
        }
        if city:
            params["town"] = city
        if criteria.min_salary:
            params["payment_from"] = criteria.min_salary
        if criteria.remote is True:
            params["place_of_work"] = 2
            params["moveable"] = 1
        if wants_no_experience(criteria):
            params["experience"] = 1
        if criteria.max_age_days:
            modified_from = datetime.now(timezone.utc) - timedelta(days=criteria.max_age_days)
            params["date_published_from"] = int(modified_from.timestamp())

        url = f"{self.base_url}?{urllib.parse.urlencode(params)}"
        trace.append(f"SuperJob request: query='{query}', town='{city or 'all'}'")

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "t-bank-vacancy-agent/0.1",
                "X-Api-App-Id": self.api_key,
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            details = clean_text(exc.read().decode("utf-8", errors="replace"))
            trace.append(f"SuperJob HTTP error for query='{query}': {exc.code} {details}")
            return []
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            trace.append(f"SuperJob error for query='{query}': {exc}")
            return []

        if isinstance(payload.get("error"), dict):
            trace.append(f"SuperJob API error for query='{query}': {payload['error']}")
            return []

        items = payload.get("objects", [])
        total = payload.get("total", 0)
        trace.append(f"SuperJob returned {len(items)} items, total={total}")
        return [normalize_superjob_item(item) for item in items if isinstance(item, dict)]


class LocalFileSource:
    name = SOURCE_LOCAL

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or ROOT / "data" / "sample_vacancies.json"

    def fetch(self, criteria: Criteria) -> SourceResult:
        if not self.path.exists():
            return SourceResult(vacancies=[], trace=[f"Local fallback skipped: {self.path} not found"])
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return SourceResult(vacancies=[], trace=[f"Local fallback JSON error: {exc}"])

        raw_items = payload.get("vacancies", payload if isinstance(payload, list) else [])
        vacancies = [normalize_local_item(item) for item in raw_items]
        return SourceResult(
            vacancies=vacancies,
            trace=[f"Local fallback loaded {len(vacancies)} items from {self.path}"],
        )


SEARCH_PREPOSITIONS = {"в", "во", "на", "по", "для", "с", "со", "к", "у"}
SEARCH_FILLER_WORDS = {
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
    "без",
    "опыта",
    "работы",
    "junior",
    "джуниор",
    "стажер",
    "стажёр",
    "стажировка",
    "intern",
    "internship",
    "можно",
    "удаленно",
    "удалённо",
    "remote",
    "зарплата",
    "оклад",
    "от",
    "до",
}


def build_search_queries(criteria: Criteria) -> list[str]:
    roles = expand_role_queries(criteria.role_keywords[:3] or [criteria.raw_query])
    skills = unique_values(criteria.skill_keywords + criteria.must_have + criteria.nice_to_have)[:5]
    raw_variants = raw_query_variants(criteria.raw_query)
    raw_modifiers = raw_search_modifiers(criteria.raw_query, roles + skills)
    generic_roles = {"стажировка", "стажер", "стажёр", "junior", "intern", "internship"}
    has_specific_role = any(role.casefold().strip() not in generic_roles for role in roles)
    queries: list[str] = []

    queries.extend(raw_variants[:3])

    for role in roles:
        normalized_role = role.casefold().strip()
        if normalized_role in generic_roles and has_specific_role:
            continue
        if skills:
            queries.append(" ".join([role, skills[0]]))
            if len(skills) > 1 and normalized_role not in generic_roles:
                queries.append(" ".join([role, skills[1]]))
        if raw_modifiers and normalized_role not in generic_roles:
            queries.append(" ".join([role] + raw_modifiers[:2]))
            for modifier in raw_modifiers[:2]:
                queries.append(" ".join([role, modifier]))
        if normalized_role not in generic_roles or not skills:
            queries.append(role)

    if skills:
        queries.append(" ".join(skills[:2]))
        queries.append(skills[0])

    result: list[str] = []
    seen: set[str] = set()
    for query in queries:
        query = normalize_search_query(query)
        key = query.casefold()
        if query and key not in seen:
            seen.add(key)
            result.append(query)
    return result[:10]


def expand_role_queries(role_keywords: list[str]) -> list[str]:
    roles: list[str] = []
    for role in role_keywords:
        normalized = normalize_search_query(role)
        if not normalized:
            continue
        roles.append(normalized)

        head = re.split(r"\b(?:в|во|на|по|для|с|со|к|у)\b", normalized, maxsplit=1)[0].strip()
        if head:
            roles.append(head)

        tokens = normalized.split()
        meaningful = [token for token in tokens if token not in SEARCH_PREPOSITIONS]
        if len(meaningful) >= 2:
            roles.append(" ".join(meaningful[:2]))
        if meaningful:
            roles.append(meaningful[0])

    return unique_values(roles)


def raw_query_variants(raw_query: str) -> list[str]:
    normalized = normalize_search_query(raw_query)
    if not normalized:
        return []

    tokens = normalized.split()
    without_fillers = [token for token in tokens if token not in SEARCH_FILLER_WORDS]
    without_prepositions = [
        token for token in without_fillers if token not in SEARCH_PREPOSITIONS
    ]
    return unique_values(
        [
            normalized,
            " ".join(without_fillers),
            " ".join(without_prepositions),
        ]
    )


def raw_search_modifiers(raw_query: str, known_terms: list[str]) -> list[str]:
    known_tokens = set()
    for term in known_terms:
        known_tokens.update(normalize_search_query(term).split())

    modifiers: list[str] = []
    for token in normalize_search_query(raw_query).split():
        if (
            token
            and token not in known_tokens
            and token not in SEARCH_FILLER_WORDS
            and token not in SEARCH_PREPOSITIONS
        ):
            modifiers.append(token)
    return unique_values(modifiers)[:4]


def normalize_search_query(value: str) -> str:
    text = clean_text(value).casefold()
    text = re.sub(r"[^0-9a-zа-яё+#]+", " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def unique_values(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.casefold().strip()
        if key and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def normalize_trudvsem_item(item: dict) -> Vacancy:
    company = item.get("company") or {}
    region = item.get("region") or {}
    requirement = item.get("requirement") or {}
    return Vacancy(
        source="trudvsem",
        external_id=clean_text(item.get("id")),
        title=clean_text(item.get("job-name") or item.get("name")),
        company=clean_text(company.get("name")),
        url=clean_text(item.get("vac_url")),
        city=extract_location(item.get("addresses")),
        region=clean_text(region.get("name")),
        salary_min=_to_int(item.get("salary_min")),
        salary_max=_to_int(item.get("salary_max")),
        published_at=parse_date(item.get("creation-date") or item.get("date_modify")),
        employment=clean_text(item.get("employment")),
        schedule=clean_text(item.get("schedule")),
        experience=build_experience_text(item, requirement),
        description=clean_text(item.get("description") or item.get("qualification")),
        requirements=clean_text(item.get("requirements")),
        responsibilities=clean_text(item.get("duty")),
        raw=item,
    )


def normalize_superjob_item(item: dict) -> Vacancy:
    town = item.get("town") or {}
    type_of_work = item.get("type_of_work") or {}
    place_of_work = item.get("place_of_work") or {}
    experience = item.get("experience") or {}
    education = item.get("education") or {}

    return Vacancy(
        source=SOURCE_SUPERJOB,
        external_id=clean_text(item.get("id")),
        title=clean_text(item.get("profession")),
        company=clean_text(item.get("firm_name")),
        url=clean_text(item.get("link") or item.get("external_url") or superjob_card_url(item.get("id"))),
        city=clean_text(town.get("title")),
        region=clean_text(town.get("declension") or town.get("genitive")),
        salary_min=_to_int(item.get("payment_from")),
        salary_max=_to_int(item.get("payment_to")),
        published_at=parse_unix_date(item.get("date_published")),
        employment=clean_text(type_of_work.get("title")),
        schedule=clean_text(place_of_work.get("title")),
        experience=build_superjob_experience_text(experience, education),
        description=clean_text(item.get("compensation") or item.get("firm_activity")),
        requirements=clean_text(item.get("candidat")),
        responsibilities=clean_text(item.get("work")),
        raw=item,
    )


def normalize_local_item(item: dict) -> Vacancy:
    return Vacancy(
        source=clean_text(item.get("source") or "local"),
        external_id=clean_text(item.get("external_id") or item.get("id")),
        title=clean_text(item.get("title")),
        company=clean_text(item.get("company")),
        url=clean_text(item.get("url")),
        city=clean_text(item.get("city")),
        region=clean_text(item.get("region")),
        salary_min=_to_int(item.get("salary_min")),
        salary_max=_to_int(item.get("salary_max")),
        published_at=parse_date(item.get("published_at")),
        employment=clean_text(item.get("employment")),
        schedule=clean_text(item.get("schedule")),
        experience=clean_text(item.get("experience")),
        description=clean_text(item.get("description")),
        requirements=clean_text(item.get("requirements")),
        responsibilities=clean_text(item.get("responsibilities")),
        raw=item,
    )


def _to_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace(" ", "")))
    except ValueError:
        return None


def parse_unix_date(value: object) -> date | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).date()
    except (TypeError, ValueError, OSError):
        return None


def extract_location(addresses: object) -> str:
    if isinstance(addresses, list) and addresses:
        first = addresses[0]
        if isinstance(first, dict):
            return clean_text(first.get("location") or first.get("address"))
    if isinstance(addresses, dict):
        direct = addresses.get("location")
        if direct:
            return clean_text(direct)
        nested = addresses.get("address")
        if isinstance(nested, list) and nested:
            first = nested[0]
            if isinstance(first, dict):
                return clean_text(first.get("location") or first.get("address"))
        return clean_text(nested)
    return ""


def build_experience_text(item: dict, requirement: dict) -> str:
    parts: list[str] = []
    if item.get("experience"):
        parts.append(f"Опыт: {item.get('experience')}")
    if item.get("qualification"):
        parts.append(f"Квалификация: {item.get('qualification')}")
    if requirement.get("experience") not in (None, ""):
        parts.append(f"Опыт работы: {requirement.get('experience')} лет")
    if requirement.get("education"):
        parts.append(f"Образование: {requirement.get('education')}")
    return clean_text(" ".join(parts))


def build_superjob_experience_text(experience: dict, education: dict) -> str:
    parts: list[str] = []
    if experience.get("title"):
        parts.append(f"Опыт работы: {experience.get('title')}")
    if education.get("title"):
        parts.append(f"Образование: {education.get('title')}")
    return clean_text(" ".join(parts))


def superjob_card_url(value: object) -> str:
    vacancy_id = clean_text(value)
    if not vacancy_id:
        return ""
    return f"https://api.superjob.ru/2.0/vacancies/{vacancy_id}/"


def wants_no_experience(criteria: Criteria) -> bool:
    text = " ".join(criteria.levels + [criteria.raw_query]).casefold()
    return any(marker in text for marker in ["без опыта", "без опыта работы", "no experience"])


def load_superjob_key(path: Path | None = None) -> str | None:
    load_env_file(path)
    for env_name in ("SUPERJOB_API_KEY", "SUPERJOB_SECRET", "SUPERJOB_TOKEN"):
        env_value = os.getenv(env_name)
        if env_value:
            return env_value.strip()
    return None
