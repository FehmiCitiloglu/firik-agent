"""Loopback-only deterministic provider and page for real Electron/agent tests."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import time

import yaml


PAGE = '''<!doctype html><html><head><meta charset="utf-8"><title>Firik yerel sayfa</title>
<style>body{margin:0;padding:32px;background:#f5f7fa;color:#182c25;font:20px sans-serif}
h1{font-size:30px}input{display:block;width:320px;height:40px;margin:18px 0}
button{width:160px;height:42px;font-size:18px}</style></head><body>
<h1 id="heading">Firik tarayıcı doğrulaması</h1><label for="note">Not</label>
<input id="note" aria-label="Not" autocomplete="off">
<label for="password">Parola</label><input id="password" aria-label="Parola" type="password" value="fixture-private-password">
<button id="send" onclick="heading.textContent=note.value||'Gönderildi';document.title='Firik gönderildi';fetch('/browser-action')">Gönder</button>
<script>note.addEventListener('input',()=>{heading.textContent=note.value||'Firik tarayıcı doğrulaması'})</script>
</body></html>'''


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, payload, content_type="application/json"):
        raw = payload.encode() if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/browser-fixture":
            return self.respond(PAGE, "text/html; charset=utf-8")
        if self.path == "/browser-action":
            self.server.browser_actions += 1
            return self.respond({"ok": True})
        if self.path == "/fixture-state":
            return self.respond({"browser_actions": self.server.browser_actions, "tools": self.server.tool_evidence})
        if self.path != "/v1/models":
            self.send_error(404)
            return
        return self.respond({"data": [{"id": name, "supports_tools": True, "supports_vision": False,
                                        "context_window": 32768, "modalities": ["text"]}
                                       for name in ["fixture-model", "fixture-alternate"]]})

    def do_POST(self):
        if self.path != "/v1/chat/completions" or self.headers.get("Authorization") != "Bearer firik_llm_local_desktop_fixture":
            self.send_error(403)
            return
        request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        user_index = max(i for i, row in enumerate(request["messages"]) if row["role"] == "user")
        prompt = str(request["messages"][user_index]["content"])
        tool_results = [row for row in request["messages"][user_index + 1:] if row["role"] == "tool"]
        browser_prompt = prompt in {"Tarayıcıdaki sayfayı oku", "Tarayıcıda Gönder düğmesine bas"}
        tool_call = None
        parts = None
        slow = "uzun" in prompt.lower()
        if browser_prompt:
            available = {tool["function"]["name"] for tool in request.get("tools", [])}
            if not tool_results:
                if "browser_snapshot" not in available:
                    parts = ["Yerel test hatası: browser_snapshot sunulmadı."]
                else:
                    tool_call = ("browser_snapshot", {})
            else:
                result = json.loads(tool_results[-1]["content"])
                self.server.tool_evidence.append({"name": tool_results[-1].get("name", ""),
                                                 "error": result.get("error"),
                                                 "private_value_leaked": "fixture-private-password" in json.dumps(result)})
                if result.get("error"):
                    parts = ["Yerel test hatası: " + str(result["error"])]
                elif prompt == "Tarayıcıdaki sayfayı oku":
                    heading = re.search(r'heading "([^"\n]+)"', result.get("content", ""))
                    parts = ["Ajan aynı sayfayı okudu: " + (heading.group(1) if heading else "BAŞLIK BULUNAMADI") + "."]
                elif len(tool_results) == 1:
                    tool_call = ("browser_click", {"target": {"role": "button", "name": "Gönder"}, "revision": result["revision"]})
                else:
                    parts = ["TypeScript tıklaması tamamlandı."]
        if not tool_call and parts is None:
            parts = ["Yerel doğrulama yanıtı: ", prompt, "!"] if not slow else ["Uzun yanıt "] + [f"adım {i} " for i in range(80)]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            if tool_call:
                name, arguments = tool_call
                delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": f"fixture-{name}-{len(tool_results)}",
                         "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}
                self.chunk(request, delta)
            else:
                for part in parts:
                    self.chunk(request, {"content": part})
                    time.sleep(.12 if slow else .03)
            chunk = {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if tool_call else "stop"}],
                     "usage": {"prompt_tokens": 12, "completion_tokens": 9, "total_tokens": 21}}
            self.wfile.write(("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode())
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def chunk(self, request, delta):
        chunk = {"id": "fixture-chat", "object": "chat.completion.chunk", "model": request["model"],
                 "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
        self.wfile.write(("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode())
        self.wfile.flush()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.browser_actions = 0
    server.tool_evidence = []
    origin = f"http://127.0.0.1:{server.server_port}"
    base_url = origin + "/v1"
    config = {"providers": {"firik": {"base_url": base_url, "default_model": "fixture-model",
                                      "api_key_env": "FIRIK_DESKTOP_TEST_KEY",
                                      "model_capabilities": {name: {"source": "verified_config", "supports_tools": True}
                                                             for name in ["fixture-model", "fixture-alternate"]}}},
              "agent": {"default_provider": "firik", "max_iterations": 6, "max_tokens": 2048},
              "disabled_default_mcps": ["web", "computer-use"], "mcp_servers": {}, "browser": {"enabled": False},
              "skills": {"autoload_builtins": False, "active_skills": []},
              "plugins": {"autoload_builtins": False}}
    (args.directory / "config.yaml").write_text(yaml.safe_dump(config))
    (args.directory / "ready.json").write_text(json.dumps({"base_url": base_url, "browser_origin": origin}))
    server.serve_forever()
