from __future__ import annotations

from .models import AgentResult
from .pipeline import VacancyAgentPipeline


def run_agent(user_query: str, top_n: int = 5, source: str = "trudvsem") -> AgentResult:
    """Public entry point for CLI, tests, and future Telegram bot handlers."""
    return VacancyAgentPipeline().run(user_query=user_query, top_n=top_n, source=source)
