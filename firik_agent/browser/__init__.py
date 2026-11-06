"""Native, host-authorized browser execution (Playwright is optional)."""
from firik_agent.browser.config import BrowserConfig
from firik_agent.browser.models import BrowserError, BrowserGrant, BrowserIdentity

__all__ = ["BrowserConfig", "BrowserError", "BrowserGrant", "BrowserIdentity", "BrowserManager"]


def __getattr__(name):
    if name == "BrowserManager":
        from firik_agent.browser.manager import BrowserManager
        return BrowserManager
    raise AttributeError(name)
