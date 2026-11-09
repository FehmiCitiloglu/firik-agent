"""Loopback-only fake FIRIK endpoint for real Electron/agent smoke tests."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import time

import yaml


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        payload = {"data": [{"id": name, "supports_tools": False, "supports_vision": False,
                              "context_window": 32768, "modalities": ["text"]}
                             for name in ["fixture-model", "fixture-alternate"]]}
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        if self.path != "/v1/chat/completions" or self.headers.get("Authorization") != "Bearer firik_llm_local_desktop_fixture":
            self.send_error(403)
            return
        request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        prompt = next(row["content"] for row in reversed(request["messages"]) if row["role"] == "user")
        slow = "uzun" in str(prompt).lower()
        parts = ["Yerel doğrulama yanıtı: ", str(prompt), "!"] if not slow else ["Uzun yanıt "] + [f"adım {i} " for i in range(80)]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            for part in parts:
                chunk = {"id": "fixture-chat", "object": "chat.completion.chunk", "model": request["model"],
                         "choices": [{"index": 0, "delta": {"content": part}, "finish_reason": None}]}
                self.wfile.write(("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode())
                self.wfile.flush()
                time.sleep(.12 if slow else .03)
            chunk = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                     "usage": {"prompt_tokens": 12, "completion_tokens": 9, "total_tokens": 21}}
            self.wfile.write(("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode())
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    base_url = f"http://127.0.0.1:{server.server_port}/v1"
    config = {"providers": {"firik": {"base_url": base_url, "default_model": "fixture-model",
                                      "api_key_env": "FIRIK_DESKTOP_TEST_KEY",
                                      "model_capabilities": {"fixture-model": {"source": "verified_config", "supports_tools": False}}}},
              "agent": {"default_provider": "firik", "max_iterations": 4, "max_tokens": 2048},
              "disabled_default_mcps": ["web", "computer-use"], "mcp_servers": {}, "browser": {"enabled": False},
              "skills": {"autoload_builtins": False, "active_skills": []},
              "plugins": {"autoload_builtins": False}}
    (args.directory / "config.yaml").write_text(yaml.safe_dump(config))
    (args.directory / "ready.json").write_text(json.dumps({"base_url": base_url}))
    server.serve_forever()
