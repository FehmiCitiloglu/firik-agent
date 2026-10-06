"""Security policy, sensitive action evaluation, audit logging, and emergency stop."""
from __future__ import annotations

import logging
import re
import threading
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from firik_agent.computer_use.models import (
    ActionBlockedError,
    ConfirmationRequest,
    RiskLevel,
    UserCancelledError,
)

logger = logging.getLogger(__name__)

# Patterns for sensitive text redaction in logs
SENSITIVE_PATTERNS = [
    re.compile(r"\b(?:\d[ -]*?){13,16}\b"),  # Credit card numbers
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-\.]{6,}"),  # Bearer tokens
    re.compile(r"(?i)(password|passwd|secret|token|api_key|apikey)\s*[:=]\s*['\"]?[^\s'\"]{6,}['\"]?"),
    re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----"),
]


# Sensitive destructive action keywords
DESTRUCTIVE_KEYWORDS = [
    "delete account", "format disk", "rm -rf", "drop database", "purge",
    "sudo rm", "mkfs", "dd if=", "wipefs", "killall -9", "shutdown -h",
    "reboot", "del /f /s", "format c:", "reg delete", "payment", "checkout",
    "buy now", "confirm purchase", "order now", "transfer funds",
]


class ComputerUseConfig(BaseModel):
    enabled: bool = True
    allow_screenshots: bool = True
    allow_pointer: bool = True
    allow_keyboard: bool = True
    allow_clipboard: bool = True
    allow_window_control: bool = True
    allow_accessibility: bool = True
    allowed_applications: List[str] = Field(default_factory=list)
    blocked_applications: List[str] = Field(default_factory=lambda: [
        "Terminal", "iTerm2", "cmd.exe", "powershell.exe", "System Preferences",
        "System Settings", "Registry Editor", "Keychain Access", "Windows Security",
    ])
    max_actions_per_turn: int = 50
    action_timeout: float = 30.0
    require_session_confirmation: bool = False


class SecurityManager:
    """Enforces safety constraints, rate-limits, sensitive action detection, and cancellation."""

    def __init__(self, config: Optional[ComputerUseConfig] = None):
        self.config = config or ComputerUseConfig()
        self._stop_event = threading.Event()
        self._action_counter = 0
        self._lock = threading.Lock()

    def request_stop(self) -> None:
        """Signals an emergency stop, aborting ongoing and future computer actions."""
        self._stop_event.set()
        logger.warning("Emergency stop requested for Computer Use.")

    def reset_stop(self) -> None:
        """Resets the emergency stop state."""
        self._stop_event.clear()

    def is_stopped(self) -> bool:
        return self._stop_event.is_set()

    def check_stop(self) -> None:
        if self._stop_event.is_set():
            raise UserCancelledError(
                "Computer action cancelled by emergency stop.",
                recovery_hint="Stop button was pressed or execution was cancelled.",
            )

    def reset_turn(self) -> None:
        with self._lock:
            self._action_counter = 0

    def check_permission_and_limits(self, capability: str, app_name: Optional[str] = None) -> None:
        """Verifies whether the action is permitted under the current security policy."""
        self.check_stop()

        if not self.config.enabled:
            raise ActionBlockedError(
                "Computer Use is globally disabled in configuration.",
                recovery_hint="Enable computer_use.enabled in config.yaml.",
            )

        # Check capability permissions
        cap_map = {
            "screenshot": self.config.allow_screenshots,
            "pointer": self.config.allow_pointer,
            "keyboard": self.config.allow_keyboard,
            "clipboard": self.config.allow_clipboard,
            "window": self.config.allow_window_control,
            "accessibility": self.config.allow_accessibility,
        }
        if capability in cap_map and not cap_map[capability]:
            raise ActionBlockedError(
                f"Action '{capability}' is blocked by security policy.",
                recovery_hint=f"Enable allow_{capability} in configuration.",
            )

        # Check blocked applications
        if app_name:
            app_lower = app_name.lower()
            for blocked in self.config.blocked_applications:
                if blocked.lower() in app_lower:
                    raise ActionBlockedError(
                        f"Interaction with application '{app_name}' is blocked by security policy.",
                        recovery_hint="Remove from blocked_applications list if safe.",
                    )

            if self.config.allowed_applications:
                allowed = any(a.lower() in app_lower for a in self.config.allowed_applications)
                if not allowed:
                    raise ActionBlockedError(
                        f"Application '{app_name}' is not in the allowed_applications whitelist.",
                        recovery_hint="Add to allowed_applications in configuration.",
                    )

        # Turn action limit counter
        with self._lock:
            self._action_counter += 1
            if self._action_counter > self.config.max_actions_per_turn:
                raise ActionBlockedError(
                    f"Maximum actions per turn limit ({self.config.max_actions_per_turn}) exceeded.",
                    recovery_hint="Break actions into smaller turns and re-observe.",
                )

    def evaluate_risk(self, action: str, details: Dict[str, Any]) -> ConfirmationRequest:
        """Evaluates whether an action requires explicit user confirmation."""
        search_str = f"{action} {str(details)}".lower()
        risk = RiskLevel.LOW
        reason = "Normal operation"

        for kw in DESTRUCTIVE_KEYWORDS:
            if kw in search_str:
                risk = RiskLevel.CRITICAL
                reason = f"Potentially destructive or irreversible operation detected: '{kw}'"
                break

        return ConfirmationRequest(action=action, details=details, risk_level=risk, reason=reason)

    def check_rate_limit(self) -> None:
        """Checks rate limit and increments counter."""
        self.check_permission_and_limits(capability="action")

    def is_app_blocked(self, app_name: str) -> bool:
        """Returns True if the application is in the blocked list."""
        if not app_name:
            return False
        app_lower = app_name.lower()
        for blocked in self.config.blocked_applications:
            if blocked.lower() in app_lower:
                return True
        return False

    def is_text_destructive(self, text: str) -> bool:
        """Returns True if the text contains destructive or sensitive action keywords."""
        t_lower = text.lower()
        return any(kw in t_lower for kw in DESTRUCTIVE_KEYWORDS)

    @staticmethod
    def redact_sensitive_text(text: str) -> str:
        """Redacts secrets, credit cards, and passwords for audit logging."""
        sanitized = text
        for pat in SENSITIVE_PATTERNS:
            sanitized = pat.sub("[REDACTED]", sanitized)
        return sanitized

    def log_audit(self, event_name: str, details: Dict[str, Any]) -> None:
        """Logs an audit entry with sensitive text redacted."""
        clean_details = {}
        for k, v in details.items():
            if isinstance(v, str):
                clean_details[k] = self.redact_sensitive_text(v)
            else:
                clean_details[k] = v
        logger.info("AUDIT [%s]: %s", event_name, clean_details)

    def log_action(self, action: str, details: Dict[str, Any], status: str = "success", duration: float = 0.0) -> None:
        """Convenience method to log an action with duration and status."""
        entry = dict(details)
        entry["status"] = status
        entry["duration_s"] = round(duration, 4)
        self.log_audit(action, entry)

