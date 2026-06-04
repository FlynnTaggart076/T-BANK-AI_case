from __future__ import annotations

from .llm import LLMClient, explain_top, extract_criteria
from .models import AgentResult
from .render import render_report, write_run_log
from .scoring import score_vacancies, validate_and_dedupe
from .sources import (
    SOURCE_ALL,
    SOURCE_LOCAL,
    SOURCE_SUPERJOB,
    SOURCE_TRUDVSEM,
    LocalFileSource,
    SuperJobSource,
    TrudvsemSource,
    normalize_source,
)


class VacancyAgentPipeline:
    def __init__(
        self,
        trudvsem_source: TrudvsemSource | None = None,
        superjob_source: SuperJobSource | None = None,
        local_source: LocalFileSource | None = None,
        llm: LLMClient | None = None,
        use_local_fallback: bool = False,
    ) -> None:
        self.trudvsem_source = trudvsem_source or TrudvsemSource()
        self.superjob_source = superjob_source or SuperJobSource()
        self.local_source = local_source or LocalFileSource()
        self.llm = llm or LLMClient()
        self.use_local_fallback = use_local_fallback

    def run(self, user_query: str, top_n: int = 5, source: str = SOURCE_TRUDVSEM) -> AgentResult:
        source = normalize_source(source)
        trace: list[str] = [f"Pipeline started: source={source}"]

        criteria, criteria_trace = extract_criteria(user_query, self.llm)
        trace.extend(criteria_trace)
        trace.append(f"Criteria extracted: roles={criteria.role_keywords}, skills={criteria.skill_keywords}")

        vacancies = []
        for source_result in self.fetch_from_sources(criteria, source):
            trace.extend(source_result.trace)
            vacancies.extend(source_result.vacancies)

        valid_vacancies, validation_trace = validate_and_dedupe(vacancies)
        trace.extend(validation_trace)

        scored = score_vacancies(criteria, valid_vacancies)
        top = [item for item in scored if not item.filtered_out][:top_n]

        if self.should_use_local_fallback(source, len(top), top_n):
            fallback_result = self.local_source.fetch(criteria)
            trace.append(
                f"Only {len(top)} relevant vacancies after source scoring; local fallback enabled"
            )
            trace.extend(fallback_result.trace)
            combined = valid_vacancies + fallback_result.vacancies
            valid_vacancies, validation_trace = validate_and_dedupe(combined)
            trace.extend(validation_trace)
            scored = score_vacancies(criteria, valid_vacancies)
            top = [item for item in scored if not item.filtered_out][:top_n]

        trace.append(f"Scored {len(scored)} vacancies; top={len(top)}")

        explanations, explanation_trace = explain_top(criteria, top, self.llm)
        trace.extend(explanation_trace)

        report_path = render_report(criteria, top, scored, explanations, trace)
        trace.append(f"Report written: {report_path}")
        log_path = write_run_log(trace)

        return AgentResult(
            criteria=criteria,
            vacancies=scored,
            top=top,
            explanations=explanations,
            report_path=str(report_path),
            log_path=str(log_path),
            trace=trace,
        )

    def fetch_from_sources(self, criteria, source: str):
        if source == SOURCE_TRUDVSEM:
            return [self.trudvsem_source.fetch(criteria)]
        if source == SOURCE_SUPERJOB:
            return [self.superjob_source.fetch(criteria)]
        if source == SOURCE_ALL:
            return [
                self.trudvsem_source.fetch(criteria),
                self.superjob_source.fetch(criteria),
            ]
        if source == SOURCE_LOCAL:
            return [self.local_source.fetch(criteria)]
        return [self.trudvsem_source.fetch(criteria)]

    def should_use_local_fallback(self, source: str, top_count: int, top_n: int) -> bool:
        if not self.use_local_fallback or top_count >= top_n:
            return False
        return source in {SOURCE_TRUDVSEM, SOURCE_ALL}
