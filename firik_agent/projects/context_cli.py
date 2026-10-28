"""Offline, read-only context inspection using the desktop's preparation path."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile

from firik_agent.core.session import ChatSession
from firik_agent.core.types import Message
from firik_agent.projects.coding_runtime import CodingRunControl, CodingToolFilter
from firik_agent.projects.coding_service import ProjectCodingService
from firik_agent.projects.context_inspector import inspect_request_context
from firik_agent.projects.db import ProjectDatabase
from firik_agent.projects.models import Project
from firik_agent.projects.tool_selection import select_tool_schemas


def inspect_context(args) -> dict:
    with tempfile.TemporaryDirectory(prefix="firik-context-inspect-") as temporary:
        if args.project_id:
            if not args.database or not args.database.is_file():
                raise ValueError("Kayıtlı proje için mevcut --database dosyasını belirtin.")
            db = ProjectDatabase(args.database)
            project = db.get_project(args.project_id)
            if not project:
                raise ValueError("Proje bulunamadı.")
        else:
            root = (args.root or Path.cwd()).resolve()
            if not root.is_dir():
                raise ValueError("Proje klasörü bulunamadı.")
            db = ProjectDatabase(Path(temporary) / "inspection.db")
            project = Project(id="inspection", name=root.name, local_path=str(root))
            db.create_project(project)
        exclusions = list(dict.fromkeys(db.get_settings(project.id).coding_preferences.get("excluded_files", []) + args.exclude))
        prepared = ProjectCodingService(db).prepare_coding_prompt(
            project.id, args.request, harness_profile=args.profile,
            relevant_paths=args.file, excluded_paths=exclusions)
        session = ChatSession()
        control = CodingRunControl(session.session_id, project.id, mode="ask",
                                   excluded_paths=exclusions)
        control.harness_profile = prepared["harness_profile"]
        tools = CodingToolFilter(project.local_path, control)
        schemas, selection = select_tool_schemas(tools.get_schemas(session), task=args.request, profile=control.harness_profile)
        data = prepared["project_context"]
        report = inspect_request_context(
            [Message(role="system", content=prepared["trusted_system_prompt"]),
             Message(role="user", content=data), Message(role="user", content=prepared["user_prompt"])],
            schemas, prepared["capabilities"], visibility_report=prepared["visibility_report"],
            context_items=prepared["context_items"], project_context=data,
            harness_profile=control.harness_profile, tool_selection=selection,
            excluded_sources=prepared["unavailable_report"], context_pipeline=prepared.get("context_pipeline"),
            output_tokens=control.harness_profile["max_output_tokens"])
        return {**report, "preview_mode": "ask", "executes_tools": False,
                "context_pipeline": prepared.get("context_pipeline", {}), "index_status": prepared["index_status"],
                "provider_requests": 0}


def print_inspection(args) -> int:
    print(json.dumps(inspect_context(args), ensure_ascii=False, indent=2, default=str))
    return 0
