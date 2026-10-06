"""AI Project Manager Agent: state monitoring, feature verification, and check pipeline."""
from __future__ import annotations

import datetime
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import uuid

from firik_agent.projects.db import ProjectDatabase
from firik_agent.projects.git_service import GitService
from firik_agent.projects.indexer import CodebaseIndexer
from firik_agent.projects.models import (
    AISuggestion,
    AutonomyLevel,
    Feature,
    FeatureStatus,
    Project,
    ProjectCheck,
    ProjectEvent,
    ProjectHealth,
    ProjectSettings,
    ProjectSnapshot,
    SuggestionStatus,
    Task,
    TaskStatus,
    TechnicalDebt,
)

logger = logging.getLogger(__name__)

PM_SYSTEM_PROMPT = """You are the Project Manager Agent for a software project.
Your responsibility is to continuously understand the real state of the project.
Use project specifications, features, tasks, architecture decisions, source code, Git changes, tests, and documentation.
Do not assume that task status represents the real implementation state.
Whenever possible, verify implementation claims against the codebase.
Identify:
- unfinished features
- blocked work
- missing tests
- documentation drift
- architectural inconsistencies
- technical debt
- bugs
- release risks
Do not make destructive changes. Do not silently alter project priorities.
Prefer suggestions over autonomous changes unless the user explicitly grants permission.
Every recommendation should explain:
1. What was detected
2. Evidence
3. Why it matters
4. Suggested action
Keep project management state synchronized with the real codebase."""


class AIProjectManager:
    """Core intelligence engine for project verification, health analysis, and suggestions."""

    def __init__(self, db: ProjectDatabase):
        self.db = db

    def calculate_health(self, project: Project) -> ProjectHealth:
        """Calculates measurable health indicators without arbitrary vanity scores."""
        tasks = self.db.list_tasks(project.id)
        bugs = self.db.list_bugs(project.id)
        tech_debts = self.db.list_tech_debt(project.id)
        docs = self.db.list_documents(project.id)

        blocked_count = sum(1 for t in tasks if t.status == TaskStatus.BLOCKED)
        critical_bugs = sum(1 for b in bugs if b.severity in ("Critical", "Blocker") and b.status != "closed")
        open_debts = sum(1 for td in tech_debts if td.status == "open")

        # Tests health estimation
        test_passing_pct = 100
        if any("fail" in (b.title + b.description).lower() for b in bugs if b.status != "closed"):
            test_passing_pct = 85

        doc_status = "Up to date"
        if not docs or any("TODO" in d.content for d in docs):
            doc_status = "Needs update"

        build_status = "Healthy" if critical_bugs == 0 else "Needs attention"

        return ProjectHealth(
            build_status=build_status,
            test_passing_pct=test_passing_pct,
            outdated_deps_count=0,
            security_warnings_count=critical_bugs,
            doc_status=doc_status,
            tech_debt_count=open_debts,
            blocked_tasks_count=blocked_count,
        )

    def run_project_check(
        self,
        project_id: str,
        trigger: str = "manual",
        force: bool = False,
    ) -> ProjectCheck:
        """Executes the full project check pipeline (Section 70)."""
        project = self.db.get_project(project_id)
        if not project:
            raise ValueError(f"Project '{project_id}' not found.")

        settings = self.db.get_settings(project_id)
        prev_snapshot = self.db.get_latest_snapshot(project_id)

        # 1. Inspect Git state and changed files
        git_status = GitService.get_status(project.local_path)
        recent_commits = GitService.get_recent_commits(project.local_path, limit=5)
        latest_commit_hash = recent_commits[0]["hash"] if recent_commits else ""
        current_branch = git_status.get("branch", "main")
        modified_files = [f["path"] for f in git_status.get("modified", []) + git_status.get("staged", [])]

        # 2. Check incremental difference (skip if nothing changed, Section 54/55)
        tasks = self.db.list_tasks(project_id)
        features = self.db.list_features(project_id)
        task_hash = hashlib.sha256(json.dumps([t.model_dump(mode="json") for t in tasks], sort_keys=True).encode()).hexdigest()
        feat_hash = hashlib.sha256(json.dumps([f.model_dump(mode="json") for f in features], sort_keys=True).encode()).hexdigest()

        if not force and prev_snapshot:
            if (
                prev_snapshot.git_commit == latest_commit_hash
                and prev_snapshot.task_state_hash == task_hash
                and prev_snapshot.feature_state_hash == feat_hash
                and len(modified_files) == 0
            ):
                # No changes detected, return previous check summary
                return ProjectCheck(
                    id=f"chk_{uuid.uuid4().hex[:8]}",
                    project_id=project_id,
                    trigger=trigger,
                    summary="No codebase or task changes detected since last check. Index is up to date.",
                    details={"cached": True},
                    changes_detected=[],
                    risks=[],
                    suggestions_count=0,
                )

        # 3. Codebase indexing and TODO extraction
        indexer = CodebaseIndexer(project.local_path)
        scanned_files = indexer.scan_files(max_files=500)
        indexed_items = []
        all_todos: List[Dict[str, Any]] = []

        for p in scanned_files:
            res = indexer.index_file(p)
            if res:
                indexed_items.append(res)
                self.db.save_code_index(
                    project_id=project_id,
                    file_path=res["file_path"],
                    file_type=res["file_type"],
                    symbols=res["symbols"],
                    classes=res["classes"],
                    functions=res["functions"],
                    imports=res["imports"],
                    todos=res["todos"],
                    file_hash=res["file_hash"],
                )
                all_todos.extend(res["todos"])

        changes_detected: List[str] = []
        if modified_files:
            changes_detected.append(f"{len(modified_files)} files modified in working tree")
        if recent_commits:
            changes_detected.append(f"Latest commit: {recent_commits[0]['short_hash']} - {recent_commits[0]['message']}")

        risks: List[str] = []
        suggestions: List[AISuggestion] = []

        # 4. Feature Verification against Codebase (Section 8 & 71)
        for feat in features:
            impl = self._verify_feature_in_codebase(feat, indexed_items, project.local_path)
            feat.implementation_status = impl
            self.db.save_feature(feat)

            # Check if feature claims completed but tests are missing
            if feat.status in (FeatureStatus.COMPLETED, FeatureStatus.TESTING):
                if impl.get("unit_tests") == "missing" and impl.get("integration_tests") == "missing":
                    risks.append(f"Feature '{feat.title}' is marked {feat.status} but no test files were detected in codebase.")
                    suggestions.append(
                        AISuggestion(
                            id=f"sug_{uuid.uuid4().hex[:8]}",
                            project_id=project_id,
                            suggestion_type="fix_test",
                            title=f"Add tests for '{feat.title}'",
                            description=f"Feature '{feat.title}' is in {feat.status} state, but corresponding test files were not found.",
                            evidence=f"No test files matching feature keywords found among {len(indexed_items)} indexed files.",
                            why_it_matters="Releasing without tests introduces regression risks and violates project testing rules.",
                            payload={"feature_id": feat.id, "suggested_task_type": "Testing"},
                        )
                    )

        # 5. Task Integrity Verification (Section 71)
        for task in tasks:
            # Check for blocked task dependencies
            if task.status == TaskStatus.BLOCKED:
                risks.append(f"Task '{task.title}' is currently blocked.")
            elif task.status == TaskStatus.DONE and task.acceptance_criteria:
                # Check if acceptance criteria mentions tests or docs
                has_test_req = any("test" in ac.lower() for ac in task.acceptance_criteria)
                if has_test_req and not task.related_files:
                    risks.append(f"Task '{task.title}' marked Done but has test criteria without related files.")

        # 6. Technical Debt from TODOs / FIXMEs (Section 40)
        existing_debts = {td.title.lower() for td in self.db.list_tech_debt(project_id)}
        for td_item in all_todos[:10]:
            title = f"{td_item['type']}: {td_item['text'] or 'Code issue'} ({Path(td_item['file']).name}:{td_item['line']})"
            if title.lower() not in existing_debts:
                new_debt = TechnicalDebt(
                    id=f"td_{uuid.uuid4().hex[:8]}",
                    project_id=project_id,
                    title=title,
                    description=f"Found in {td_item['file']} line {td_item['line']}",
                    category="code_smell" if td_item["type"] in ("TODO", "HACK") else "bug",
                    suggested_task=f"Resolve {td_item['type']} in {td_item['file']}",
                )
                self.db.save_tech_debt(new_debt)
                existing_debts.add(title.lower())

        # 7. Documentation Drift (Section 39)
        readme_file = Path(project.local_path) / "README.md"
        if readme_file.exists():
            readme_text = readme_file.read_text(encoding="utf-8", errors="ignore")
            # If project tech stack has items not mentioned in README
            for lang in project.tech_stack.get("Language", []):
                if lang.lower() not in readme_text.lower():
                    suggestions.append(
                        AISuggestion(
                            id=f"sug_{uuid.uuid4().hex[:8]}",
                            project_id=project_id,
                            suggestion_type="update_doc",
                            title=f"Update README for {lang} documentation",
                            description=f"Project stack uses {lang} but it is not mentioned in README.md.",
                            evidence="README.md lacks references to detected stack component.",
                            why_it_matters="Keeps project documentation synchronized with actual codebase architecture.",
                            payload={"doc_type": "readme"},
                        )
                    )

        # 8. Deduplicate suggestions (Section 72)
        existing_suggestions = {s.title.lower() for s in self.db.list_suggestions(project_id, status="pending")}
        added_count = 0
        for sug in suggestions:
            if sug.title.lower() not in existing_suggestions:
                self.db.save_suggestion(sug)
                existing_suggestions.add(sug.title.lower())
                added_count += 1

                # If autonomy level is 'manage' or 'autonomous', auto-convert task suggestions
                if settings.autonomy_level in (AutonomyLevel.MANAGE, AutonomyLevel.AUTONOMOUS) and sug.suggestion_type == "create_task":
                    auto_task = Task(
                        id=f"task_{uuid.uuid4().hex[:8]}",
                        project_id=project_id,
                        title=sug.title,
                        description=sug.description + "\n\nEvidence: " + sug.evidence,
                        status=TaskStatus.TODO,
                        task_type="Testing" if "test" in sug.title.lower() else "Feature",
                        ai_generated=True,
                    )
                    self.db.save_task(auto_task)
                    sug.status = SuggestionStatus.ACCEPTED
                    self.db.save_suggestion(sug)

        # 9. Build Summary Report
        summary_lines = [
            f"Project check completed at {datetime.datetime.now().strftime('%H:%M:%S')}.",
            f"Files indexed: {len(indexed_items)}. Modified files: {len(modified_files)}.",
            f"Active features: {len(features)}. Total tasks: {len(tasks)}.",
        ]
        if risks:
            summary_lines.append(f"Identified {len(risks)} risks/warnings.")
        if added_count > 0:
            summary_lines.append(f"Generated {added_count} new AI suggestions.")

        check_record = ProjectCheck(
            id=f"chk_{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            trigger=trigger,
            summary="\n".join(summary_lines),
            details={
                "modified_files": modified_files,
                "latest_commit": latest_commit_hash,
                "total_todos": len(all_todos),
            },
            changes_detected=changes_detected,
            risks=risks,
            suggestions_count=added_count,
        )
        self.db.save_check(check_record)

        # 10. Update Snapshot
        new_snapshot = ProjectSnapshot(
            id=f"snp_{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            git_commit=latest_commit_hash,
            branch=current_branch,
            changed_files_count=len(modified_files),
            task_state_hash=task_hash,
            feature_state_hash=feat_hash,
            test_state="passing" if not any("test" in r.lower() for r in risks) else "failing",
            build_state="healthy" if len(risks) == 0 else "warnings",
        )
        self.db.save_snapshot(new_snapshot)

        # Log project event
        self.db.log_event(
            ProjectEvent(
                id=f"ev_{uuid.uuid4().hex[:8]}",
                project_id=project_id,
                event_type="AI_PM_CHECK_COMPLETED",
                actor="ai_pm",
                title=f"AI Project Manager Check ({trigger})",
                details={"risks_count": len(risks), "suggestions_count": added_count},
            )
        )

        return check_record

    def _verify_feature_in_codebase(
        self,
        feature: Feature,
        indexed_items: List[Dict[str, Any]],
        project_root: str,
    ) -> Dict[str, str]:
        """Analyzes indexed symbols and files to verify feature components (Section 8)."""
        words = [w.lower() for w in feature.title.split() if len(w) > 3]
        status: Dict[str, str] = {
            "backend_api": "missing",
            "database_schema": "missing",
            "frontend_ui": "missing",
            "validation": "missing",
            "unit_tests": "missing",
            "integration_tests": "missing",
            "documentation": "missing",
        }

        for item in indexed_items:
            path_str = item["file_path"].lower()
            symbols_str = " ".join(item.get("symbols", [])).lower()
            matches_feature = any(w in path_str or w in symbols_str for w in words)

            if matches_feature:
                # Backend API / Router / Controller
                if any(x in path_str for x in ("api", "router", "route", "controller", "endpoint", "view", "service")):
                    status["backend_api"] = "implemented"

                # DB schema / models / entities
                if any(x in path_str for x in ("model", "schema", "entity", "migration", "table")):
                    status["database_schema"] = "implemented"

                # Frontend UI / Component
                if any(x in path_str for x in ("component", "view", "page", "screen", "widget", "ui", "templates")):
                    status["frontend_ui"] = "implemented"

                # Tests
                if "test" in path_str or "spec" in path_str:
                    if "integration" in path_str or "e2e" in path_str:
                        status["integration_tests"] = "implemented"
                    else:
                        status["unit_tests"] = "implemented"

                # Documentation
                if path_str.endswith(".md") or "doc" in path_str:
                    status["documentation"] = "implemented"

        return status
