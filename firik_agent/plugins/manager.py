from __future__ import annotations
import importlib.util
import logging
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Union
from firik_agent.core.tools import ToolRegistry
from firik_agent.core.types import Message, ToolCall, ToolResult
from firik_agent.plugins.base import BasePlugin
from firik_agent.plugins.builtins.calculator import CalculatorPlugin
from firik_agent.plugins.builtins.logger import LoggerPlugin
from firik_agent.plugins.builtins.web_search import WebSearchPlugin

logger = logging.getLogger(__name__)


def _is_root_or_root_child(path: Path) -> bool:
    try:
        resolved = path.resolve()
        return resolved.parent == resolved or resolved.parent.parent == resolved.parent
    except Exception:
        return True


def _resolve_safe_plugins_dir(plugins_dir: Optional[Union[str, Path]] = None) -> Path:
    target = Path(plugins_dir or "plugins")
    try:
        if _is_root_or_root_child(target):
            return Path.home() / ".firik" / "plugins"
        return target.resolve()
    except Exception:
        return Path.home() / ".firik" / "plugins"


class PluginManager:
    """Manages plugin discovery, lifecycle hooks execution, and plugin tool registration."""

    def __init__(self, tool_registry: ToolRegistry, plugins_dir: Optional[Union[str, Path]] = None):
        self.tool_registry = tool_registry
        self.plugins_dir = _resolve_safe_plugins_dir(plugins_dir)
        try:
            self.plugins_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            fallback = Path.home() / ".firik" / "plugins"
            if self.plugins_dir != fallback:
                logger.warning(
                    "Failed to create plugins directory at %s (%s). Falling back to %s",
                    self.plugins_dir, e, fallback,
                )
                self.plugins_dir = fallback
                try:
                    self.plugins_dir.mkdir(parents=True, exist_ok=True)
                except OSError as err_fb:
                    logger.warning("Failed to create fallback plugins directory %s: %s", fallback, err_fb)
            else:
                logger.warning("Failed to create plugins directory %s: %s", self.plugins_dir, e)
        self.plugins: Dict[str, BasePlugin] = {}

    def register_plugin(self, plugin: BasePlugin) -> None:
        """Register a plugin instance and mount its tools."""
        self.plugins[plugin.name] = plugin

        # Register tools
        for tool_def, handler in plugin.get_tools():
            self.tool_registry.register(tool_def, handler)
            logger.info("Registered tool '%s' from plugin '%s'", tool_def.name, plugin.name)

        logger.info("Registered plugin '%s' (v%s)", plugin.name, plugin.version)

    def unregister_plugin(self, name: str) -> bool:
        """Unregister a plugin and remove its tools."""
        if name not in self.plugins:
            return False

        del self.plugins[name]
        self.tool_registry.unregister_by_source(f"plugin:{name}")
        logger.info("Unregistered plugin '%s'", name)
        return True

    def load_builtins(self, enabled_names: Optional[List[str]] = None) -> None:
        """Load default built-in plugins."""
        builtins = [LoggerPlugin(), CalculatorPlugin(), WebSearchPlugin()]
        for p in builtins:
            if enabled_names is None or p.name in enabled_names:
                self.register_plugin(p)

    def load_plugin_from_file(self, file_path: Union[str, Path]) -> Optional[BasePlugin]:
        """Dynamically load and instantiate a plugin from a python file."""
        path = Path(file_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Plugin file not found: {file_path}")

        module_name = f"dynamic_plugin_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, str(path))
        if not spec or not spec.loader:
            raise ImportError(f"Could not load spec for {path}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        # Find BasePlugin subclass
        plugin_instance = None
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, BasePlugin)
                and attr is not BasePlugin
            ):
                plugin_instance = attr()
                self.register_plugin(plugin_instance)
                break

        return plugin_instance

    def load_plugin_from_code(self, name: str, code: str) -> BasePlugin:
        """Save code to plugins directory and load it dynamically."""
        file_path = self.plugins_dir / f"{name}.py"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(code)

        plugin = self.load_plugin_from_file(file_path)
        if not plugin:
            raise ValueError(f"No BasePlugin subclass found in provided code for '{name}'")
        return plugin

    def discover_plugins(self) -> int:
        """Scan plugins directory and load any valid python files."""
        count = 0
        for py_file in self.plugins_dir.glob("*.py"):
            if py_file.name.startswith("__"):
                continue
            try:
                p = self.load_plugin_from_file(py_file)
                if p:
                    count += 1
            except Exception as e:
                logger.error("Failed loading plugin from %s: %s", py_file, e)
        return count

    def list_plugins(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": p.name,
                "description": p.description,
                "version": p.version,
                "is_enabled": p.is_enabled,
                "tools": [
                    t.name for t in self.tool_registry.list_tools()
                    if t.source == f"plugin:{p.name}"
                ],
            }
            for p in self.plugins.values()
        ]

    # --- Lifecycle Hooks Dispatchers ---

    async def dispatch_agent_init(self, agent: Any) -> None:
        for p in self.plugins.values():
            if p.is_enabled:
                try:
                    await p.on_agent_init(agent)
                except Exception as e:
                    logger.error("Error in on_agent_init for %s: %s", p.name, e)

    async def dispatch_user_message(self, text: str) -> str:
        current_text = text
        for p in self.plugins.values():
            if p.is_enabled:
                try:
                    new_text = await p.on_user_message(current_text)
                    if new_text is not None:
                        current_text = new_text
                except Exception as e:
                    logger.error("Error in on_user_message for %s: %s", p.name, e)
        return current_text

    async def dispatch_model_request(
        self,
        messages: List[Message],
        tools: List[Dict[str, Any]],
    ) -> None:
        for p in self.plugins.values():
            if p.is_enabled:
                try:
                    await p.on_model_request(messages, tools)
                except Exception as e:
                    logger.error("Error in on_model_request for %s: %s", p.name, e)

    async def dispatch_model_response(self, message: Message) -> None:
        for p in self.plugins.values():
            if p.is_enabled:
                try:
                    await p.on_model_response(message)
                except Exception as e:
                    logger.error("Error in on_model_response for %s: %s", p.name, e)

    async def dispatch_tool_call(self, tool_call: ToolCall) -> None:
        for p in self.plugins.values():
            if p.is_enabled:
                try:
                    await p.on_tool_call(tool_call)
                except Exception as e:
                    logger.error("Error in on_tool_call for %s: %s", p.name, e)

    async def dispatch_tool_result(self, result: ToolResult) -> None:
        for p in self.plugins.values():
            if p.is_enabled:
                try:
                    await p.on_tool_result(result)
                except Exception as e:
                    logger.error("Error in on_tool_result for %s: %s", p.name, e)

    async def dispatch_error(self, error: Exception) -> None:
        for p in self.plugins.values():
            if p.is_enabled:
                try:
                    await p.on_error(error)
                except Exception as e:
                    logger.error("Error in on_error for %s: %s", p.name, e)
