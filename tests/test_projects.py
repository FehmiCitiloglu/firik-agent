"""Unit and integration tests for Firik Agent Projects Workspace."""
import tempfile
from pathlib import Path
import pytest

from firik_agent.projects.analyzer import detect_tech_stack, generate_project_template_data
from firik_agent.projects.context_builder import ProjectContextBuilder
from firik_agent.projects.db import ProjectDatabase
from firik_agent.projects.events import ProjectEventBus, ProjectEventType
from firik_agent.projects.git_service import GitService
from firik_agent.projects.indexer import CodebaseIndexer, is_sensitive_file, sanitize_secrets
from firik_agent.projects.models import (
    Feature,
    FeatureStatus,
    Priority,
    Project,
    ProjectInstruction,
    ProjectMemory,
    ProjectSettings,
    Task,
    TaskStatus,
)
from firik_agent.projects.pm_service import AIProjectManager
from firik_agent.projects.service import ProjectService


@pytest.fixture
def temp_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = Path(tmpdir) / "test_projects.db"
        yield ProjectDatabase(db_file)


@pytest.fixture
def temp_workspace():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        # Create dummy python files and package files
        (root / "pyproject.toml").write_text('[project]\nname = "demo-app"\ndependencies = ["fastapi", "pytest"]')
        (root / "README.md").write_text("# Demo App\nWelcome to demo app.")
        (root / "main.py").write_text(
            "import os\n\nclass AuthService:\n    def login(self):\n        # TODO: implement refresh token rotation\n        return True\n"
        )
        (root / "test_main.py").write_text("def test_login():\n    assert True\n")
        (root / ".env").write_text("SECRET_KEY=supersecretkey123456\n")
        yield root


def test_project_crud(temp_db):
    p = Project(
        id="proj_1",
        name="Test Project",
        description="A sample test project",
        local_path="/tmp/test",
        active_branch="main",
    )
    temp_db.create_project(p)

    fetched = temp_db.get_project("proj_1")
    assert fetched is not None
    assert fetched.name == "Test Project"
    assert fetched.active_branch == "main"

    p.name = "Updated Project"
    temp_db.update_project(p)
    updated = temp_db.get_project("proj_1")
    assert updated.name == "Updated Project"

    all_p = temp_db.list_projects()
    assert len(all_p) == 1

    temp_db.delete_project("proj_1")
    assert temp_db.get_project("proj_1") is None


def test_tech_stack_detection(temp_workspace):
    stack = detect_tech_stack(temp_workspace)
    assert "Python" in stack.get("Language", [])
    assert "FastAPI" in stack.get("Backend", [])
    assert "pytest" in stack.get("Testing", [])


def test_secret_detection_and_sanitizer():
    assert is_sensitive_file(".env") is True
    assert is_sensitive_file("id_rsa") is True
    assert is_sensitive_file("server.key") is True
    assert is_sensitive_file("main.py") is False

    raw_text = "API_KEY='ghp_123456789012345678901234567890123456' in connection"
    sanitized = sanitize_secrets(raw_text)
    assert "[SECRET_FILTERED]" in sanitized


def test_indexer_ast_and_todos(temp_workspace):
    indexer = CodebaseIndexer(temp_workspace)
    files = indexer.scan_files()
    rel_files = [str(f.name) for f in files]

    # Sensitive files should NOT be scanned
    assert ".env" not in rel_files
    assert "main.py" in rel_files

    main_p = temp_workspace / "main.py"
    data = indexer.index_file(main_p)
    assert data is not None
    assert "AuthService" in data["classes"]
    assert "login" in data["functions"]
    assert len(data["todos"]) == 1
    assert data["todos"][0]["type"] == "TODO"
    assert "refresh token" in data["todos"][0]["text"]


def test_context_builder(temp_db, temp_workspace):
    p = Project(id="proj_ctx", name="Ctx Proj", local_path=str(temp_workspace))
    s = ProjectSettings(project_id="proj_ctx")
    inst = [ProjectInstruction(id="i1", project_id="proj_ctx", title="Rule 1", content="No any types.")]
    mem = [ProjectMemory(id="m1", project_id="proj_ctx", content="Use Result<T>")]
    feat = Feature(id="f1", project_id="proj_ctx", title="Auth", requirements=["Login"])
    task = Task(id="t1", project_id="proj_ctx", title="Login API", acceptance_criteria=["Return 200"])

    builder = ProjectContextBuilder(
        project=p,
        settings=s,
        instructions=inst,
        memories=mem,
        current_feature=feat,
        current_task=task,
        total_token_budget=2000,
    )

    prompt = builder.assemble_system_prompt("Base system prompt")
    assert "Ctx Proj" in prompt
    assert "Rule 1" in prompt
    assert "Use Result<T>" in prompt
    assert "Login API" in prompt

    report = builder.get_visibility_report()
    assert len(report) >= 5
    assert any(r["name"] == "Instructions" and r["tokens_used"] > 0 for r in report)


def test_ai_project_manager_and_service(temp_workspace):
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = Path(tmpdir) / "service_test.db"
        service = ProjectService(db_file)

        proj = service.create_project(
            name="My Workspace",
            local_path=str(temp_workspace),
            description="Testing AI PM",
            template="Web Application",
        )
        assert proj.id.startswith("proj_")

        # Check that starter template populated features and tasks
        tasks = service.db.list_tasks(proj.id)
        assert len(tasks) >= 2

        features = service.db.list_features(proj.id)
        assert len(features) >= 1

        # Run PM Check
        check = service.pm.run_project_check(proj.id, trigger="unit_test", force=True)
        assert check is not None
        assert "Project check completed" in check.summary

        # Universal search
        results = service.coding.universal_search(proj.id, "AuthService")
        assert len(results["code"]) >= 1

        # Ask Project
        qa = service.coding.ask_project(proj.id, "Where is AuthService implemented?")
        assert len(qa["citations"]) >= 1

        # Start with agent prompt
        agent_setup = service.coding.prepare_task_agent_prompt(proj.id, tasks[0].id)
        assert "task" in agent_setup
        assert agent_setup["task"].title == tasks[0].title
        assert len(agent_setup["system_prompt"]) > 50

        # Backlog analysis
        backlog_recs = service.analyze_backlog(proj.id)
        assert len(backlog_recs) >= 1

        # Clean stop
        service.scheduler.stop()
