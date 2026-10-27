"""Exercise the real Hermes tool loop against a local simulated model, without API charges."""

import copy
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from pydantic import SecretStr

from app.config import Settings
from app.services.agent_reviews import validate_result
from app.services.hermes_runtime import run_hermes

SNAPSHOT = {
    "sources": [
        {
            "id": "opportunity",
            "title": "Consultation fictive",
            "text": "Technical services required. This is a synthetic test.",
        }
    ],
    "warnings": [],
}
REPORT = {
    "summary": "Examen d’une consultation fictive.",
    "recommendation": "review",
    "rationale": ["Les capacités de l’entreprise doivent être vérifiées."],
    "requirements": [
        {
            "requirement": "Fournir des services techniques.",
            "category": "technical",
            "mandatory": True,
            "citations": [{"source_id": "opportunity", "quote": "Technical services required."}],
        }
    ],
    "eligibility": [],
    "missing_documents": [],
    "questions": ["Quelle est la capacité disponible ?"],
    "next_actions": ["Faire contrôler ce rapport par l’admin."],
    "limitations": ["Données de test fictives."],
}


class ModelHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_json(
            {
                "object": "list",
                "data": [{"id": "sam-hermes-smoke", "object": "model", "context_length": 65536}],
            }
        )

    def send_json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        tools = {t["function"]["name"] for t in data.get("tools", [])}
        if tools != {"read_source", "search_sources", "submit_report"}:
            raise AssertionError("Hermes exposed an unexpected tool surface")
        # Count actual tool results rather than relying on the runtime's message-name convention.
        tool_count = sum(m.get("role") == "tool" for m in data["messages"])
        if not tool_count:
            name, arguments = "search_sources", {"query": "Technical"}
        elif tool_count == 1:
            name, arguments = "read_source", {"source_id": "opportunity"}
        else:
            name, arguments = "submit_report", REPORT
            if tool_count == 2:
                # The real engine must return the tool's citation error to the model,
                # then accept its corrected submission on the following turn.
                arguments = copy.deepcopy(REPORT)
                arguments["requirements"][0]["citations"][0]["quote"] = "An invented excerpt."
                self.server.invalid_submissions += 1
        message = {"role": "assistant", "content": None}
        if name:
            message["tool_calls"] = [
                {
                    "id": "call_" + str(tool_count),
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ]
        else:
            message["content"] = json.dumps(REPORT)
        if data.get("stream"):
            delta = dict(message)
            if name:
                delta["tool_calls"][0]["index"] = 0
            chunk = {
                "id": "smoke-completion",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "sam-hermes-smoke",
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            }
            ending = {
                **chunk,
                "choices": [
                    {"index": 0, "delta": {}, "finish_reason": "tool_calls" if name else "stop"}
                ],
            }
            body = (
                "data: "
                + json.dumps(chunk)
                + "\n\ndata: "
                + json.dumps(ending)
                + "\n\ndata: [DONE]\n\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_json(
                {
                    "id": "smoke-completion",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "sam-hermes-smoke",
                    "choices": [
                        {
                            "index": 0,
                            "message": message,
                            "finish_reason": "tool_calls" if name else "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200},
                }
            )


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
    server.invalid_submissions = 0
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = Settings(
        _env_file=None,
        hermes_enabled=True,
        hermes_model="sam-hermes-smoke",
        hermes_api_key=SecretStr("synthetic-local-key"),
        hermes_base_url=f"http://127.0.0.1:{server.server_port}/v1",
    )
    try:
        result, error = run_hermes(settings, SNAPSHOT, settings.hermes_model, lambda: False)
        if error or result is None:
            raise SystemExit("Hermes smoke test failed: " + str(error))
        report, trace = validate_result(SNAPSHOT, result)
        assert report["summary"] == REPORT["summary"]
        assert {c["name"] for c in trace} == {"read_source", "search_sources"}
        assert server.invalid_submissions == 1
        print(
            json.dumps(
                {
                    "status": "ok",
                    "engine": "Hermes Agent",
                    "tool_calls": len(trace),
                    "provider": "local simulation",
                }
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
