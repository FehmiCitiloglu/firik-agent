"""Domain models for Firik Agent Projects Workspace."""
from __future__ import annotations

import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ProjectStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class AutonomyLevel(str, Enum):
    OBSERVE = "observe"
    SUGGEST = "suggest"
    MANAGE = "manage"
    AUTONOMOUS = "autonomous"


class FeatureStatus(str, Enum):
    IDEA = "Idea"
    PLANNED = "Planned"
    READY = "Ready"
    IN_PROGRESS = "In Progress"
    BLOCKED = "Blocked"
    REVIEW = "Review"
    TESTING = "Testing"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"


class TaskStatus(str, Enum):
    BACKLOG = "Backlog"
    TODO = "Todo"
    IN_PROGRESS = "In Progress"
    BLOCKED = "Blocked"
    REVIEW = "Review"
    TESTING = "Testing"
    DONE = "Done"
    CANCELLED = "Cancelled"


class Priority(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


class TaskType(str, Enum):
    FEATURE = "Feature"
    BUG = "Bug"
    REFACTOR = "Refactor"
    TESTING = "Testing"
    DOCUMENTATION = "Documentation"
    RESEARCH = "Research"
    DEVOPS = "DevOps"
    SECURITY = "Security"
    PERFORMANCE = "Performance"


class BugSeverity(str, Enum):
    MINOR = "Minor"
    MAJOR = "Major"
    CRITICAL = "Critical"
    BLOCKER = "Blocker"


class MemoryType(str, Enum):
    ARCHITECTURE = "Architecture"
    CONVENTION = "Convention"
    DECISION = "Decision"
    PREFERENCE = "Preference"
    CONSTRAINT = "Constraint"
    KNOWN_ISSUE = "Known Issue"
    PROJECT_FACT = "Project Fact"


class MemoryStatus(str, Enum):
    CANDIDATE = "candidate"
    APPROVED = "approved"
    PINNED = "pinned"
    REJECTED = "rejected"


class InstructionScope(str, Enum):
    GLOBAL = "Global"
    PROJECT = "Project"
    FOLDER = "Folder"
    FEATURE = "Feature"
    TASK = "Task"
    SESSION = "Session"


class SuggestionStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    IGNORED = "ignored"


class Project(BaseModel):
    id: str
    name: str
    description: str = ""
    local_path: str = ""
    repository_url: str = ""
    tech_stack: Dict[str, List[str]] = Field(default_factory=dict)
    active_branch: str = "main"
    icon: str = "🚀"
    tags: List[str] = Field(default_factory=list)
    status: ProjectStatus = ProjectStatus.ACTIVE
    is_favorite: bool = False
    created_at: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())
    last_activity_at: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())
    last_commit_hash: str = ""
    last_commit_message: str = ""


class ProjectSettings(BaseModel):
    project_id: str
    coding_model: str = "glm-5.3"
    pm_model: str = "glm-5.3"
    review_model: str = "glm-5.3"
    fast_model: str = "glm-5.3"
    autonomy_level: AutonomyLevel = AutonomyLevel.SUGGEST
    pm_check_interval: str = "Every 30 minutes"  # Disabled, 15m, 30m, 1h, 3h, Daily, On Commit, Manual
    permissions: Dict[str, str] = Field(default_factory=lambda: {
        "read_files": "allow",
        "write_files": "ask",
        "delete_files": "ask",
        "terminal": "allow",
        "git_commit": "ask",
        "git_push": "never",
        "install_packages": "ask",
        "database_access": "ask",
        "network_access": "allow",
    })
    token_budget: Dict[str, int] = Field(default_factory=lambda: {
        "instructions": 5,
        "feature_context": 10,
        "memory": 10,
        "code": 50,
        "git": 10,
        "session": 15,
    })
    max_checks_per_hour: int = 10
    max_tokens_per_check: int = 4000
    min_change_threshold: int = 1
    # Coding workspace preferences are extensible and independent of PM settings.
    coding_preferences: Dict[str, Any] = Field(default_factory=dict)


class ProjectInstruction(BaseModel):
    id: str
    project_id: str
    title: str
    category: str = "Coding Rules"  # Coding Rules, Architecture Rules, Testing Rules, UI Rules, Security Rules, Git Rules, Project Goals, Forbidden Actions
    content: str
    scope: InstructionScope = InstructionScope.PROJECT
    priority: int = 100
    is_active: bool = True
    created_at: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())


class PromptTemplate(BaseModel):
    id: str
    project_id: str
    title: str
    category: str = "Development"  # Development, Review, Testing, Security, Architecture, Documentation, DevOps, Release, Project Management
    prompt_text: str
    is_builtin: bool = False
    created_at: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())


class Feature(BaseModel):
    id: str
    project_id: str
    title: str
    description: str = ""
    status: FeatureStatus = FeatureStatus.IDEA
    priority: Priority = Priority.MEDIUM
    owner: str = ""
    requirements: List[str] = Field(default_factory=list)
    acceptance_criteria: List[str] = Field(default_factory=list)
    related_files: List[str] = Field(default_factory=list)
    related_commits: List[str] = Field(default_factory=list)
    related_branches: List[str] = Field(default_factory=list)
    tests: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
    prompts: List[str] = Field(default_factory=list)
    implementation_status: Dict[str, str] = Field(default_factory=lambda: {
        "backend_api": "missing",
        "database_schema": "missing",
        "frontend_ui": "missing",
        "validation": "missing",
        "unit_tests": "missing",
        "integration_tests": "missing",
        "documentation": "missing",
    })
    created_at: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())


class Task(BaseModel):
    id: str
    project_id: str
    feature_id: Optional[str] = None
    milestone_id: Optional[str] = None
    sprint_id: Optional[str] = None
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.TODO
    priority: Priority = Priority.MEDIUM
    task_type: TaskType = TaskType.FEATURE
    assignee: str = ""
    labels: List[str] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)
    subtasks: List[Dict[str, Any]] = Field(default_factory=list)
    acceptance_criteria: List[str] = Field(default_factory=list)
    related_files: List[str] = Field(default_factory=list)
    related_commits: List[str] = Field(default_factory=list)
    estimated_effort: str = ""  # e.g. "3h", "2d", "5pts"
    actual_effort: str = ""
    ai_generated: bool = False
    created_at: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())
    completed_at: Optional[str] = None


class Milestone(BaseModel):
    id: str
    project_id: str
    title: str
    description: str = ""
    target_date: str = ""
    status: str = "open"  # open, closed
    completion_pct: int = 0
    created_at: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())


class Sprint(BaseModel):
    id: str
    project_id: str
    title: str
    start_date: str = ""
    end_date: str = ""
    status: str = "active"  # planned, active, completed
    goals: str = ""
    summary: str = ""
    created_at: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())


class Bug(BaseModel):
    id: str
    project_id: str
    title: str
    description: str = ""
    severity: BugSeverity = BugSeverity.MAJOR
    priority: Priority = Priority.HIGH
    environment: str = ""
    steps_to_reproduce: str = ""
    expected_behavior: str = ""
    actual_behavior: str = ""
    related_files: List[str] = Field(default_factory=list)
    related_logs: str = ""
    related_commits: List[str] = Field(default_factory=list)
    status: str = "open"  # open, in_progress, resolved, closed
    created_at: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())


class TechnicalDebt(BaseModel):
    id: str
    project_id: str
    title: str
    description: str = ""
    category: str = "code_smell"  # code_smell, missing_tests, circular_dependency, duplicate_logic, deprecated_dependency, hardcoded_config, performance
    severity: Priority = Priority.MEDIUM
    suggested_task: str = ""
    status: str = "open"  # open, resolved, ignored
    created_at: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())


class Specification(BaseModel):
    id: str
    project_id: str
    section: str  # vision, target_user, core_features, nfr, performance, security, platforms, constraints
    title: str
    content: str
    updated_at: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())


class ArchitectureDoc(BaseModel):
    id: str
    project_id: str
    section: str  # overview, frontend, backend, database, infrastructure, services, data_flow, security
    title: str
    content: str
    updated_at: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())


class ArchitectureDecision(BaseModel):
    id: str
    project_id: str
    adr_number: str  # ADR-001
    title: str
    context: str = ""
    decision: str = ""
    alternatives: str = ""
    consequences: str = ""
    status: str = "accepted"  # proposed, accepted, superseded, rejected
    date: str = Field(default_factory=lambda: datetime.date.today().isoformat())
    created_at: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())


class ProjectMemory(BaseModel):
    id: str
    project_id: str
    memory_type: MemoryType = MemoryType.CONVENTION
    content: str
    status: MemoryStatus = MemoryStatus.APPROVED
    created_at: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())


class Document(BaseModel):
    id: str
    project_id: str
    doc_type: str = "readme"  # readme, architecture, api, db, setup, deployment, runbook, custom
    title: str
    file_path: str = ""
    content: str = ""
    updated_at: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())


class CodingSession(BaseModel):
    id: str
    project_id: str
    task_id: Optional[str] = None
    feature_id: Optional[str] = None
    title: str = "Coding Session"
    user_request: str = ""
    plan: str = ""
    agent_actions: List[Dict[str, Any]] = Field(default_factory=list)
    files_changed: List[str] = Field(default_factory=list)
    commands_executed: List[str] = Field(default_factory=list)
    tests_executed: List[Dict[str, Any]] = Field(default_factory=list)
    git_diff: str = ""
    result: str = ""
    summary: str = ""
    duration_sec: int = 0
    commit_hash: str = ""
    tokens_used: int = 0
    cost_est: float = 0.0
    created_at: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())
    # id remains the historical record identifier; a new record represents one run.
    # session_id groups multiple explicitly started runs of the same conversation.
    session_id: str = ""
    run_id: str = ""
    provider: str = ""
    model: str = ""
    mode: str = "plan"
    permissions: Dict[str, str | bool] = Field(default_factory=dict)
    status: str = "completed"  # Legacy history contains completed session summaries.
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    completed_steps: List[Dict[str, Any]] = Field(default_factory=list)
    checkpoints: List[Dict[str, Any]] = Field(default_factory=list)
    file_journal: List[Dict[str, Any]] = Field(default_factory=list)
    decisions: List[Dict[str, Any]] = Field(default_factory=list)
    context_summary: str = ""
    context_report: Dict[str, Any] | List[Dict[str, Any]] = Field(default_factory=dict)
    usage: Dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    updated_at: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())
    ended_at: Optional[str] = None
    resumed_from: Optional[str] = None


class CodingRunEvent(BaseModel):
    id: str
    run_id: str
    session_id: str
    project_id: str
    task_id: Optional[str] = None
    sequence: int
    event_type: str
    data: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())


class AISuggestion(BaseModel):
    id: str
    project_id: str
    suggestion_type: str = "create_task"  # create_task, update_feature, update_doc, fix_test, refactor_debt, security_alert
    title: str
    description: str
    evidence: str = ""
    why_it_matters: str = ""
    payload: Dict[str, Any] = Field(default_factory=dict)
    status: SuggestionStatus = SuggestionStatus.PENDING
    created_at: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())


class ProjectCheck(BaseModel):
    id: str
    project_id: str
    trigger: str = "manual"  # manual, periodic, git_commit, git_push, branch_change, task_completed, build_failed, test_failed
    summary: str = ""
    details: Dict[str, Any] = Field(default_factory=dict)
    changes_detected: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    suggestions_count: int = 0
    created_at: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())


class ProjectEvent(BaseModel):
    id: str
    project_id: str
    event_type: str
    actor: str = "user"  # user, ai_pm, coding_agent, system
    title: str
    details: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())


class CodeSymbolIndex(BaseModel):
    id: str
    project_id: str
    file_path: str
    file_type: str
    symbols: List[str] = Field(default_factory=list)
    classes: List[str] = Field(default_factory=list)
    functions: List[str] = Field(default_factory=list)
    imports: List[str] = Field(default_factory=list)
    todos: List[Dict[str, Any]] = Field(default_factory=list)
    file_hash: str = ""
    updated_at: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())


class ProjectSnapshot(BaseModel):
    id: str
    project_id: str
    git_commit: str = ""
    branch: str = ""
    changed_files_count: int = 0
    task_state_hash: str = ""
    feature_state_hash: str = ""
    test_state: str = "passing"
    build_state: str = "healthy"
    created_at: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())


class ProjectHealth(BaseModel):
    build_status: str = "Healthy"
    test_passing_pct: int = 100
    outdated_deps_count: int = 0
    security_warnings_count: int = 0
    doc_status: str = "Up to date"
    tech_debt_count: int = 0
    blocked_tasks_count: int = 0
