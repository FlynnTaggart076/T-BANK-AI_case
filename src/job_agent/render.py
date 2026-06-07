from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from .models import Criteria, ScoredVacancy, VacancyDeepAnalysis
from .utils import ROOT, clamp


def render_report(
    criteria: Criteria,
    top: list[ScoredVacancy],
    all_scored: list[ScoredVacancy],
    explanations: dict[str, VacancyDeepAnalysis],
    trace: list[str],
    output_path: Path | None = None,
) -> Path:
    path = output_path or ROOT / "output" / "report.md"
    path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append("# Vacancy Agent Report")
    lines.append("")
    lines.append("## Input")
    lines.append("")
    lines.append(f"**User query:** {criteria.raw_query}")
    lines.append("")
    lines.append("## Extracted Criteria")
    lines.append("")
    for key, value in asdict(criteria).items():
        if key == "raw_query":
            continue
        lines.append(f"- **{key}:** {value}")
    lines.append("")
    lines.append(f"## Top Results ({len(top)})")
    lines.append("")

    if not top:
        lines.append("No relevant vacancies found.")
    elif len(top) < 5:
        lines.append(
            f"Only {len(top)} vacancies passed the strict filters. "
            "Filtered candidates are listed below for audit."
        )
        lines.append("")
    for index, item in enumerate(top, start=1):
        vacancy = item.vacancy
        explanation = explanations.get(vacancy.external_id)
        lines.append(f"### {index}. {vacancy.title}")
        lines.append("")
        lines.append(f"- **Company:** {vacancy.company or 'unknown'}")
        lines.append(f"- **Location:** {vacancy.city or vacancy.region or 'unknown'}")
        lines.append(f"- **Format:** {vacancy.employment or 'unknown'} / {vacancy.schedule or 'unknown'}")
        lines.append(f"- **Salary:** {format_salary(vacancy.salary_min, vacancy.salary_max)}")
        lines.append(f"- **Published:** {vacancy.published_at or 'unknown'}")
        lines.append(f"- **Score:** {item.score}")
        lines.append(f"- **URL:** {vacancy.url}")
        lines.append(f"- **Matched:** {', '.join(item.matched) if item.matched else 'none'}")
        lines.append(f"- **Concerns:** {', '.join(item.concerns) if item.concerns else 'none'}")
        if explanation:
            lines.append(f"- **LLM match:** {explanation.overall_match}/100")
            lines.append(f"- **Recommendation:** {explanation.final_recommendation}")
            met = [r.requirement for r in explanation.requirement_check if r.met]
            unmet = [r.requirement for r in explanation.requirement_check if not r.met]
            if met:
                lines.append(f"- **Requirements met:** {', '.join(met[:5])}")
            if unmet:
                lines.append(f"- **Requirements not met:** {', '.join(unmet[:5])}")
            if explanation.red_flags:
                lines.append(f"- **Red flags:** {', '.join(explanation.red_flags)}")
            if explanation.inconsistencies:
                lines.append(f"- **Inconsistencies:** {', '.join(explanation.inconsistencies)}")
            lines.append(f"- **Advice:** {explanation.specific_advice}")
        snippet = clamp(vacancy.requirements or vacancy.description or vacancy.responsibilities, 450)
        if snippet:
            lines.append("")
            lines.append(f"> {snippet}")
        lines.append("")

    lines.append("## Considered Vacancies")
    lines.append("")
    lines.append("| Score | Status | Title | Company | Location | Source | Reasons |")
    lines.append("|---:|---|---|---|---|---|---|")
    for item in all_scored[:25]:
        vacancy = item.vacancy
        status = "filtered" if item.filtered_out else "kept"
        title = escape_cell(vacancy.title)
        company = escape_cell(vacancy.company)
        location = escape_cell(vacancy.city or vacancy.region)
        reasons = escape_cell(", ".join(item.concerns[:5]) if item.concerns else "")
        lines.append(
            f"| {item.score} | {status} | [{title}]({vacancy.url}) | "
            f"{company} | {location} | {vacancy.source} | {reasons} |"
        )

    lines.append("")
    lines.append("## Trace")
    lines.append("")
    for step in trace:
        lines.append(f"- {step}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_run_log(trace: list[str], path: Path | None = None) -> Path:
    log_path = path or ROOT / "output" / "run.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(trace) + "\n", encoding="utf-8")
    return log_path


def format_salary(salary_min: int | None, salary_max: int | None) -> str:
    if salary_min and salary_max:
        return f"{salary_min:,}-{salary_max:,} RUB".replace(",", " ")
    if salary_min:
        return f"from {salary_min:,} RUB".replace(",", " ")
    if salary_max:
        return f"up to {salary_max:,} RUB".replace(",", " ")
    return "not specified"


def escape_cell(value: str) -> str:
    return value.replace("|", "\\|") if value else ""
