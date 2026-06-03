from __future__ import annotations

import argparse

from .service import run_agent


DEFAULT_QUERY = (
    "Ищу стажировку или junior позицию Python backend, можно удаленно или Москва, "
    "FastAPI/PostgreSQL, без опыта или до года."
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run vacancy matching agent")
    parser.add_argument("query", nargs="*", help="Free-form candidate query")
    args = parser.parse_args()

    query = " ".join(args.query).strip() or DEFAULT_QUERY
    result = run_agent(query)

    print(f"Report: {result.report_path}")
    print(f"Log: {result.log_path}")
    print("")
    print("Top vacancies:")
    for index, item in enumerate(result.top, start=1):
        vacancy = item.vacancy
        print(f"{index}. [{item.score}] {vacancy.title} — {vacancy.company}")
        print(f"   {vacancy.url}")


if __name__ == "__main__":
    main()
