"""Versioned benchmark contracts; nullable measurements never mean zero.

These are host records, not a second runtime event protocol. Native AgentEvent
and CodingSessionStore remain the source of execution evidence.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from firik_agent.benchmarks.manifests import EVALUATOR_VERSION, StrictModel, source_path


def fingerprint(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class VersionedRecord(StrictModel):
    schema_version: Literal[1] = 1

    def fingerprint(self) -> str:
        return fingerprint(self)


class ModelProviderIdentity(VersionedRecord):
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    endpoint_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ToolPermissions(VersionedRecord):
    allow_edits: bool = True
    allow_tests: bool = True
    allow_terminal: Literal[False] = False
    allow_mcp: Literal[False] = False
    network: Literal[False] = False


class HarnessConfiguration(VersionedRecord):
    id: str = Field(default="native-replay", pattern=r"^[a-z][a-z0-9-]{0,63}$")
    request_budget: int = Field(default=16, ge=1, le=128)
    token_budget: int = Field(default=10_000, ge=1, le=1_000_000)
    timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    max_output_tokens: int = Field(default=2048, ge=1, le=32768)
    temperature: float = Field(default=0.0, ge=0, le=2)
    model: ModelProviderIdentity = Field(default_factory=lambda: ModelProviderIdentity(provider="replay", model="scripted-v1"))
    permissions: ToolPermissions = Field(default_factory=ToolPermissions)
    evaluator_version: Literal[EVALUATOR_VERSION] = EVALUATOR_VERSION
    compaction: bool = False
    fallback: Literal[False] = False


class RepositoryFixture(VersionedRecord):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    starting_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    files: dict[str, str] = Field(min_length=1, max_length=20)

    @field_validator("files")
    @classmethod
    def bounded_sources(cls, value):
        if len({path.casefold() for path in value}) != len(value):
            raise ValueError("Fixture source names collide.")
        for path, content in value.items():
            source_path(path)
            if len(content.encode()) > 32768:
                raise ValueError("Fixture source exceeds the bounded evaluator limit.")
        return value


class EvaluationCriteria(VersionedRecord):
    version: Literal[EVALUATOR_VERSION] = EVALUATOR_VERSION
    kind: Literal["exact-source-ast"] = "exact-source-ast"
    expected_files: dict[str, str] = Field(min_length=1, max_length=20)
    independently_controlled: Literal[True] = True
    description: str = Field(min_length=1, max_length=2048)

    _bounded_sources = field_validator("expected_files")(RepositoryFixture.bounded_sources.__func__)


TaskCategory = Literal["bug-fixing", "refactoring", "feature", "testing", "build-ci", "security"]


class TaskSpecification(VersionedRecord):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    category: TaskCategory
    prompt: str = Field(min_length=1, max_length=8192)
    repository: RepositoryFixture
    evaluation: EvaluationCriteria
    curation_status: Literal["curated-small-source-task"] = "curated-small-source-task"


class BenchmarkSuite(VersionedRecord):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    version: str = Field(min_length=1, max_length=50)
    mode: Literal["offline", "live"]
    description: str = Field(min_length=1, max_length=4096)
    tasks: list[TaskSpecification] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique_tasks(self):
        if len({task.id for task in self.tasks}) != len(self.tasks):
            raise ValueError("Suite task identities must be unique.")
        return self


MeasurementSource = Literal["provider", "local", "estimate", "synthetic", "unknown"]


class TokenUsage(VersionedRecord):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_tokens: int | None = Field(default=None, ge=0)
    source: MeasurementSource = "unknown"
    complete: bool = False


class Cost(VersionedRecord):
    amount: float | None = Field(default=None, ge=0)
    currency: Literal["USD"] = "USD"
    source: MeasurementSource = "unknown"
    complete: bool = False


class ToolExecutionTrace(VersionedRecord):
    tool_call_id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=100)
    status: Literal["completed", "failed", "denied", "interrupted"]
    duration_ms: float | None = Field(default=None, ge=0)
    event_sequence: int = Field(ge=1)


class RecoveryEvent(VersionedRecord):
    kind: Literal["retry", "tool-correction", "compaction", "resume"]
    request_id: str | None = None
    event_sequence: int = Field(ge=1)
    count: int = Field(default=1, ge=1)


class VerificationResult(VersionedRecord):
    evaluator_version: str = EVALUATOR_VERSION
    passed: bool
    source_match: bool
    evaluator_intact: bool = True
    detail: str = Field(max_length=8192)


class TerminalOutcome(VersionedRecord):
    status: Literal["completed", "failed", "blocked", "cancelled", "timeout", "interrupted", "crashed"]
    completion_claimed: bool = False
    verified: bool = False
    failure_kind: str | None = Field(default=None, max_length=100)


class IndividualTrial(VersionedRecord):
    trial_id: str = Field(min_length=1, max_length=256)
    run_id: str = Field(min_length=1, max_length=256)
    suite_id: str = Field(min_length=1, max_length=64)
    task_id: str = Field(min_length=1, max_length=64)
    repetition: int = Field(ge=1, le=100)
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    starting_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    model: ModelProviderIdentity
    outcome: TerminalOutcome
    verification: VerificationResult
    tokens: TokenUsage = Field(default_factory=TokenUsage)
    cost: Cost = Field(default_factory=Cost)
    tools: list[ToolExecutionTrace] = Field(default_factory=list, max_length=10000)
    recoveries: list[RecoveryEvent] = Field(default_factory=list, max_length=10000)
