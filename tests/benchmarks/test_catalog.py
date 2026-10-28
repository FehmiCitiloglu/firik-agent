from collections import Counter

from firik_agent.benchmarks.coding import coding_suite, task_manifest
from firik_agent.benchmarks.models import BenchmarkSuite


def test_forty_curated_tasks_have_six_categories_and_pinned_sources():
    suite = coding_suite()
    assert len(suite.tasks) == 40
    assert len({task.id for task in suite.tasks}) == 40
    assert len(Counter(task.category for task in suite.tasks)) == 6
    assert BenchmarkSuite.model_validate_json(suite.model_dump_json()) == suite
    assert coding_suite().fingerprint() == suite.fingerprint()
    assert all(len(task.repository.starting_revision) == 40 for task in suite.tasks)
    assert all(task.repository.files != task.evaluation.expected_files for task in suite.tasks)


def test_all_curated_oracles_reject_start_and_accept_expected_without_execution(tmp_path):
    from firik_agent.benchmarks.runner import evaluate_sources

    for task in coding_suite().tasks:
        root = tmp_path / task.id
        root.mkdir()
        for name, content in task.repository.files.items():
            path = root / name
            path.parent.mkdir(exist_ok=True, parents=True)
            path.write_bytes(content.encode("utf-8"))
        assert not evaluate_sources(root, task.evaluation.expected_files)[0], task.id
        for name, content in task.evaluation.expected_files.items():
            (root / name).write_bytes(content.encode("utf-8"))
        assert evaluate_sources(root, task.evaluation.expected_files)[0], task.id
        manifest = task_manifest(task)
        assert manifest.expected_files == task.evaluation.expected_files
        assert manifest.files == task.repository.files


def test_catalog_revision_matches_actual_disposable_git_commit(tmp_path):
    from firik_agent.benchmarks.runner import _git

    task = coding_suite().tasks[0]
    for name, content in task.repository.files.items():
        (tmp_path / name).write_bytes(content.encode("utf-8"))
    _git(tmp_path, "init", "--quiet", "--template=")
    _git(tmp_path, "add", "--all")
    _git(tmp_path, "commit", "--quiet", "--allow-empty", "-m", "Deterministic fixture")
    assert _git(tmp_path, "rev-parse", "HEAD") == task.repository.starting_revision
