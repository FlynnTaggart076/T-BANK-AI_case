from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(slots=True)
class Criteria:
    raw_query: str
    role_keywords: list[str] = field(default_factory=list)
    skill_keywords: list[str] = field(default_factory=list)
    cities: list[str] = field(default_factory=list)
    remote: bool | None = None
    levels: list[str] = field(default_factory=list)
    min_salary: int | None = None
    max_age_days: int = 45
    stop_words: list[str] = field(default_factory=list)
    must_have: list[str] = field(default_factory=list)
    nice_to_have: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Vacancy:
    source: str
    external_id: str
    title: str
    company: str
    url: str
    city: str = ""
    region: str = ""
    salary_min: int | None = None
    salary_max: int | None = None
    published_at: date | None = None
    employment: str = ""
    schedule: str = ""
    experience: str = ""
    description: str = ""
    requirements: str = ""
    responsibilities: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return " ".join(
            part
            for part in [
                self.title,
                self.company,
                self.city,
                self.region,
                self.employment,
                self.schedule,
                self.experience,
                self.description,
                self.requirements,
                self.responsibilities,
            ]
            if part
        )


@dataclass(slots=True)
class ScoredVacancy:
    vacancy: Vacancy
    score: float
    matched: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    filtered_out: bool = False


@dataclass(slots=True)
class VacancyExplanation:
    external_id: str
    suitability: str
    matched_requirements: list[str]
    concerns: list[str]
    next_step: str
    priority: str


@dataclass(slots=True)
class AgentResult:
    criteria: Criteria
    vacancies: list[ScoredVacancy]
    top: list[ScoredVacancy]
    explanations: dict[str, VacancyExplanation]
    report_path: str
    log_path: str
    trace: list[str]
