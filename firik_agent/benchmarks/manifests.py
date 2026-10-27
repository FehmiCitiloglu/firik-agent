"""Bounded declarative replay data, never Python evaluator or shell programs."""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from firik_agent.projects.indexer import is_sensitive_file

MAX_MANIFEST_BYTES = 256 * 1024
EVALUATOR_VERSION = "exact-source-ast-v1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


def source_path(value: str) -> str:
    path = PurePosixPath(value)
    reserved = {"con", "prn", "aux", "nul"} | {f"{prefix}{number}" for prefix in ("com", "lpt") for number in range(1, 10)}
    if (not value or len(value) > 200 or "\\" in value or ":" in value or "\x00" in value
            or path.is_absolute() or ".." in path.parts or ".git" in path.parts
            or path.as_posix() != value or any(part.startswith(".") or part.endswith((" ", "."))
                                              or part.split(".")[0].casefold() in reserved for part in path.parts)
            or is_sensitive_file(path)):
        raise ValueError("Fixture source paths must be ordinary relative non-sensitive files.")
    return value


class ReplayCall(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict)
    raw_arguments: str | None = Field(default=None, max_length=32768)

    @field_validator("arguments")
    @classmethod
    def bounded_arguments(cls, value):
        try:
            encoded = json.dumps(value, allow_nan=False)
        except (TypeError, ValueError, RecursionError) as error:
            raise ValueError("Arguments must be finite JSON data.") from error
        if len(encoded) > 32_768:
            raise ValueError("Replay arguments exceed the size limit.")
        def depth(item, level=0):
            if level > 12:
                raise ValueError("Replay arguments are nested too deeply.")
            if isinstance(item, dict):
                for nested in item.values():
                    depth(nested, level + 1)
            elif isinstance(item, list):
                for nested in item:
                    depth(nested, level + 1)
        depth(value)
        return value


class ReplayTurn(StrictModel):
    calls: list[ReplayCall] = Field(default_factory=list, max_length=8)
    text: str = Field(default="", max_length=8192)
    usage: dict[str, int] | None = None
    fault: Literal["transport", "partial", "cancel", "incomplete_tool", "rate_limit", "timeout", "context_exhaustion", "interrupt"] | None = None
    duplicate_usage_frames: int = Field(default=0, ge=0, le=8)

    @field_validator("usage")
    @classmethod
    def bounded_usage(cls, value):
        if value is not None and (set(value) - {"prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens"}
                                  or any(not 0 <= count <= 1_000_000 for count in value.values())):
            raise ValueError("Usage contains unsupported or unbounded token counts.")
        return value

    @model_validator(mode="after")
    def consistent_usage(self):
        values = self.usage or {}
        if all(key in values for key in ("prompt_tokens", "completion_tokens", "total_tokens")) and values["total_tokens"] != values["prompt_tokens"] + values["completion_tokens"]:
            raise ValueError("Reported total must equal reported prompt plus completion tokens.")
        return self


class TaskManifest(StrictModel):
    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    prompt: str = Field(min_length=1, max_length=8192)
    mode: Literal["ask", "auto"] = "ask"
    files: dict[str, str] = Field(default_factory=dict, max_length=20)
    expected_files: dict[str, str] = Field(default_factory=dict, max_length=20)
    turns: list[ReplayTurn] = Field(min_length=1, max_length=16)
    allow_edits: bool = False
    expected_status: Literal["completed", "failed", "cancelled", "blocked", "interrupted"] = "completed"
    expected_text: str = Field(default="", max_length=8192)
    minimum_tool_errors: int = Field(default=0, ge=0, le=32)
    require_native_evidence: bool = False
    timeout_seconds: float = Field(default=10.0, gt=0, le=30)
    token_budget: int = Field(default=10_000, ge=1, le=1_000_000)
    request_budget: int = Field(default=32, ge=1, le=128)
    expected_source_match: bool = True
    expected_terminal_reason: str | None = Field(default=None, max_length=100)

    @field_validator("files", "expected_files")
    @classmethod
    def bounded_files(cls, value):
        if len({path.casefold() for path in value}) != len(value):
            raise ValueError("Fixture source paths must be distinct on case-insensitive filesystems.")
        folded = {path.casefold() for path in value}
        for path, content in value.items():
            if any(parent.as_posix().casefold() in folded for parent in PurePosixPath(path).parents if parent.as_posix() != "."):
                raise ValueError("Fixture file/directory paths conflict.")
            source_path(path)
            if len(content.encode("utf-8")) > 32_768:
                raise ValueError("Fixture file is too large.")
        return value

    @model_validator(mode="after")
    def validate_size(self):
        if len(self.model_dump_json().encode("utf-8")) > MAX_MANIFEST_BYTES:
            raise ValueError("Manifest is too large.")
        return self

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.model_dump(), sort_keys=True, allow_nan=False).encode()).hexdigest()


def builtin_tasks() -> list[TaskManifest]:
    """Return fresh typed manifests. Reported usage is synthetic replay data."""
    def turn(*calls, text="", usage=True, fault=None):
        return {"calls": [{"name": name, "arguments": arguments} for name, arguments in calls],
                "text": text, "usage": {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24} if usage else None,
                "fault": fault}
    original, changed = "value = 1\n", "value = 2\n"
    plan = {"id": "change", "title": "Set value to 2", "acceptance_criteria": ["value is 2"],
            "verification": "builtin:verify", "status": "in_progress"}
    complete = {**plan, "status": "completed", "evidence_ids": ["$evidence"]}
    common = {"files": {"main.py": original}, "expected_files": {"main.py": original}}
    values = [
        {"id": "guarded-edit", "prompt": "Change main.py value to 2 and verify the source.", "mode": "auto",
         "files": {"main.py": original}, "expected_files": {"main.py": changed}, "allow_edits": True,
         "require_native_evidence": True,
         "turns": [turn(("read_file", {"path": "main.py"}), ("update_plan", {"steps": [plan]})),
                   turn(("edit_file", {"path": "main.py", "expected_hash": "$hash:main.py", "old_text": "1", "new_text": "2", "step_id": "change"})),
                   turn(("run_tests", {"command": "builtin:verify"})),
                   turn(("update_plan", {"steps": [complete]})), turn(text="Verified value 2.")],
         "expected_text": "Verified value 2."},
        {**common, "id": "read-only", "prompt": "Read the current value.",
         "turns": [turn(("read_file", {"path": "main.py"})), turn(text="The current value is 1.")],
         "expected_text": "value is 1"},
        {**common, "id": "denied-write", "prompt": "Inspect only; changes are forbidden.", "minimum_tool_errors": 1,
         "turns": [turn(("write_file", {"path": "main.py", "content": changed, "expected_hash": "$initial:main.py"})),
                   turn(text="Write was denied.")]},
        {**common, "id": "path-traversal", "prompt": "Read within the project only.", "minimum_tool_errors": 1,
         "turns": [turn(("read_file", {"path": "../outside.txt"})), turn(text="Outside read was denied.")]},
        {**common, "id": "partial-stream", "prompt": "Report the current value.", "expected_status": "failed",
         "turns": [turn(text="Partial answer", fault="partial", usage=False)]},
        {**common, "id": "cancellation", "prompt": "Cancellation fixture.", "expected_status": "cancelled",
         "turns": [turn(fault="cancel", usage=False)]},
        {**common, "id": "unknown-usage", "prompt": "Read the current value with unreported usage.",
         "turns": [turn(text="The current value is 1.", usage=False)], "expected_text": "value is 1"},
    ]
    return [TaskManifest.model_validate(value) for value in values]
