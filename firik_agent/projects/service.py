"""Central ProjectService facade coordinating storage, git, indexer, PM, and coding agents."""
from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import uuid

from firik_agent.projects.analyzer import detect_tech_stack, generate_project_template_data
from firik_agent.projects.coding_service import ProjectCodingService
from firik_agent.projects.db import ProjectDatabase
from firik_agent.projects.events import ProjectEventBus, ProjectEventType
from firik_agent.projects.git_service import GitService
from firik_agent.projects.indexer import CodebaseIndexer
from firik_agent.projects.models import (
    AISuggestion,
    ArchitectureDecision,
    ArchitectureDoc,
    Bug,
    CodingSession,
    Document,
    Feature,
    FeatureStatus,
    Milestone,
    Priority,
    Project,
    ProjectCheck,
    ProjectEvent,
    ProjectHealth,
    ProjectInstruction,
    ProjectMemory,
    ProjectSettings,
    ProjectStatus,
    PromptTemplate,
    Specification,
    Sprint,
    SuggestionStatus,
    Task,
    TaskStatus,
    TechnicalDebt,
)
from firik_agent.projects.pm_service import AIProjectManager
from firik_agent.projects.scheduler import ProjectScheduler


class ProjectService:
    """High-level service coordinating all Project Workspace features."""

    def __init__(self, db_path: Optional[str | Path] = None):
        self.db = ProjectDatabase(db_path)
        self.event_bus = ProjectEventBus.get_instance()
        self.pm = AIProjectManager(self.db)
        self.scheduler = ProjectScheduler(self.pm, self.event_bus)
        self.coding = ProjectCodingService(self.db)
        self.scheduler.start()

    # --- Project Management ---

    def create_project(
        self,
        name: str,
        local_path: str,
        description: str = "",
        repository_url: str = "",
        template: str = "Web Application",
        icon: str = "🚀",
        tags: Optional[List[str]] = None,
    ) -> Project:
        """Creates and indexes a new project from a local folder or template."""
        p_path = Path(local_path).resolve()
        p_path.mkdir(parents=True, exist_ok=True)

        project_id = f"proj_{uuid.uuid4().hex[:8]}"
        tech_stack = detect_tech_stack(p_path)

        # Check Git
        active_branch = "main"
        last_hash = ""
        last_msg = ""
        if GitService.is_git_repo(p_path):
            active_branch = GitService.get_current_branch(p_path)
            if not repository_url:
                repository_url = GitService.get_remote_url(p_path)
            commits = GitService.get_recent_commits(p_path, limit=1)
            if commits:
                last_hash = commits[0]["short_hash"]
                last_msg = commits[0]["message"]

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        project = Project(
            id=project_id,
            name=name.strip() or p_path.name,
            description=description,
            local_path=str(p_path),
            repository_url=repository_url,
            tech_stack=tech_stack,
            active_branch=active_branch,
            icon=icon,
            tags=tags or [],
            status=ProjectStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            last_activity_at=now,
            last_commit_hash=last_hash,
            last_commit_message=last_msg,
        )

        self.db.create_project(project)

        # Populate starter template data
        tpl_data = generate_project_template_data(project_id, template)
        for inst in tpl_data["instructions"]:
            self.db.save_instruction(inst)
        for pr in tpl_data["prompts"]:
            self.db.save_prompt(pr)
        for spec in tpl_data["specs"]:
            self.db.save_spec(spec)
        for arch in tpl_data["arch_docs"]:
            self.db.save_arch_doc(arch)
        for feat in tpl_data["features"]:
            self.db.save_feature(feat)
        for task in tpl_data["tasks"]:
            self.db.save_task(task)

        # Log creation event
        self.db.log_event(
            ProjectEvent(
                id=f"ev_{uuid.uuid4().hex[:8]}",
                project_id=project_id,
                event_type=ProjectEventType.PROJECT_CREATED,
                actor="user",
                title=f"Project '{project.name}' created",
                details={"local_path": str(p_path), "template": template},
            )
        )
        self.event_bus.emit(ProjectEventType.PROJECT_CREATED, {"project_id": project_id})

        # Run initial background check
        self.scheduler.trigger_check(project_id, trigger_name="project_created", force=True)

        return project

    def clone_and_create_project(
        self,
        name: str,
        repository_url: str,
        destination_parent: str,
        template: str = "Custom",
        icon: str = "🐙",
    ) -> Project:
        """Clones a remote Git repository and creates a new project workspace."""
        repo_name = Path(repository_url.rstrip("/").split("/")[-1]).stem
        dest_dir = Path(destination_parent) / (name or repo_name)

        ok, msg = GitService.clone_repository(repository_url, dest_dir)
        if not ok:
            raise RuntimeError(f"Git clone error: {msg}")

        return self.create_project(
            name=name or repo_name,
            local_path=str(dest_dir),
            repository_url=repository_url,
            template=template,
            icon=icon,
        )

    def list_projects(
        self,
        search_query: str = "",
        status_filter: Optional[str] = None,
        view_mode: str = "Grid",  # Grid, List, Recent, Favorites, Archived
    ) -> List[Project]:
        """Lists projects with search and view filters."""
        all_projects = self.db.list_projects()

        if view_mode == "Archived":
            all_projects = [p for p in all_projects if p.status == ProjectStatus.ARCHIVED]
        elif view_mode == "Favorites":
            all_projects = [p for p in all_projects if p.is_favorite and p.status != ProjectStatus.ARCHIVED]
        else:
            all_projects = [p for p in all_projects if p.status != ProjectStatus.ARCHIVED]

        if status_filter and status_filter != "All":
            all_projects = [p for p in all_projects if p.status == status_filter]

        if search_query.strip():
            q = search_query.lower().strip()
            all_projects = [
                p for p in all_projects
                if q in p.name.lower()
                or q in p.description.lower()
                or q in p.local_path.lower()
                or any(q in t.lower() for t in p.tags)
                or any(q in val.lower() for vals in p.tech_stack.values() for val in vals)
            ]

        if view_mode == "Recent":
            all_projects.sort(key=lambda x: x.last_activity_at, reverse=True)

        return all_projects

    def get_project_overview(self, project_id: str) -> Dict[str, Any]:
        """Gathers dashboard overview data for a single project (Section 4)."""
        project = self.db.get_project(project_id)
        if not project:
            raise ValueError("Project not found")

        tasks = self.db.list_tasks(project_id)
        features = self.db.list_features(project_id)
        bugs = self.db.list_bugs(project_id)
        tech_debts = self.db.list_tech_debt(project_id)
        milestones = self.db.list_milestones(project_id)
        sprints = self.db.list_sprints(project_id)
        checks = self.db.list_checks(project_id, limit=1)
        suggestions = self.db.list_suggestions(project_id, status="pending")
        commits = GitService.get_recent_commits(project.local_path, limit=5)
        sessions = self.db.list_sessions(project_id)
        health = self.pm.calculate_health(project)

        active_tasks = [t for t in tasks if t.status in (TaskStatus.TODO, TaskStatus.IN_PROGRESS)]
        features_in_dev = [f for f in features if f.status in (FeatureStatus.IN_PROGRESS, FeatureStatus.TESTING)]
        open_bugs = [b for b in bugs if b.status != "closed"]
        current_sprint = sprints[0] if sprints else None

        return {
            "project": project,
            "health": health,
            "active_tasks_count": len(active_tasks),
            "features_in_dev_count": len(features_in_dev),
            "open_bugs_count": len(open_bugs),
            "tech_debt_count": len(tech_debts),
            "active_tasks": active_tasks[:6],
            "features_in_dev": features_in_dev[:4],
            "milestones": milestones[:3],
            "current_sprint": current_sprint,
            "recent_commits": commits,
            "recent_sessions": sessions[:3],
            "pending_suggestions": suggestions,
            "latest_check": checks[0] if checks else None,
        }

    # --- Feature & Task Management ---

    def create_feature(
        self,
        project_id: str,
        title: str,
        description: str = "",
        priority: Priority = Priority.MEDIUM,
        requirements: Optional[List[str]] = None,
        acceptance_criteria: Optional[List[str]] = None,
    ) -> Feature:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        feat = Feature(
            id=f"feat_{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            title=title,
            description=description,
            priority=priority,
            requirements=requirements or [],
            acceptance_criteria=acceptance_criteria or [],
            created_at=now,
            updated_at=now,
        )
        self.db.save_feature(feat)
        self.event_bus.emit(ProjectEventType.FEATURE_UPDATED, {"project_id": project_id, "feature_id": feat.id})
        return feat

    def create_task(
        self,
        project_id: str,
        title: str,
        description: str = "",
        feature_id: Optional[str] = None,
        priority: Priority = Priority.MEDIUM,
        task_type: str = "Feature",
        status: TaskStatus = TaskStatus.TODO,
        acceptance_criteria: Optional[List[str]] = None,
        estimated_effort: str = "",
    ) -> Task:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        task = Task(
            id=f"task_{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            feature_id=feature_id,
            title=title,
            description=description,
            priority=priority,
            task_type=task_type,
            status=status,
            acceptance_criteria=acceptance_criteria or [],
            estimated_effort=estimated_effort,
            created_at=now,
            updated_at=now,
        )
        self.db.save_task(task)
        self.event_bus.emit(ProjectEventType.TASK_CREATED, {"project_id": project_id, "task_id": task.id})
        return task

    def update_task_status(self, task_id: str, new_status: TaskStatus) -> None:
        task = self.db.get_task(task_id)
        if not task:
            return
        task.status = new_status
        task.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if new_status == TaskStatus.DONE:
            task.completed_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self.event_bus.emit(ProjectEventType.TASK_COMPLETED, {"project_id": task.project_id, "task_id": task.id})
        self.db.save_task(task)

    def get_kanban_board_data(self, project_id: str) -> Dict[str, List[Task]]:
        """Groups tasks into standard Kanban columns (Section 10)."""
        tasks = self.db.list_tasks(project_id)
        board: Dict[str, List[Task]] = {
            "BACKLOG": [],
            "TODO": [],
            "IN PROGRESS": [],
            "REVIEW": [],
            "TESTING": [],
            "DONE": [],
        }
        status_map = {
            TaskStatus.BACKLOG: "BACKLOG",
            TaskStatus.TODO: "TODO",
            TaskStatus.IN_PROGRESS: "IN PROGRESS",
            TaskStatus.REVIEW: "REVIEW",
            TaskStatus.TESTING: "TESTING",
            TaskStatus.DONE: "DONE",
        }
        for t in tasks:
            col = status_map.get(t.status, "TODO")
            board[col].append(t)
        return board

    # --- Backlog & AI Analysis ---

    def analyze_backlog(self, project_id: str) -> List[str]:
        """Provides AI recommendations for backlog prioritization (Section 11)."""
        tasks = self.db.list_tasks(project_id)
        backlog_tasks = [t for t in tasks if t.status == TaskStatus.BACKLOG]
        features = self.db.list_features(project_id)

        recommendations = []
        if not backlog_tasks:
            return ["Backlog is currently clear. No stalled tasks detected."]

        for t in backlog_tasks:
            # Check if any active feature depends on or mentions this task
            related_feat = next((f for f in features if f.id == t.feature_id), None)
            if related_feat and related_feat.status == FeatureStatus.IN_PROGRESS:
                recommendations.append(
                    f"Task '{t.title}' is in Backlog, but its parent feature '{related_feat.title}' "
                    f"is currently In Progress. Consider prioritizing this task into Sprint/Todo."
                )

        if len(backlog_tasks) > 8:
            recommendations.append(
                f"You have {len(backlog_tasks)} tasks in Backlog. "
                "Consider pruning obsolete items or scheduling a refinement session."
            )

        if not recommendations:
            recommendations.append(f"{len(backlog_tasks)} tasks waiting in backlog. Priorities are balanced.")

        return recommendations

    # --- Suggestions ---

    def resolve_suggestion(self, suggestion_id: str, action: str) -> None:
        """Handles Accept, Modify, Reject, or Ignore on AI suggestions (Section 28)."""
        suggestions = self.db.list_suggestions("")
        # Search across all or by ID
        target = next((s for s in suggestions if s.id == suggestion_id), None)
        if not target:
            return

        if action == "accept":
            target.status = SuggestionStatus.ACCEPTED
            # If create_task, generate task
            if target.suggestion_type in ("create_task", "fix_test"):
                self.create_task(
                    project_id=target.project_id,
                    title=target.title,
                    description=f"{target.description}\n\nEvidence: {target.evidence}",
                    task_type="Testing" if "test" in target.title.lower() else "Feature",
                )
        elif action == "reject":
            target.status = SuggestionStatus.REJECTED
        elif action == "ignore":
            target.status = SuggestionStatus.IGNORED

        self.db.save_suggestion(target)
