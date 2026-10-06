"""Lazy explorer metadata, context actions and native keyboard behavior."""
from pathlib import Path
from types import SimpleNamespace

import customtkinter as ctk

from firik_agent.projects.db import ProjectDatabase
from firik_agent.projects.indexer import CodebaseIndexer
from firik_agent.projects.models import Project
from firik_agent.ui.app import _ensure_tk_environment
from firik_agent.ui.components.coding_explorer import CodingExplorer


def test_context_markers_and_actions_use_canonical_metadata_without_source_reads(tmp_path, monkeypatch):
    project_root = tmp_path / "source"
    project_root.mkdir()
    (project_root / "main.py").write_text("class FeatureService: pass\n", encoding="utf-8")
    db = ProjectDatabase(tmp_path / "projects.db")
    db.create_project(Project(id="p", name="Proje", local_path=str(project_root)))
    db.save_code_index("p", **CodebaseIndexer(project_root).index_file(project_root / "main.py"))
    monkeypatch.setattr(CodingExplorer, "_background", lambda self, operation, callback: callback(operation()))
    monkeypatch.setattr("firik_agent.ui.components.coding_explorer.GitService.get_status",
                        lambda *args: {"branch": "main", "files": []})
    _ensure_tk_environment()
    root = ctk.CTk()
    actions, contexts, preferences, opened = [], [], [], []
    explorer = CodingExplorer(root, SimpleNamespace(projects=SimpleNamespace(db=db)), "p",
        on_open=lambda *args: opened.append(args), on_context=contexts.append, on_preferences=preferences.append,
        on_action=lambda *args: actions.append(args))
    explorer.pack(fill="both", expand=True)
    try:
        root.update()
        monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Source text was read")))
        explorer.mark_paths(["main.py"], ["main.py"], {"branch": "feature", "files": [{"path": "main.py", "index": " ", "worktree": "M"}]})
        node = next(node for node, metadata in explorer.nodes.items() if metadata["path"] == "main.py")
        assert "Git M" in explorer.tree.item(node, "text") and "Bağlam" in explorer.tree.item(node, "text") and "Ajan" in explorer.tree.item(node, "text")
        explorer.tree.selection_set(node)
        assert explorer._key_action("remove_context") == "break"
        assert "Bağlam" not in explorer.tree.item(node, "text")
        assert preferences[-1] == {"context_paths": []}
        explorer.perform_action("exclude", "main.py")
        assert preferences[-1] == {"excluded_files": ["main.py"]}
        assert "AI hariç" in explorer.tree.item(node, "text")
        explorer.perform_action("add_context", "main.py")
        explorer.perform_action("diff", "main.py")
        assert contexts == ["main.py"] and actions[-1] == ("diff", "main.py")
        explorer._key_action("open_ide")
        assert opened[-1] == ("main.py", True)
        symbols = explorer._search_items("Feature", "Sembol")
        assert symbols[0]["matched_symbols"] == ["FeatureService"]
        assert explorer._search_items("Feature", "Dosya") == []
        # A canonical clean snapshot clears old row Git markers and survives refresh.
        explorer.mark_paths(git_status={"branch": "feature", "files": []})
        assert "Git M" not in explorer.tree.item(node, "text")
        monkeypatch.setattr("firik_agent.ui.components.coding_explorer.GitService.get_status",
                            lambda *args: (_ for _ in ()).throw(AssertionError("Second Git source queried")))
        explorer.refresh()
        assert "feature" in explorer.hint.cget("text")
    finally:
        root.destroy()
