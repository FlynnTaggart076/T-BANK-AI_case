from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .payload import agent_result_to_payload, clamp_top_n
from .pipeline import VacancyAgentPipeline
from .sources import normalize_source
from .utils import ROOT


FRONTEND_DIR = ROOT / "frontend"


class JuniorScoutHandler(SimpleHTTPRequestHandler):
    pipeline = VacancyAgentPipeline()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(FRONTEND_DIR), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def do_POST(self) -> None:
        if self.path != "/api/search":
            self.send_json({"error": "not_found", "message": "Unknown endpoint"}, HTTPStatus.NOT_FOUND)
            return

        try:
            payload = self.read_json()
            query = str(payload.get("query", "")).strip()
            top_n = clamp_top_n(payload.get("top_n"))
            source = normalize_source(payload.get("source"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_json(
                {"error": "bad_request", "message": "Request body must be valid JSON"},
                HTTPStatus.BAD_REQUEST,
            )
            return

        if not query:
            self.send_json(
                {"error": "empty_query", "message": "Введите запрос для поиска вакансий."},
                HTTPStatus.BAD_REQUEST,
            )
            return

        try:
            result = self.pipeline.run(query, top_n=top_n, source=source)
        except Exception as exc:  # noqa: BLE001 - API boundary should return JSON errors.
            self.send_json(
                {
                    "error": "agent_failed",
                    "message": f"Agent pipeline failed: {exc}",
                    "trace": ["Запрос принят", "Ошибка при выполнении pipeline"],
                },
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return

        self.send_json(agent_result_to_payload(result, top_n=top_n, source=source))

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8")
        payload = json.loads(raw or "{}")
        if not isinstance(payload, dict):
            return {}
        return payload

    def send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_server(host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), JuniorScoutHandler)
    print(f"Junior Scout AI: http://{host}:{port}")
    print("API endpoint: POST /api/search")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server")
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve Junior Scout AI frontend and API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    args = parser.parse_args()
    run_server(args.host, args.port)


if __name__ == "__main__":
    main()
