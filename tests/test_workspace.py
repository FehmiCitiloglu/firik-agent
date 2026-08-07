from __future__ import annotations

from pathlib import Path

import pytest

from firik_agent.contracts import ToolError
from firik_agent.workspace import Workspace


def test_workspace_reads_writes_replaces_and_searches(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)

    created = workspace.write_text("src/example.py", "value = 1\n")
    replaced = workspace.replace_text("src/example.py", "1", "2")

    assert created["changed"] is True
    assert replaced["replacements"] == 1
    assert workspace.read_text("src/example.py") == "value = 2\n"
    assert workspace.list_files("**/*.py") == ["src/example.py"]
    assert workspace.search("VALUE", glob="**/*.py")[0]["line"] == 1


def test_workspace_rejects_traversal_and_internal_record_writes(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)

    with pytest.raises(ToolError, match="escapes workspace"):
        workspace.read_text("../secret.txt")
    with pytest.raises(ToolError, match=r"internal \.firik-agent"):
        workspace.write_text(".firik-agent/tasks/forged.json", "{}")


def test_workspace_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ToolError, match="escapes workspace"):
        Workspace(tmp_path).read_text("link/secret.txt")


def test_workspace_rejects_oversized_and_binary_files(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path, max_read_bytes=4)
    (tmp_path / "large.txt").write_text("12345", encoding="utf-8")
    (tmp_path / "binary").write_bytes(b"a\x00b")

    with pytest.raises(ToolError, match="limit"):
        workspace.read_text("large.txt")
    with pytest.raises(ToolError, match="Binary"):
        workspace.read_text("binary", max_bytes=4)
