from copy import deepcopy

import pytest
from pydantic import ValidationError

from firik_agent.benchmarks.models import (
    BenchmarkSuite, Cost, EvaluationCriteria, HarnessConfiguration,
    IndividualTrial, ModelProviderIdentity, RepositoryFixture, TerminalOutcome,
    TokenUsage, VerificationResult,
)


def test_configuration_roundtrip_and_stable_fingerprint():
    config = HarnessConfiguration()
    assert HarnessConfiguration.model_validate_json(config.model_dump_json()) == config
    assert config.fingerprint() == deepcopy(config).fingerprint()
    assert config.fingerprint() != config.model_copy(update={"request_budget": 3}).fingerprint()
    assert TokenUsage().input_tokens is None and Cost().amount is None


@pytest.mark.parametrize("change", [
    {"request_budget": True}, {"token_budget": 0}, {"timeout_seconds": float("nan")},
    {"permissions": {"allow_terminal": True}}, {"fallback": True}, {"surprise": "value"},
])
def test_config_rejects_unsafe_or_unbounded_fields(change):
    with pytest.raises(ValidationError):
        HarnessConfiguration.model_validate({**HarnessConfiguration().model_dump(), **change})


def test_versioned_trial_has_explicit_identity_and_outcome():
    record = IndividualTrial(trial_id="trial-1", run_id="run-1", suite_id="coding", task_id="bug-one",
        repetition=1, configuration_sha256="a" * 64, task_sha256="b" * 64, starting_revision="c" * 40,
        model=ModelProviderIdentity(provider="configured", model="selected"),
        outcome=TerminalOutcome(status="completed", completion_claimed=True, verified=True),
        verification=VerificationResult(passed=True, source_match=True, detail="Independent source match."))
    assert record.schema_version == 1
    assert IndividualTrial.model_validate_json(record.model_dump_json()) == record


@pytest.mark.parametrize("files", [{"../outside.py": "x"}, {"main.py": "x", "MAIN.py": "y"}])
def test_repository_and_oracle_reject_escaping_paths(files):
    with pytest.raises(ValidationError):
        RepositoryFixture(id="fixture", starting_revision="a" * 40, files=files)
    with pytest.raises(ValidationError):
        EvaluationCriteria(expected_files=files, description="Hidden source oracle.")
