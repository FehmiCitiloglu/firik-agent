"""SQLite local-first storage manager for Firik Agent Projects."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from firik_agent.projects.models import (
    AISuggestion,
    ArchitectureDecision,
    ArchitectureDoc,
    Bug,
    CodingSession,
    Document,
    Feature,
    Milestone,
    Project,
    ProjectCheck,
    ProjectEvent,
    ProjectInstruction,
    ProjectMemory,
    ProjectSettings,
    ProjectSnapshot,
    PromptTemplate,
    Specification,
    Sprint,
    Task,
    TechnicalDebt,
)


def get_default_db_path() -> Path:
    base = Path.home() / ".firik"
    base.mkdir(parents=True, exist_ok=True)
    return base / "projects.db"


class ProjectDatabase:
    """Thread-safe SQLite database manager for Projects."""

    def __init__(self, db_path: Optional[str | Path] = None):
        self.db_path = Path(db_path) if db_path else get_default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000;")
        conn.execute("PRAGMA journal_mode=WAL;")
        # Intents must survive process failure before an external dispatch.
        conn.execute("PRAGMA synchronous=FULL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    local_path TEXT,
                    repository_url TEXT,
                    tech_stack TEXT,
                    active_branch TEXT,
                    icon TEXT,
                    tags TEXT,
                    status TEXT DEFAULT 'active',
                    is_favorite INTEGER DEFAULT 0,
                    created_at TEXT,
                    updated_at TEXT,
                    last_activity_at TEXT,
                    last_commit_hash TEXT,
                    last_commit_message TEXT
                );

                CREATE TABLE IF NOT EXISTS project_settings (
                    project_id TEXT PRIMARY KEY,
                    coding_model TEXT,
                    pm_model TEXT,
                    review_model TEXT,
                    fast_model TEXT,
                    autonomy_level TEXT,
                    pm_check_interval TEXT,
                    permissions TEXT,
                    token_budget TEXT,
                    max_checks_per_hour INTEGER,
                    max_tokens_per_check INTEGER,
                    min_change_threshold INTEGER,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS project_instructions (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    title TEXT NOT NULL,
                    category TEXT,
                    content TEXT NOT NULL,
                    scope TEXT,
                    priority INTEGER DEFAULT 100,
                    is_active INTEGER DEFAULT 1,
                    created_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS prompt_templates (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    title TEXT NOT NULL,
                    category TEXT,
                    prompt_text TEXT NOT NULL,
                    is_builtin INTEGER DEFAULT 0,
                    created_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS features (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    title TEXT NOT NULL,
                    description TEXT,
                    status TEXT DEFAULT 'Idea',
                    priority TEXT DEFAULT 'Medium',
                    owner TEXT,
                    requirements TEXT,
                    acceptance_criteria TEXT,
                    related_files TEXT,
                    related_commits TEXT,
                    related_branches TEXT,
                    tests TEXT,
                    notes TEXT,
                    prompts TEXT,
                    implementation_status TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    feature_id TEXT,
                    milestone_id TEXT,
                    sprint_id TEXT,
                    title TEXT NOT NULL,
                    description TEXT,
                    status TEXT DEFAULT 'Todo',
                    priority TEXT DEFAULT 'Medium',
                    task_type TEXT DEFAULT 'Feature',
                    assignee TEXT,
                    labels TEXT,
                    dependencies TEXT,
                    subtasks TEXT,
                    acceptance_criteria TEXT,
                    related_files TEXT,
                    related_commits TEXT,
                    estimated_effort TEXT,
                    actual_effort TEXT,
                    ai_generated INTEGER DEFAULT 0,
                    created_at TEXT,
                    updated_at TEXT,
                    completed_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS milestones (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    title TEXT NOT NULL,
                    description TEXT,
                    target_date TEXT,
                    status TEXT DEFAULT 'open',
                    completion_pct INTEGER DEFAULT 0,
                    created_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS sprints (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    title TEXT NOT NULL,
                    start_date TEXT,
                    end_date TEXT,
                    status TEXT DEFAULT 'active',
                    goals TEXT,
                    summary TEXT,
                    created_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS bugs (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    title TEXT NOT NULL,
                    description TEXT,
                    severity TEXT DEFAULT 'Major',
                    priority TEXT DEFAULT 'High',
                    environment TEXT,
                    steps_to_reproduce TEXT,
                    expected_behavior TEXT,
                    actual_behavior TEXT,
                    related_files TEXT,
                    related_logs TEXT,
                    related_commits TEXT,
                    status TEXT DEFAULT 'open',
                    created_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS technical_debt (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    title TEXT NOT NULL,
                    description TEXT,
                    category TEXT,
                    severity TEXT DEFAULT 'Medium',
                    suggested_task TEXT,
                    status TEXT DEFAULT 'open',
                    created_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS specifications (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    section TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    updated_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS architecture_docs (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    section TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    updated_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS architecture_decisions (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    adr_number TEXT NOT NULL,
                    title TEXT NOT NULL,
                    context TEXT,
                    decision TEXT,
                    alternatives TEXT,
                    consequences TEXT,
                    status TEXT DEFAULT 'accepted',
                    date TEXT,
                    created_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS project_memories (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    memory_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT DEFAULT 'approved',
                    created_at TEXT,
                    updated_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    doc_type TEXT,
                    title TEXT NOT NULL,
                    file_path TEXT,
                    content TEXT,
                    updated_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS coding_sessions (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    task_id TEXT,
                    feature_id TEXT,
                    title TEXT,
                    user_request TEXT,
                    plan TEXT,
                    agent_actions TEXT,
                    files_changed TEXT,
                    commands_executed TEXT,
                    tests_executed TEXT,
                    git_diff TEXT,
                    result TEXT,
                    summary TEXT,
                    duration_sec INTEGER DEFAULT 0,
                    commit_hash TEXT,
                    tokens_used INTEGER DEFAULT 0,
                    cost_est REAL DEFAULT 0.0,
                    created_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS ai_suggestions (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    suggestion_type TEXT,
                    title TEXT NOT NULL,
                    description TEXT,
                    evidence TEXT,
                    why_it_matters TEXT,
                    payload TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS project_checks (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    trigger TEXT,
                    summary TEXT,
                    details TEXT,
                    changes_detected TEXT,
                    risks TEXT,
                    suggestions_count INTEGER DEFAULT 0,
                    created_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS project_events (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    event_type TEXT NOT NULL,
                    actor TEXT DEFAULT 'user',
                    title TEXT NOT NULL,
                    details TEXT,
                    created_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS code_index (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    file_path TEXT NOT NULL,
                    file_type TEXT,
                    symbols TEXT,
                    classes TEXT,
                    functions TEXT,
                    imports TEXT,
                    todos TEXT,
                    file_hash TEXT,
                    updated_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS project_snapshots (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    git_commit TEXT,
                    branch TEXT,
                    changed_files_count INTEGER,
                    task_state_hash TEXT,
                    feature_state_hash TEXT,
                    test_state TEXT,
                    build_state TEXT,
                    created_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
                CREATE INDEX IF NOT EXISTS idx_tasks_feature ON tasks(feature_id);
                CREATE INDEX IF NOT EXISTS idx_features_project ON features(project_id);
                CREATE INDEX IF NOT EXISTS idx_events_project ON project_events(project_id);
                CREATE INDEX IF NOT EXISTS idx_code_index_project ON code_index(project_id);
                CREATE INDEX IF NOT EXISTS idx_code_index_path ON code_index(project_id, file_path);
                """)
                self._migrate_coding_storage(conn)
                conn.commit()
            finally:
                conn.close()

    @staticmethod
    def _migrate_coding_storage(conn: sqlite3.Connection) -> None:
        """Add durable runs without recreating or deleting historical tables."""
        conn.execute("BEGIN IMMEDIATE")
        additions = {
            "session_id": "TEXT NOT NULL DEFAULT ''",
            "run_id": "TEXT NOT NULL DEFAULT ''",
            "provider": "TEXT NOT NULL DEFAULT ''",
            "model": "TEXT NOT NULL DEFAULT ''",
            "mode": "TEXT NOT NULL DEFAULT 'plan'",
            "permissions": "TEXT NOT NULL DEFAULT '{}'",
            "status": "TEXT NOT NULL DEFAULT 'completed'",
            "messages": "TEXT NOT NULL DEFAULT '[]'",
            "completed_steps": "TEXT NOT NULL DEFAULT '[]'",
            "checkpoints": "TEXT NOT NULL DEFAULT '[]'",
            "file_journal": "TEXT NOT NULL DEFAULT '[]'",
            "decisions": "TEXT NOT NULL DEFAULT '[]'",
            "context_summary": "TEXT NOT NULL DEFAULT ''",
            "context_report": "TEXT NOT NULL DEFAULT '{}'",
            "usage": "TEXT NOT NULL DEFAULT '{}'",
            "error": "TEXT NOT NULL DEFAULT ''",
            "updated_at": "TEXT NOT NULL DEFAULT ''",
            "ended_at": "TEXT",
            "resumed_from": "TEXT",
        }
        columns = {row[1] for row in conn.execute("PRAGMA table_info(coding_sessions)")}
        for name, definition in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE coding_sessions ADD COLUMN {name} {definition}")
        settings_columns = {row[1] for row in conn.execute("PRAGMA table_info(project_settings)")}
        if "coding_preferences" not in settings_columns:
            conn.execute("ALTER TABLE project_settings ADD COLUMN coding_preferences TEXT NOT NULL DEFAULT '{}'")
        conn.execute("UPDATE coding_sessions SET session_id=id WHERE session_id=''")
        conn.execute("UPDATE coding_sessions SET run_id=id WHERE run_id=''")
        conn.execute("UPDATE coding_sessions SET updated_at=COALESCE(created_at, '') WHERE updated_at=''")
        schema = """
            CREATE TABLE IF NOT EXISTS coding_run_events (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                task_id TEXT,
                sequence INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                data TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                UNIQUE(run_id, sequence),
                FOREIGN KEY(run_id) REFERENCES coding_sessions(id) ON DELETE CASCADE,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_coding_runs_project ON coding_sessions(project_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_coding_runs_session ON coding_sessions(session_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_coding_events_run ON coding_run_events(run_id, sequence);
            CREATE TABLE IF NOT EXISTS project_schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT OR IGNORE INTO project_schema_migrations(version) VALUES (1);
        """
        for statement in schema.split(";"):
            if statement.strip():
                conn.execute(statement)
        # Additive v2: reuse the owning coding run and its existing event stream.
        # Statements execute in the same migration transaction as v1/backfills.
        durable_schema = """
            CREATE TABLE IF NOT EXISTS coding_execution_steps (
                attempt_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                task_id TEXT,
                step_id TEXT NOT NULL,
                parent_step_id TEXT,
                agent_id TEXT NOT NULL DEFAULT '',
                operation TEXT NOT NULL,
                input_fingerprint TEXT NOT NULL,
                input_summary TEXT NOT NULL DEFAULT '{}',
                side_effect TEXT NOT NULL DEFAULT 'unknown',
                permission_context TEXT NOT NULL DEFAULT '{}',
                workspace_revision TEXT NOT NULL DEFAULT 'null',
                state TEXT NOT NULL,
                retry_safe INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                started_at TEXT,
                terminal_at TEXT,
                updated_at TEXT NOT NULL,
                result_ref TEXT,
                result TEXT NOT NULL DEFAULT 'null',
                error TEXT NOT NULL DEFAULT 'null',
                metadata TEXT NOT NULL DEFAULT '{}',
                version INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(run_id) REFERENCES coding_sessions(id) ON DELETE CASCADE,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                CHECK(state IN ('created','planned','ready','dispatching','running',
                    'completed','failed','cancelled','outcome_unknown','needs_reconciliation'))
            );
            CREATE INDEX IF NOT EXISTS idx_execution_run_state
                ON coding_execution_steps(run_id, state);
            CREATE INDEX IF NOT EXISTS idx_execution_run_step
                ON coding_execution_steps(run_id, step_id);
            CREATE TABLE IF NOT EXISTS coding_run_owners (
                run_id TEXT PRIMARY KEY,
                owner_pid INTEGER NOT NULL,
                owner_host TEXT NOT NULL,
                owner_start TEXT NOT NULL,
                owner_token TEXT NOT NULL,
                claimed_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES coding_sessions(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS coding_resume_claims (
                source_run_id TEXT PRIMARY KEY,
                continuation_run_id TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                FOREIGN KEY(source_run_id) REFERENCES coding_sessions(id) ON DELETE CASCADE,
                FOREIGN KEY(continuation_run_id) REFERENCES coding_sessions(id) ON DELETE CASCADE
            );
            INSERT OR IGNORE INTO project_schema_migrations(version) VALUES (2);
        """
        for statement in durable_schema.split(";"):
            if statement.strip():
                conn.execute(statement)

    # --- Project CRUD ---

    def create_project(self, project: Project) -> Project:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    """
                    INSERT INTO projects (
                        id, name, description, local_path, repository_url, tech_stack,
                        active_branch, icon, tags, status, is_favorite, created_at,
                        updated_at, last_activity_at, last_commit_hash, last_commit_message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project.id, project.name, project.description, project.local_path,
                        project.repository_url, json.dumps(project.tech_stack),
                        project.active_branch, project.icon, json.dumps(project.tags),
                        project.status.value if hasattr(project.status, "value") else str(project.status),
                        1 if project.is_favorite else 0,
                        project.created_at, project.updated_at, project.last_activity_at,
                        project.last_commit_hash, project.last_commit_message,
                    ),
                )
                conn.commit()
                return project
            finally:
                conn.close()

    def get_project(self, project_id: str) -> Optional[Project]:
        conn = self._get_connection()
        try:
            row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            if not row:
                return None
            return self._row_to_project(row)
        finally:
            conn.close()

    def list_projects(self, status: Optional[str] = None) -> List[Project]:
        conn = self._get_connection()
        try:
            if status:
                rows = conn.execute("SELECT * FROM projects WHERE status = ? ORDER BY last_activity_at DESC", (status,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM projects ORDER BY last_activity_at DESC").fetchall()
            return [self._row_to_project(r) for r in rows]
        finally:
            conn.close()

    def update_project(self, project: Project) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    """
                    UPDATE projects SET
                        name = ?, description = ?, local_path = ?, repository_url = ?,
                        tech_stack = ?, active_branch = ?, icon = ?, tags = ?,
                        status = ?, is_favorite = ?, updated_at = ?, last_activity_at = ?,
                        last_commit_hash = ?, last_commit_message = ?
                    WHERE id = ?
                    """,
                    (
                        project.name, project.description, project.local_path,
                        project.repository_url, json.dumps(project.tech_stack),
                        project.active_branch, project.icon, json.dumps(project.tags),
                        project.status.value if hasattr(project.status, "value") else str(project.status),
                        1 if project.is_favorite else 0,
                        project.updated_at, project.last_activity_at,
                        project.last_commit_hash, project.last_commit_message,
                        project.id,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def delete_project(self, project_id: str) -> bool:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
                conn.commit()
                return True
            finally:
                conn.close()

    def _row_to_project(self, r: sqlite3.Row) -> Project:
        return Project(
            id=r["id"],
            name=r["name"],
            description=r["description"] or "",
            local_path=r["local_path"] or "",
            repository_url=r["repository_url"] or "",
            tech_stack=json.loads(r["tech_stack"] or "{}"),
            active_branch=r["active_branch"] or "main",
            icon=r["icon"] or "🚀",
            tags=json.loads(r["tags"] or "[]"),
            status=r["status"] or "active",
            is_favorite=bool(r["is_favorite"]),
            created_at=r["created_at"] or "",
            updated_at=r["updated_at"] or "",
            last_activity_at=r["last_activity_at"] or "",
            last_commit_hash=r["last_commit_hash"] or "",
            last_commit_message=r["last_commit_message"] or "",
        )

    # --- Project Settings ---

    def get_settings(self, project_id: str) -> ProjectSettings:
        conn = self._get_connection()
        try:
            row = conn.execute("SELECT * FROM project_settings WHERE project_id = ?", (project_id,)).fetchone()
            if not row:
                default_settings = ProjectSettings(project_id=project_id)
                self.save_settings(default_settings)
                return default_settings
            return ProjectSettings(
                project_id=row["project_id"],
                coding_model=row["coding_model"] or "glm-5.3",
                pm_model=row["pm_model"] or "glm-5.3",
                review_model=row["review_model"] or "glm-5.3",
                fast_model=row["fast_model"] or "glm-5.3",
                autonomy_level=row["autonomy_level"] or "suggest",
                pm_check_interval=row["pm_check_interval"] or "Every 30 minutes",
                permissions=json.loads(row["permissions"] or "{}"),
                token_budget=json.loads(row["token_budget"] or "{}"),
                max_checks_per_hour=row["max_checks_per_hour"] or 10,
                max_tokens_per_check=row["max_tokens_per_check"] or 4000,
                min_change_threshold=row["min_change_threshold"] or 1,
                coding_preferences=json.loads(row["coding_preferences"] or "{}"),
            )
        finally:
            conn.close()

    def save_settings(self, s: ProjectSettings) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO project_settings (
                        project_id, coding_model, pm_model, review_model, fast_model,
                        autonomy_level, pm_check_interval, permissions, token_budget,
                        max_checks_per_hour, max_tokens_per_check, min_change_threshold, coding_preferences
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        s.project_id, s.coding_model, s.pm_model, s.review_model, s.fast_model,
                        s.autonomy_level.value if hasattr(s.autonomy_level, "value") else str(s.autonomy_level),
                        s.pm_check_interval, json.dumps(s.permissions), json.dumps(s.token_budget),
                        s.max_checks_per_hour, s.max_tokens_per_check, s.min_change_threshold,
                        json.dumps(s.coding_preferences, ensure_ascii=False),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    # --- Instructions ---

    def list_instructions(self, project_id: str) -> List[ProjectInstruction]:
        conn = self._get_connection()
        try:
            rows = conn.execute("SELECT * FROM project_instructions WHERE project_id = ? ORDER BY priority ASC, created_at ASC", (project_id,)).fetchall()
            return [
                ProjectInstruction(
                    id=r["id"], project_id=r["project_id"], title=r["title"], category=r["category"],
                    content=r["content"], scope=r["scope"], priority=r["priority"],
                    is_active=bool(r["is_active"]), created_at=r["created_at"],
                )
                for r in rows
            ]
        finally:
            conn.close()

    def save_instruction(self, inst: ProjectInstruction) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO project_instructions (
                        id, project_id, title, category, content, scope, priority, is_active, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        inst.id, inst.project_id, inst.title, inst.category, inst.content,
                        inst.scope.value if hasattr(inst.scope, "value") else str(inst.scope),
                        inst.priority, 1 if inst.is_active else 0, inst.created_at,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def delete_instruction(self, instruction_id: str) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("DELETE FROM project_instructions WHERE id = ?", (instruction_id,))
                conn.commit()
            finally:
                conn.close()

    # --- Prompts ---

    def list_prompts(self, project_id: str) -> List[PromptTemplate]:
        conn = self._get_connection()
        try:
            rows = conn.execute("SELECT * FROM prompt_templates WHERE project_id = ? ORDER BY category, title", (project_id,)).fetchall()
            return [
                PromptTemplate(
                    id=r["id"], project_id=r["project_id"], title=r["title"], category=r["category"],
                    prompt_text=r["prompt_text"], is_builtin=bool(r["is_builtin"]), created_at=r["created_at"],
                )
                for r in rows
            ]
        finally:
            conn.close()

    def save_prompt(self, p: PromptTemplate) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO prompt_templates (
                        id, project_id, title, category, prompt_text, is_builtin, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (p.id, p.project_id, p.title, p.category, p.prompt_text, 1 if p.is_builtin else 0, p.created_at),
                )
                conn.commit()
            finally:
                conn.close()

    def delete_prompt(self, prompt_id: str) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("DELETE FROM prompt_templates WHERE id = ?", (prompt_id,))
                conn.commit()
            finally:
                conn.close()

    # --- Features ---

    def list_features(self, project_id: str) -> List[Feature]:
        conn = self._get_connection()
        try:
            rows = conn.execute("SELECT * FROM features WHERE project_id = ? ORDER BY created_at DESC", (project_id,)).fetchall()
            return [
                Feature(
                    id=r["id"], project_id=r["project_id"], title=r["title"], description=r["description"] or "",
                    status=r["status"] or "Idea", priority=r["priority"] or "Medium", owner=r["owner"] or "",
                    requirements=json.loads(r["requirements"] or "[]"),
                    acceptance_criteria=json.loads(r["acceptance_criteria"] or "[]"),
                    related_files=json.loads(r["related_files"] or "[]"),
                    related_commits=json.loads(r["related_commits"] or "[]"),
                    related_branches=json.loads(r["related_branches"] or "[]"),
                    tests=json.loads(r["tests"] or "[]"),
                    notes=json.loads(r["notes"] or "[]"),
                    prompts=json.loads(r["prompts"] or "[]"),
                    implementation_status=json.loads(r["implementation_status"] or "{}"),
                    created_at=r["created_at"] or "", updated_at=r["updated_at"] or "",
                )
                for r in rows
            ]
        finally:
            conn.close()

    def get_feature(self, feature_id: str) -> Optional[Feature]:
        conn = self._get_connection()
        try:
            r = conn.execute("SELECT * FROM features WHERE id = ?", (feature_id,)).fetchone()
            if not r:
                return None
            return Feature(
                id=r["id"], project_id=r["project_id"], title=r["title"], description=r["description"] or "",
                status=r["status"] or "Idea", priority=r["priority"] or "Medium", owner=r["owner"] or "",
                requirements=json.loads(r["requirements"] or "[]"),
                acceptance_criteria=json.loads(r["acceptance_criteria"] or "[]"),
                related_files=json.loads(r["related_files"] or "[]"),
                related_commits=json.loads(r["related_commits"] or "[]"),
                related_branches=json.loads(r["related_branches"] or "[]"),
                tests=json.loads(r["tests"] or "[]"),
                notes=json.loads(r["notes"] or "[]"),
                prompts=json.loads(r["prompts"] or "[]"),
                implementation_status=json.loads(r["implementation_status"] or "{}"),
                created_at=r["created_at"] or "", updated_at=r["updated_at"] or "",
            )
        finally:
            conn.close()

    def save_feature(self, f: Feature) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO features (
                        id, project_id, title, description, status, priority, owner,
                        requirements, acceptance_criteria, related_files, related_commits,
                        related_branches, tests, notes, prompts, implementation_status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f.id, f.project_id, f.title, f.description,
                        f.status.value if hasattr(f.status, "value") else str(f.status),
                        f.priority.value if hasattr(f.priority, "value") else str(f.priority),
                        f.owner, json.dumps(f.requirements), json.dumps(f.acceptance_criteria),
                        json.dumps(f.related_files), json.dumps(f.related_commits),
                        json.dumps(f.related_branches), json.dumps(f.tests),
                        json.dumps(f.notes), json.dumps(f.prompts),
                        json.dumps(f.implementation_status), f.created_at, f.updated_at,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def delete_feature(self, feature_id: str) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("DELETE FROM features WHERE id = ?", (feature_id,))
                conn.commit()
            finally:
                conn.close()

    # --- Tasks ---

    def list_tasks(self, project_id: str, feature_id: Optional[str] = None) -> List[Task]:
        conn = self._get_connection()
        try:
            if feature_id:
                rows = conn.execute("SELECT * FROM tasks WHERE project_id = ? AND feature_id = ? ORDER BY created_at DESC", (project_id, feature_id)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM tasks WHERE project_id = ? ORDER BY created_at DESC", (project_id,)).fetchall()
            return [
                Task(
                    id=r["id"], project_id=r["project_id"], feature_id=r["feature_id"],
                    milestone_id=r["milestone_id"], sprint_id=r["sprint_id"],
                    title=r["title"], description=r["description"] or "",
                    status=r["status"] or "Todo", priority=r["priority"] or "Medium",
                    task_type=r["task_type"] or "Feature", assignee=r["assignee"] or "",
                    labels=json.loads(r["labels"] or "[]"),
                    dependencies=json.loads(r["dependencies"] or "[]"),
                    subtasks=json.loads(r["subtasks"] or "[]"),
                    acceptance_criteria=json.loads(r["acceptance_criteria"] or "[]"),
                    related_files=json.loads(r["related_files"] or "[]"),
                    related_commits=json.loads(r["related_commits"] or "[]"),
                    estimated_effort=r["estimated_effort"] or "",
                    actual_effort=r["actual_effort"] or "",
                    ai_generated=bool(r["ai_generated"]),
                    created_at=r["created_at"] or "", updated_at=r["updated_at"] or "",
                    completed_at=r["completed_at"],
                )
                for r in rows
            ]
        finally:
            conn.close()

    def get_task(self, task_id: str) -> Optional[Task]:
        conn = self._get_connection()
        try:
            r = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not r:
                return None
            return Task(
                id=r["id"], project_id=r["project_id"], feature_id=r["feature_id"],
                milestone_id=r["milestone_id"], sprint_id=r["sprint_id"],
                title=r["title"], description=r["description"] or "",
                status=r["status"] or "Todo", priority=r["priority"] or "Medium",
                task_type=r["task_type"] or "Feature", assignee=r["assignee"] or "",
                labels=json.loads(r["labels"] or "[]"),
                dependencies=json.loads(r["dependencies"] or "[]"),
                subtasks=json.loads(r["subtasks"] or "[]"),
                acceptance_criteria=json.loads(r["acceptance_criteria"] or "[]"),
                related_files=json.loads(r["related_files"] or "[]"),
                related_commits=json.loads(r["related_commits"] or "[]"),
                estimated_effort=r["estimated_effort"] or "",
                actual_effort=r["actual_effort"] or "",
                ai_generated=bool(r["ai_generated"]),
                created_at=r["created_at"] or "", updated_at=r["updated_at"] or "",
                completed_at=r["completed_at"],
            )
        finally:
            conn.close()

    def save_task(self, t: Task) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO tasks (
                        id, project_id, feature_id, milestone_id, sprint_id, title, description,
                        status, priority, task_type, assignee, labels, dependencies, subtasks,
                        acceptance_criteria, related_files, related_commits, estimated_effort,
                        actual_effort, ai_generated, created_at, updated_at, completed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        t.id, t.project_id, t.feature_id, t.milestone_id, t.sprint_id,
                        t.title, t.description,
                        t.status.value if hasattr(t.status, "value") else str(t.status),
                        t.priority.value if hasattr(t.priority, "value") else str(t.priority),
                        t.task_type.value if hasattr(t.task_type, "value") else str(t.task_type),
                        t.assignee, json.dumps(t.labels), json.dumps(t.dependencies),
                        json.dumps(t.subtasks), json.dumps(t.acceptance_criteria),
                        json.dumps(t.related_files), json.dumps(t.related_commits),
                        t.estimated_effort, t.actual_effort, 1 if t.ai_generated else 0,
                        t.created_at, t.updated_at, t.completed_at,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def delete_task(self, task_id: str) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
                conn.commit()
            finally:
                conn.close()

    # --- Milestones & Sprints ---

    def list_milestones(self, project_id: str) -> List[Milestone]:
        conn = self._get_connection()
        try:
            rows = conn.execute("SELECT * FROM milestones WHERE project_id = ? ORDER BY target_date ASC", (project_id,)).fetchall()
            return [
                Milestone(
                    id=r["id"], project_id=r["project_id"], title=r["title"], description=r["description"] or "",
                    target_date=r["target_date"] or "", status=r["status"] or "open",
                    completion_pct=r["completion_pct"] or 0, created_at=r["created_at"] or "",
                )
                for r in rows
            ]
        finally:
            conn.close()

    def save_milestone(self, m: Milestone) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO milestones (
                        id, project_id, title, description, target_date, status, completion_pct, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (m.id, m.project_id, m.title, m.description, m.target_date, m.status, m.completion_pct, m.created_at),
                )
                conn.commit()
            finally:
                conn.close()

    def list_sprints(self, project_id: str) -> List[Sprint]:
        conn = self._get_connection()
        try:
            rows = conn.execute("SELECT * FROM sprints WHERE project_id = ? ORDER BY start_date DESC", (project_id,)).fetchall()
            return [
                Sprint(
                    id=r["id"], project_id=r["project_id"], title=r["title"], start_date=r["start_date"] or "",
                    end_date=r["end_date"] or "", status=r["status"] or "active", goals=r["goals"] or "",
                    summary=r["summary"] or "", created_at=r["created_at"] or "",
                )
                for r in rows
            ]
        finally:
            conn.close()

    def save_sprint(self, s: Sprint) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO sprints (
                        id, project_id, title, start_date, end_date, status, goals, summary, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (s.id, s.project_id, s.title, s.start_date, s.end_date, s.status, s.goals, s.summary, s.created_at),
                )
                conn.commit()
            finally:
                conn.close()

    # --- Bugs & Technical Debt ---

    def list_bugs(self, project_id: str) -> List[Bug]:
        conn = self._get_connection()
        try:
            rows = conn.execute("SELECT * FROM bugs WHERE project_id = ? ORDER BY created_at DESC", (project_id,)).fetchall()
            return [
                Bug(
                    id=r["id"], project_id=r["project_id"], title=r["title"], description=r["description"] or "",
                    severity=r["severity"] or "Major", priority=r["priority"] or "High",
                    environment=r["environment"] or "", steps_to_reproduce=r["steps_to_reproduce"] or "",
                    expected_behavior=r["expected_behavior"] or "", actual_behavior=r["actual_behavior"] or "",
                    related_files=json.loads(r["related_files"] or "[]"), related_logs=r["related_logs"] or "",
                    related_commits=json.loads(r["related_commits"] or "[]"), status=r["status"] or "open",
                    created_at=r["created_at"] or "",
                )
                for r in rows
            ]
        finally:
            conn.close()

    def save_bug(self, b: Bug) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO bugs (
                        id, project_id, title, description, severity, priority, environment,
                        steps_to_reproduce, expected_behavior, actual_behavior, related_files,
                        related_logs, related_commits, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        b.id, b.project_id, b.title, b.description,
                        b.severity.value if hasattr(b.severity, "value") else str(b.severity),
                        b.priority.value if hasattr(b.priority, "value") else str(b.priority),
                        b.environment, b.steps_to_reproduce, b.expected_behavior, b.actual_behavior,
                        json.dumps(b.related_files), b.related_logs, json.dumps(b.related_commits),
                        b.status, b.created_at,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def list_tech_debt(self, project_id: str) -> List[TechnicalDebt]:
        conn = self._get_connection()
        try:
            rows = conn.execute("SELECT * FROM technical_debt WHERE project_id = ? ORDER BY created_at DESC", (project_id,)).fetchall()
            return [
                TechnicalDebt(
                    id=r["id"], project_id=r["project_id"], title=r["title"], description=r["description"] or "",
                    category=r["category"] or "code_smell", severity=r["severity"] or "Medium",
                    suggested_task=r["suggested_task"] or "", status=r["status"] or "open",
                    created_at=r["created_at"] or "",
                )
                for r in rows
            ]
        finally:
            conn.close()

    def save_tech_debt(self, td: TechnicalDebt) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO technical_debt (
                        id, project_id, title, description, category, severity, suggested_task, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        td.id, td.project_id, td.title, td.description, td.category,
                        td.severity.value if hasattr(td.severity, "value") else str(td.severity),
                        td.suggested_task, td.status, td.created_at,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    # --- Specifications, Architecture, ADRs ---

    def list_specs(self, project_id: str) -> List[Specification]:
        conn = self._get_connection()
        try:
            rows = conn.execute("SELECT * FROM specifications WHERE project_id = ? ORDER BY section", (project_id,)).fetchall()
            return [
                Specification(
                    id=r["id"], project_id=r["project_id"], section=r["section"],
                    title=r["title"], content=r["content"], updated_at=r["updated_at"] or "",
                )
                for r in rows
            ]
        finally:
            conn.close()

    def save_spec(self, s: Specification) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO specifications (id, project_id, section, title, content, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (s.id, s.project_id, s.section, s.title, s.content, s.updated_at),
                )
                conn.commit()
            finally:
                conn.close()

    def list_arch_docs(self, project_id: str) -> List[ArchitectureDoc]:
        conn = self._get_connection()
        try:
            rows = conn.execute("SELECT * FROM architecture_docs WHERE project_id = ? ORDER BY section", (project_id,)).fetchall()
            return [
                ArchitectureDoc(
                    id=r["id"], project_id=r["project_id"], section=r["section"],
                    title=r["title"], content=r["content"], updated_at=r["updated_at"] or "",
                )
                for r in rows
            ]
        finally:
            conn.close()

    def save_arch_doc(self, a: ArchitectureDoc) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO architecture_docs (id, project_id, section, title, content, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (a.id, a.project_id, a.section, a.title, a.content, a.updated_at),
                )
                conn.commit()
            finally:
                conn.close()

    def list_adrs(self, project_id: str) -> List[ArchitectureDecision]:
        conn = self._get_connection()
        try:
            rows = conn.execute("SELECT * FROM architecture_decisions WHERE project_id = ? ORDER BY adr_number ASC", (project_id,)).fetchall()
            return [
                ArchitectureDecision(
                    id=r["id"], project_id=r["project_id"], adr_number=r["adr_number"],
                    title=r["title"], context=r["context"] or "", decision=r["decision"] or "",
                    alternatives=r["alternatives"] or "", consequences=r["consequences"] or "",
                    status=r["status"] or "accepted", date=r["date"] or "", created_at=r["created_at"] or "",
                )
                for r in rows
            ]
        finally:
            conn.close()

    def save_adr(self, adr: ArchitectureDecision) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO architecture_decisions (
                        id, project_id, adr_number, title, context, decision, alternatives, consequences, status, date, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        adr.id, adr.project_id, adr.adr_number, adr.title, adr.context, adr.decision,
                        adr.alternatives, adr.consequences, adr.status, adr.date, adr.created_at,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    # --- Project Memory ---

    def list_memories(self, project_id: str, status: Optional[str] = None) -> List[ProjectMemory]:
        conn = self._get_connection()
        try:
            if status:
                rows = conn.execute("SELECT * FROM project_memories WHERE project_id = ? AND status = ? ORDER BY created_at DESC", (project_id, status)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM project_memories WHERE project_id = ? ORDER BY created_at DESC", (project_id,)).fetchall()
            return [
                ProjectMemory(
                    id=r["id"], project_id=r["project_id"], memory_type=r["memory_type"],
                    content=r["content"], status=r["status"] or "approved",
                    created_at=r["created_at"] or "", updated_at=r["updated_at"] or "",
                )
                for r in rows
            ]
        finally:
            conn.close()

    def save_memory(self, m: ProjectMemory) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO project_memories (id, project_id, memory_type, content, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        m.id, m.project_id,
                        m.memory_type.value if hasattr(m.memory_type, "value") else str(m.memory_type),
                        m.content,
                        m.status.value if hasattr(m.status, "value") else str(m.status),
                        m.created_at, m.updated_at,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def delete_memory(self, memory_id: str) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("DELETE FROM project_memories WHERE id = ?", (memory_id,))
                conn.commit()
            finally:
                conn.close()

    # --- Documents ---

    def list_documents(self, project_id: str) -> List[Document]:
        conn = self._get_connection()
        try:
            rows = conn.execute("SELECT * FROM documents WHERE project_id = ? ORDER BY doc_type, title", (project_id,)).fetchall()
            return [
                Document(
                    id=r["id"], project_id=r["project_id"], doc_type=r["doc_type"] or "readme",
                    title=r["title"], file_path=r["file_path"] or "", content=r["content"] or "",
                    updated_at=r["updated_at"] or "",
                )
                for r in rows
            ]
        finally:
            conn.close()

    def save_document(self, d: Document) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO documents (id, project_id, doc_type, title, file_path, content, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (d.id, d.project_id, d.doc_type, d.title, d.file_path, d.content, d.updated_at),
                )
                conn.commit()
            finally:
                conn.close()

    # --- Coding Sessions ---

    def list_sessions(self, project_id: str) -> List[CodingSession]:
        conn = self._get_connection()
        try:
            rows = conn.execute("SELECT * FROM coding_sessions WHERE project_id = ? ORDER BY created_at DESC", (project_id,)).fetchall()
            return [self._row_to_session(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> CodingSession:
        data = dict(row)
        json_defaults = {
            "agent_actions": [], "files_changed": [], "commands_executed": [],
            "tests_executed": [], "permissions": {}, "messages": [],
            "completed_steps": [], "checkpoints": [], "file_journal": [],
            "decisions": [], "context_report": {}, "usage": {},
        }
        for name, default in json_defaults.items():
            data[name] = json.loads(data.get(name) or json.dumps(default))
        # Very old records allow NULL in summary fields; retain their old defaults.
        for name in CodingSession.model_fields:
            if data.get(name) is None and name not in {"task_id", "feature_id", "ended_at", "resumed_from"}:
                data.pop(name, None)
        data["session_id"] = data.get("session_id") or data["id"]
        data["run_id"] = data.get("run_id") or data["id"]
        return CodingSession.model_validate(data)

    @staticmethod
    def _write_session(conn: sqlite3.Connection, session: CodingSession) -> None:
        from firik_agent.projects.coding_sessions import redact_sensitive

        data = redact_sensitive(session.model_dump(mode="json"))
        data["session_id"] = session.session_id or session.id
        data["run_id"] = session.run_id or session.id
        if data["run_id"] != session.id:
            raise ValueError("Çalışma kimliği oturum kaydının kimliğiyle uyuşmuyor.")
        existing = conn.execute("SELECT project_id, task_id, session_id FROM coding_sessions WHERE id=?", (session.id,)).fetchone()
        if existing and (existing["project_id"] != session.project_id or existing["task_id"] != session.task_id
                         or existing["session_id"] != data["session_id"]):
            raise ValueError("Bir çalışmanın proje, görev veya oturum kimliği değiştirilemez.")
        task = conn.execute("SELECT project_id FROM tasks WHERE id=?", (session.task_id,)).fetchone() if session.task_id else None
        if session.task_id and (task is None or task["project_id"] != session.project_id):
            raise ValueError("Görev bu projeye ait değil.")
        # UPSERT retains child event rows, unlike the historical INSERT OR REPLACE.
        columns = list(CodingSession.model_fields)
        values = [json.dumps(data[name], ensure_ascii=False) if isinstance(data[name], (dict, list)) else data[name]
                  for name in columns]
        updates = ", ".join(f"{name}=excluded.{name}" for name in columns if name != "id")
        conn.execute(
            f"INSERT INTO coding_sessions ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}", values,
        )

    def save_session(self, cs: CodingSession) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE")
                self._write_session(conn, cs)
                conn.commit()
            finally:
                conn.close()

    # --- AI Suggestions ---

    def list_suggestions(self, project_id: str, status: Optional[str] = None) -> List[AISuggestion]:
        conn = self._get_connection()
        try:
            if status:
                rows = conn.execute("SELECT * FROM ai_suggestions WHERE project_id = ? AND status = ? ORDER BY created_at DESC", (project_id, status)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM ai_suggestions WHERE project_id = ? ORDER BY created_at DESC", (project_id,)).fetchall()
            return [
                AISuggestion(
                    id=r["id"], project_id=r["project_id"], suggestion_type=r["suggestion_type"] or "create_task",
                    title=r["title"], description=r["description"] or "", evidence=r["evidence"] or "",
                    why_it_matters=r["why_it_matters"] or "", payload=json.loads(r["payload"] or "{}"),
                    status=r["status"] or "pending", created_at=r["created_at"] or "",
                )
                for r in rows
            ]
        finally:
            conn.close()

    def save_suggestion(self, s: AISuggestion) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO ai_suggestions (
                        id, project_id, suggestion_type, title, description, evidence, why_it_matters, payload, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        s.id, s.project_id, s.suggestion_type, s.title, s.description,
                        s.evidence, s.why_it_matters, json.dumps(s.payload),
                        s.status.value if hasattr(s.status, "value") else str(s.status),
                        s.created_at,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    # --- Project Checks & Events ---

    def list_checks(self, project_id: str, limit: int = 20) -> List[ProjectCheck]:
        conn = self._get_connection()
        try:
            rows = conn.execute("SELECT * FROM project_checks WHERE project_id = ? ORDER BY created_at DESC LIMIT ?", (project_id, limit)).fetchall()
            return [
                ProjectCheck(
                    id=r["id"], project_id=r["project_id"], trigger=r["trigger"] or "manual",
                    summary=r["summary"] or "", details=json.loads(r["details"] or "{}"),
                    changes_detected=json.loads(r["changes_detected"] or "[]"),
                    risks=json.loads(r["risks"] or "[]"),
                    suggestions_count=r["suggestions_count"] or 0, created_at=r["created_at"] or "",
                )
                for r in rows
            ]
        finally:
            conn.close()

    def save_check(self, c: ProjectCheck) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO project_checks (
                        id, project_id, trigger, summary, details, changes_detected, risks, suggestions_count, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        c.id, c.project_id, c.trigger, c.summary, json.dumps(c.details),
                        json.dumps(c.changes_detected), json.dumps(c.risks), c.suggestions_count, c.created_at,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def list_events(self, project_id: str, limit: int = 50) -> List[ProjectEvent]:
        conn = self._get_connection()
        try:
            rows = conn.execute("SELECT * FROM project_events WHERE project_id = ? ORDER BY created_at DESC LIMIT ?", (project_id, limit)).fetchall()
            return [
                ProjectEvent(
                    id=r["id"], project_id=r["project_id"], event_type=r["event_type"],
                    actor=r["actor"] or "user", title=r["title"], details=json.loads(r["details"] or "{}"),
                    created_at=r["created_at"] or "",
                )
                for r in rows
            ]
        finally:
            conn.close()

    def log_event(self, ev: ProjectEvent) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    "INSERT INTO project_events (id, project_id, event_type, actor, title, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (ev.id, ev.project_id, ev.event_type, ev.actor, ev.title, json.dumps(ev.details), ev.created_at),
                )
                conn.commit()
            finally:
                conn.close()

    # --- Code Index & Snapshots ---

    def save_code_index(self, project_id: str, file_path: str, file_type: str, symbols: List[str], classes: List[str], functions: List[str], imports: List[str], todos: List[Dict[str, Any]], file_hash: str) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                import uuid
                import datetime
                cid = f"idx_{uuid.uuid4().hex[:12]}"
                now = datetime.datetime.now(datetime.timezone.utc).isoformat()
                conn.execute(
                    """
                    INSERT OR REPLACE INTO code_index (
                        id, project_id, file_path, file_type, symbols, classes, functions, imports, todos, file_hash, updated_at
                    ) VALUES (
                        COALESCE((SELECT id FROM code_index WHERE project_id = ? AND file_path = ?), ?),
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        project_id, file_path, cid, project_id, file_path, file_type,
                        json.dumps(symbols), json.dumps(classes), json.dumps(functions),
                        json.dumps(imports), json.dumps(todos), file_hash, now,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def delete_code_index(self, project_id: str, file_path: str) -> None:
        """Remove one stale metadata entry without touching any repository file."""
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("DELETE FROM code_index WHERE project_id = ? AND file_path = ?", (project_id, file_path))
                conn.commit()
            finally:
                conn.close()

    def get_all_code_indexes(self, project_id: str) -> List[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            rows = conn.execute("SELECT * FROM code_index WHERE project_id = ?", (project_id,)).fetchall()
            return [
                {
                    "file_path": r["file_path"],
                    "file_type": r["file_type"],
                    "symbols": json.loads(r["symbols"] or "[]"),
                    "classes": json.loads(r["classes"] or "[]"),
                    "functions": json.loads(r["functions"] or "[]"),
                    "imports": json.loads(r["imports"] or "[]"),
                    "todos": json.loads(r["todos"] or "[]"),
                    "file_hash": r["file_hash"],
                    "updated_at": r["updated_at"],
                }
                for r in rows
            ]
        finally:
            conn.close()

    def save_snapshot(self, s: ProjectSnapshot) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    """
                    INSERT INTO project_snapshots (
                        id, project_id, git_commit, branch, changed_files_count,
                        task_state_hash, feature_state_hash, test_state, build_state, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        s.id, s.project_id, s.git_commit, s.branch, s.changed_files_count,
                        s.task_state_hash, s.feature_state_hash, s.test_state, s.build_state, s.created_at,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def get_latest_snapshot(self, project_id: str) -> Optional[ProjectSnapshot]:
        conn = self._get_connection()
        try:
            r = conn.execute("SELECT * FROM project_snapshots WHERE project_id = ? ORDER BY created_at DESC LIMIT 1", (project_id,)).fetchone()
            if not r:
                return None
            return ProjectSnapshot(
                id=r["id"], project_id=r["project_id"], git_commit=r["git_commit"] or "",
                branch=r["branch"] or "", changed_files_count=r["changed_files_count"] or 0,
                task_state_hash=r["task_state_hash"] or "", feature_state_hash=r["feature_state_hash"] or "",
                test_state=r["test_state"] or "passing", build_state=r["build_state"] or "healthy",
                created_at=r["created_at"] or "",
            )
        finally:
            conn.close()
