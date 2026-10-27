"""The supplemental benchmark runs real native recovery and records effects."""
import importlib.util
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_paired_probes_measure_recovery_without_false_retries_or_effect_replays():
    spec = importlib.util.spec_from_file_location("phase2_scripted_benchmark", Path(__file__).parents[1] / "scripts/phase2_scripted_benchmark.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = await module.run(1)
    summary = module.summarize(rows)
    assert all(row["passed"] for row in rows)
    assert summary["disabled"]["recovered"] == 0
    assert summary["adaptive"]["recovered"] == 4
    assert summary["adaptive"]["false_retry_count"] == 0
    assert summary["adaptive"]["repeated_external_effects"] == 0
    unknown = next(row for row in rows if row["case"] == "unknown-external" and row["profile"] == "adaptive")
    assert unknown["external_effects"] == 1 and unknown["requests"] == 1
    confirmed = next(row for row in rows if row["case"] == "confirmed-external-then-overload" and row["profile"] == "adaptive")
    assert confirmed["external_effects"] == 1 and confirmed["requests"] == 3
