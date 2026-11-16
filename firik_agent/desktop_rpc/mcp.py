"""Private desktop MCP management using the shared service's async lifecycle."""
from __future__ import annotations

from firik_agent.desktop_rpc.models import (
    MCPConfig, MCPListResult, MCPOperationResult, MCPServerInfo,
)
from firik_agent.mcp.models import (
    MCPServerConfig, redact_secrets, resolve_server_env, store_mcp_secret,
)


class MCPRPC:
    def __init__(self, server):
        self.server, self.service = server, server.service
        self.busy = False

    def _clean(self, value, config):
        secrets = [*resolve_server_env(config).values(), *config.headers.values()]
        return self.server._clean(redact_secrets(str(value), secrets), 1024)

    def list(self):
        servers = []
        for conn in list(self.service.mcp_manager.connections.values()):
            config = conn.config
            public = config.model_dump(mode="json")
            # Empty values mean "keep the saved value" when editing. Never send
            # keyring references, resolved secrets or authentication headers back.
            public["secret_env"] = dict.fromkeys(config.secret_env, "")
            public["headers"] = dict.fromkeys(config.headers, "")
            for key in ("command", "cwd", "url"):
                public[key] = public[key] or ""
            servers.append(MCPServerInfo(config=MCPConfig(**public), status=conn.status.value,
                tool_count=len(conn.cached_tools), error=self._clean(conn.last_error, config) if conn.last_error else None))
        return MCPListResult(servers=servers)

    def handle(self, request_id, method, params, result_type):
        from firik_agent.desktop_rpc.server import RPCError
        with self.server._lock:
            if not self.server._hello:
                raise RPCError(-32000, "Önce protokol sürümü doğrulanmalıdır.")
            if method == "mcp.list":
                self.server.send({"jsonrpc": "2.0", "id": request_id, "result": self.list().model_dump()})
                return
            if self.busy:
                raise RPCError(-32009, "MCP işlemi sürüyor; tamamlanmasını bekleyin.")
            if (any(run.status == "running" for run in self.server._runs.values())
                    or self.server.workspaces.coding_busy()):
                raise RPCError(-32009, "MCP sunucularını yönetmeden önce çalışan yanıtları durdurun.")
            name = params.config.name if method == "mcp.save" else params.name
            conn = self.service.mcp_manager.get_connection(name)
            if method == "mcp.save" and params.mode == "create":
                if conn:
                    raise RPCError(-32009, "Bu adla bir MCP sunucusu zaten var; farklı bir ad seçin.")
            elif conn is None:
                raise RPCError(-32004, "MCP sunucusu bulunamadı.")
            if method == "mcp.command" and params.command in {"connect", "restart"} and not conn.config.enabled:
                raise RPCError(-32009, "Önce sunucuyu düzenleyip etkinleştirin.")
            self.busy = True

        def send_result(result):
            with self.server._lock:
                try:
                    checked = result_type.model_validate(result)
                    self.busy = False
                    self.server.send({"jsonrpc": "2.0", "id": request_id, "result": checked.model_dump()})
                finally:
                    self.busy = False

        def done():
            if method in {"mcp.save", "mcp.remove"}:
                send_result(self.list())
            else:
                current = self.service.mcp_manager.get_connection(name)
                send_result(MCPOperationResult(message="MCP işlemi tamamlandı.",
                    tool_count=len(current.cached_tools) if current else 0))

        def failed(_error):
            # Subprocess/HTTP exceptions can include credentials supplied in the
            # request. Details are available in the redacted per-server status.
            self.busy = False
            self.server.send({"jsonrpc": "2.0", "id": request_id, "error": {
                "code": -32001, "message": "MCP işlemi tamamlanamadı. Sunucu durumunu ve bağlantı ayarlarını kontrol edin."}})

        try:
            if method == "mcp.save":
                raw = params.config.model_dump()
                old = conn.config if conn else None
                raw["headers"] = {key: value or (old.headers.get(key, "") if old else "")
                                  for key, value in raw["headers"].items()}
                raw["secret_env"] = {key: store_mcp_secret(name, key, value) if value else
                                     (old.secret_env.get(key, "") if old else "")
                                     for key, value in raw["secret_env"].items()}
                if raw["transport"] == "stdio":
                    raw["url"], raw["headers"] = None, {}
                else:
                    raw["command"], raw["args"], raw["cwd"] = None, [], None
                config = MCPServerConfig(**raw)
                if params.mode == "create":
                    self.service.add_mcp_server_async(config, on_success=done, on_error=failed)
                else:
                    self.service.update_mcp_server_async(name, config, on_success=done, on_error=failed)
            elif method == "mcp.remove":
                self.service.remove_mcp_server_async(name, on_success=done, on_error=failed)
            elif params.command == "test":
                def tested(result):
                    send_result(MCPOperationResult(success=result.success, tool_count=result.tool_count,
                        tool_names=result.tool_names, message=(f"Bağlantı başarılı · {result.tool_count} araç bulundu."
                        if result.success else self._clean(result.error or "Bağlantı kurulamadı.", conn.config))))
                self.service.test_mcp_async(name, on_success=tested, on_error=failed)
            else:
                action = {"connect": self.service.connect_mcp_async, "disconnect": self.service.disconnect_mcp_async,
                          "restart": self.service.restart_mcp_async}[params.command]
                action(name, on_success=done, on_error=failed)
        except Exception:
            self.busy = False
            raise
