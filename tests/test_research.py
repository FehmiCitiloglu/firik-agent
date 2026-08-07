from __future__ import annotations

import socket

import pytest

from firik_agent.contracts import ToolError
from firik_agent.research import NetworkPolicy, _TextExtractor


def address(ip: str) -> list[tuple[int, int, int, str, tuple[str, int]]]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]


def test_network_policy_rejects_private_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: address("127.0.0.1"))

    with pytest.raises(ToolError, match="Non-public"):
        NetworkPolicy().validate_url("https://localhost/secrets")


def test_network_policy_accepts_public_http_and_rejects_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: address("93.184.216.34"))

    assert NetworkPolicy().validate_url("https://example.com/docs") == "https://example.com/docs"
    with pytest.raises(ToolError, match="no credentials"):
        NetworkPolicy().validate_url("https://user:pass@example.com")
    with pytest.raises(ToolError, match="Only http"):
        NetworkPolicy().validate_url("file:///etc/passwd")


def test_html_extractor_removes_active_content() -> None:
    parser = _TextExtractor()
    parser.feed("<h1>Title</h1><script>steal()</script><p>Useful <b>text</b></p>")

    assert "Title" in parser.text()
    assert "Useful text" in parser.text()
    assert "steal" not in parser.text()
