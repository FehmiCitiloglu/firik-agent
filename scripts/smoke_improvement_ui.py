"""Native widget smoke using inert fixture data; no provider or user DB access.

Run from the checkout: .venv/bin/python scripts/smoke_improvement_ui.py
This verifies presentation/dispatch only, not learning or benchmark correctness.
"""
from __future__ import annotations

import customtkinter as ctk
from firik_agent.ui.views.improvement_view import ImprovementView


class FixtureService:
    def __init__(self):
        self.calls = []
        self.proposal = {"id": "fixture-proposal", "revision": 2, "kind": "memory", "status": "pending_approval",
            "title": "Fixture knowledge", "evidence_ids": ["fixture-evidence"],
            "evaluation": {"passed": True, "cost_usd": None}, "payload": {"content": "Fixture convention"}}

    def improvement_async(self, action, *args, on_success, on_error, **kwargs):
        self.calls.append(action)
        if action == "status":
            on_success({"settings": {"mode": "observe", "automatic_reflection": False}, "metrics": {"cost_usd": None}})
        elif action == "proposals":
            on_success([self.proposal])
        elif action == "show":
            on_success({**self.proposal, "evidence": [{"id": "fixture-evidence"}], "audit": []})
        elif action == "diff":
            on_success("--- previous\n+++ proposed\n+Fixture convention\n")
        else:
            raise AssertionError("The widget smoke must not submit mutations: " + action)


def main():
    from firik_agent.ui.app import _ensure_tk_environment
    _ensure_tk_environment()
    root = ctk.CTk()
    root.geometry("1100x760")
    service = FixtureService()
    try:
        view = ImprovementView(root, service)
        view.pack(fill="both", expand=True)
        view.refresh()
        root.update()
        assert "observe" in view.mode_label.cget("text")
        view.section.set("Onaylar")
        view.refresh()
        root.update()
        assert len(view._rows) == 1
        view._select(view._rows[0])
        root.update()
        assert view.selected["revision"] == 2
        assert "fixture-evidence" in view.detail.get("1.0", "end-1c")
        assert view.action_buttons["Onayla"].cget("state") == "normal"
        view._diff()
        root.update()
        assert "+Fixture convention" in view.detail.get("1.0", "end-1c")
        assert not set(service.calls) & {"approve", "rollback", "configure"}
        print("Native learning dashboard fixture smoke passed; no mutations or model requests.")
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
