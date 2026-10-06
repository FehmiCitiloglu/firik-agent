"""Codebase technology stack analyzer and project template generator."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid
import datetime

from firik_agent.projects.models import (
    ArchitectureDoc,
    Feature,
    FeatureStatus,
    Priority,
    ProjectInstruction,
    PromptTemplate,
    Specification,
    Task,
    TaskStatus,
    TaskType,
)


def detect_tech_stack(project_path: str | Path) -> Dict[str, List[str]]:
    """Inspects project files to automatically infer the technologies, libraries, and frameworks."""
    root = Path(project_path)
    if not root.exists() or not root.is_dir():
        return {}

    stack: Dict[str, List[str]] = {
        "Frontend": [],
        "Backend": [],
        "Database": [],
        "Infrastructure": [],
        "Testing": [],
        "Language": [],
    }

    # Helper to read file safely
    def read_text(name: str) -> str:
        f = root / name
        if f.exists() and f.is_file():
            try:
                return f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                return ""
        return ""

    # Node.js / JavaScript / TypeScript
    pkg_json_text = read_text("package.json")
    if pkg_json_text:
        try:
            pkg = json.loads(pkg_json_text)
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            if (root / "tsconfig.json").exists() or "typescript" in deps:
                stack["Language"].append("TypeScript")
            else:
                stack["Language"].append("JavaScript")

            if "next" in deps:
                stack["Frontend"].append("Next.js")
            if "react" in deps:
                stack["Frontend"].append("React")
            if "vue" in deps:
                stack["Frontend"].append("Vue.js")
            if "svelte" in deps or "@sveltejs/kit" in deps:
                stack["Frontend"].append("Svelte")
            if "tailwindcss" in deps:
                stack["Frontend"].append("Tailwind CSS")
            if "vite" in deps:
                stack["Frontend"].append("Vite")

            if "express" in deps:
                stack["Backend"].append("Express")
            if "nestjs" in deps or "@nestjs/core" in deps:
                stack["Backend"].append("NestJS")
            if "fastify" in deps:
                stack["Backend"].append("Fastify")
            if not stack["Backend"] and not stack["Frontend"]:
                stack["Backend"].append("Node.js")

            if "prisma" in deps or "@prisma/client" in deps:
                stack["Database"].append("Prisma ORM")
            if "typeorm" in deps:
                stack["Database"].append("TypeORM")
            if "mongoose" in deps:
                stack["Database"].append("MongoDB")
            if "pg" in deps:
                stack["Database"].append("PostgreSQL")

            if "jest" in deps:
                stack["Testing"].append("Jest")
            if "vitest" in deps:
                stack["Testing"].append("Vitest")
            if "playwright" in deps:
                stack["Testing"].append("Playwright")
        except Exception:
            stack["Language"].append("JavaScript")

    # Python
    pyproject = read_text("pyproject.toml")
    reqs = read_text("requirements.txt")
    setup_py = read_text("setup.py")
    py_content = (pyproject + "\n" + reqs + "\n" + setup_py).lower()

    if py_content.strip() or any(root.glob("*.py")):
        if "Python" not in stack["Language"]:
            stack["Language"].append("Python")

        if "fastapi" in py_content:
            stack["Backend"].append("FastAPI")
        if "django" in py_content:
            stack["Backend"].append("Django")
        if "flask" in py_content:
            stack["Backend"].append("Flask")
        if "customtkinter" in py_content:
            stack["Frontend"].append("CustomTkinter (GUI)")

        if "sqlalchemy" in py_content:
            stack["Database"].append("SQLAlchemy")
        if "psycopg2" in py_content or "asyncpg" in py_content:
            stack["Database"].append("PostgreSQL")
        if "sqlite3" in py_content or any(root.glob("*.db")):
            stack["Database"].append("SQLite")

        if "pytest" in py_content or (root / "pytest.ini").exists():
            stack["Testing"].append("pytest")
        if "unittest" in py_content:
            stack["Testing"].append("unittest")

    # Rust
    cargo = read_text("Cargo.toml")
    if cargo:
        stack["Language"].append("Rust")
        if "actix" in cargo:
            stack["Backend"].append("Actix-web")
        if "axum" in cargo:
            stack["Backend"].append("Axum")
        if "tokio" in cargo:
            stack["Backend"].append("Tokio Async")
        stack["Testing"].append("cargo test")

    # Go
    go_mod = read_text("go.mod")
    if go_mod:
        stack["Language"].append("Go")
        if "gin-gonic/gin" in go_mod:
            stack["Backend"].append("Gin")
        if "fiber" in go_mod:
            stack["Backend"].append("Fiber")
        stack["Testing"].append("go test")

    # Java / Kotlin
    if (root / "pom.xml").exists() or (root / "build.gradle").exists() or (root / "build.gradle.kts").exists():
        stack["Language"].append("Java/Kotlin")
        pom = read_text("pom.xml") + read_text("build.gradle")
        if "spring-boot" in pom.lower():
            stack["Backend"].append("Spring Boot")
        stack["Testing"].append("JUnit")

    # Infrastructure
    if (root / "Dockerfile").exists():
        stack["Infrastructure"].append("Docker")
    if (root / "docker-compose.yml").exists() or (root / "docker-compose.yaml").exists():
        stack["Infrastructure"].append("Docker Compose")
    if (root / ".github" / "workflows").exists():
        stack["Infrastructure"].append("GitHub Actions")
    if (root / ".gitlab-ci.yml").exists():
        stack["Infrastructure"].append("GitLab CI")
    if (root / "Makefile").exists():
        stack["Infrastructure"].append("Makefile")
    if (root / "CMakeLists.txt").exists():
        stack["Infrastructure"].append("CMake")

    # Deduplicate and remove empty categories
    cleaned_stack = {}
    for cat, items in stack.items():
        deduped = list(dict.fromkeys(items))
        if deduped:
            cleaned_stack[cat] = deduped

    return cleaned_stack


def generate_project_template_data(project_id: str, template_name: str) -> Dict[str, Any]:
    """Generates starter instructions, prompt templates, specs, architecture, and tasks based on a template."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    template = template_name.lower()

    instructions: List[ProjectInstruction] = [
        ProjectInstruction(
            id=f"inst_{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            title="TypeScript / Python Strictness",
            category="Coding Rules",
            content="Enforce strict typing and error handling. Do not use 'any' or untyped returns.",
            priority=10,
        ),
        ProjectInstruction(
            id=f"inst_{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            title="Test Coverage Rule",
            category="Testing Rules",
            content="Every newly implemented feature or bugfix must be accompanied by unit or integration tests.",
            priority=20,
        ),
        ProjectInstruction(
            id=f"inst_{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            title="Database Safety Rule",
            category="Forbidden Actions",
            content="Never drop tables or run destructive migrations in production without explicit confirmation.",
            priority=5,
        ),
    ]

    prompts: List[PromptTemplate] = [
        PromptTemplate(
            id=f"pr_{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            title="Review Architecture & Clean Code",
            category="Review",
            prompt_text="Analyze the current architecture against clean code and domain-driven design principles. Identify circular dependencies and large classes.",
            is_builtin=True,
        ),
        PromptTemplate(
            id=f"pr_{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            title="Security & Dependency Audit",
            category="Security",
            prompt_text="Inspect project dependencies and input handlers for potential injection or security vulnerabilities.",
            is_builtin=True,
        ),
        PromptTemplate(
            id=f"pr_{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            title="Find Technical Debt & Code Smells",
            category="Architecture",
            prompt_text="Search the codebase for code smells, TODOs, missing tests, and hard-coded configurations. Suggest structured tasks to resolve them.",
            is_builtin=True,
        ),
        PromptTemplate(
            id=f"pr_{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            title="Prepare Release Notes & Changelog",
            category="Release",
            prompt_text="Review the recent commits and closed tasks. Generate clean release notes with new features, bug fixes, and breaking changes.",
            is_builtin=True,
        ),
    ]

    specs: List[Specification] = [
        Specification(
            id=f"spec_{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            section="vision",
            title="Product Vision",
            content="Build a high performance, reliable software product with modern developer experience.",
        ),
        Specification(
            id=f"spec_{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            section="target_user",
            title="Target User",
            content="Developers, power users, and engineering teams requiring robust automation.",
        ),
        Specification(
            id=f"spec_{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            section="nfr",
            title="Non-Functional Requirements",
            content="- Sub-100ms response time for local operations.\n- 100% offline-first capability.\n- Secure credential storage.",
        ),
    ]

    arch_docs: List[ArchitectureDoc] = [
        ArchitectureDoc(
            id=f"arch_{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            section="overview",
            title="System Architecture Overview",
            content="Layered architecture with distinct Domain, Infrastructure, and UI presentation boundaries.",
        ),
        ArchitectureDoc(
            id=f"arch_{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            section="data_flow",
            title="Data Flow",
            content="User Action -> UI Controller -> Service Layer -> Local Repository / Remote API -> State Update.",
        ),
    ]

    initial_features: List[Feature] = [
        Feature(
            id=f"feat_{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            title="Core Application Setup",
            description="Initial repository bootstrap, dependency resolution, and basic structure.",
            status=FeatureStatus.IN_PROGRESS,
            priority=Priority.HIGH,
            requirements=["Setup project files", "Configure linting and formatting", "Create baseline tests"],
            acceptance_criteria=["Tests run cleanly with 100% pass", "Build completes without errors"],
        ),
    ]

    initial_tasks: List[Task] = [
        Task(
            id=f"task_{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            feature_id=initial_features[0].id,
            title="Verify development environment & dependencies",
            description="Run install scripts, check package locks, and ensure build toolchain is ready.",
            status=TaskStatus.TODO,
            priority=Priority.HIGH,
            task_type=TaskType.DEVOPS,
            estimated_effort="1h",
        ),
        Task(
            id=f"task_{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            feature_id=initial_features[0].id,
            title="Configure CI/CD and automated test suite",
            description="Setup GitHub Actions or local test runner to run tests on every commit.",
            status=TaskStatus.BACKLOG,
            priority=Priority.MEDIUM,
            task_type=TaskType.TESTING,
            estimated_effort="2h",
        ),
    ]

    return {
        "instructions": instructions,
        "prompts": prompts,
        "specs": specs,
        "arch_docs": arch_docs,
        "features": initial_features,
        "tasks": initial_tasks,
    }
