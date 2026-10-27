"""Forty small curated coding tasks with independent, host-controlled oracles.

This is a bounded source-transformation collection, not a claim to represent
large production repositories. Exact canonical source and Python syntax are
checked without executing untrusted repository code. No model results ship.
"""
from __future__ import annotations

from firik_agent.benchmarks.manifests import TaskManifest, ReplayTurn
from firik_agent.benchmarks.models import (
    BenchmarkSuite, EvaluationCriteria, RepositoryFixture, TaskSpecification,
)


# Each entry is explicitly curated: identity, category, instruction, source
# path, immutable initial bytes and independently controlled expected bytes.
_TASKS = [
    ("bug-inclusive-bound", "bug-fixing", "Fix inclusive(n) to include n in the returned list, retaining the function and one return statement.", "main.py",
     "def inclusive(n):\n    return list(range(n))\n", "def inclusive(n):\n    return list(range(n + 1))\n"),
    ("bug-shared-default", "bug-fixing", "Replace append_value's shared list default with None and initialize an empty list when None, preserving the append and return.", "main.py",
     "def append_value(value, values=[]):\n    values.append(value)\n    return values\n", "def append_value(value, values=None):\n    if values is None:\n        values = []\n    values.append(value)\n    return values\n"),
    ("bug-empty-average", "bug-fixing", "Add an early return of 0 when values is empty before calculating the existing average.", "main.py",
     "def average(values):\n    return sum(values) / len(values)\n", "def average(values):\n    if not values:\n        return 0\n    return sum(values) / len(values)\n"),
    ("bug-key-presence", "bug-fixing", "Fix has_key to test dictionary membership instead of the truthiness of its value.", "main.py",
     "def has_key(data, key):\n    return bool(data.get(key))\n", "def has_key(data, key):\n    return key in data\n"),
    ("bug-negative-max", "bug-fixing", "Initialize maximum's result from values[0] so all-negative nonempty lists work, preserving its loop.", "main.py",
     "def maximum(values):\n    result = 0\n    for value in values:\n        result = max(result, value)\n    return result\n", "def maximum(values):\n    result = values[0]\n    for value in values:\n        result = max(result, value)\n    return result\n"),
    ("bug-strip-newline", "bug-fixing", "Fix trim_newline to remove only trailing newline characters, using rstrip with a newline argument.", "main.py",
     "def trim_newline(text):\n    return text.strip()\n", "def trim_newline(text):\n    return text.rstrip('\\n')\n"),
    ("bug-celsius", "bug-fixing", "Correct celsius's Fahrenheit conversion by parenthesizing fahrenheit minus 32 before multiplication.", "main.py",
     "def celsius(fahrenheit):\n    return fahrenheit - 32 * 5 / 9\n", "def celsius(fahrenheit):\n    return (fahrenheit - 32) * 5 / 9\n"),
    ("bug-copy-sort", "bug-fixing", "Make sorted_values return a sorted copy with the sorted builtin and leave the input untouched.", "main.py",
     "def sorted_values(values):\n    values.sort()\n    return values\n", "def sorted_values(values):\n    return sorted(values)\n"),
    ("refactor-square-comprehension", "refactoring", "Replace the accumulator and loop in squares with one list-comprehension return, retaining value * value.", "main.py",
     "def squares(values):\n    result = []\n    for value in values:\n        result.append(value * value)\n    return result\n", "def squares(values):\n    return [value * value for value in values]\n"),
    ("refactor-context-file", "refactoring", "Refactor read_text to use with open(path) as stream and return stream.read() inside the context.", "main.py",
     "def read_text(path):\n    stream = open(path)\n    text = stream.read()\n    stream.close()\n    return text\n", "def read_text(path):\n    with open(path) as stream:\n        return stream.read()\n"),
    ("refactor-get-default", "refactoring", "Replace the lookup branch with return data.get(key, 'unknown').", "main.py",
     "def lookup(data, key):\n    if key in data:\n        return data[key]\n    return 'unknown'\n", "def lookup(data, key):\n    return data.get(key, 'unknown')\n"),
    ("refactor-any-positive", "refactoring", "Replace the loop in has_positive with a single any(value > 0 for value in values) return.", "main.py",
     "def has_positive(values):\n    for value in values:\n        if value > 0:\n            return True\n    return False\n", "def has_positive(values):\n    return any(value > 0 for value in values)\n"),
    ("refactor-named-limit", "refactoring", "Extract 100 to a module constant MAX_ITEMS, followed by two blank lines, and use it in limited's slice.", "main.py",
     "def limited(values):\n    return values[:100]\n", "MAX_ITEMS = 100\n\n\ndef limited(values):\n    return values[:MAX_ITEMS]\n"),
    ("refactor-enumerate", "refactoring", "Replace pairs' range/len indexing with return list(enumerate(values)).", "main.py",
     "def pairs(values):\n    return [(index, values[index]) for index in range(len(values))]\n", "def pairs(values):\n    return list(enumerate(values))\n"),
    ("feature-clamp", "feature", "Implement clamp(value, low, high) using return max(low, min(value, high)).", "main.py",
     "def clamp(value, low, high):\n    raise NotImplementedError\n", "def clamp(value, low, high):\n    return max(low, min(value, high))\n"),
    ("feature-slug", "feature", "Implement slug(text) as '-'.join(text.lower().split()), retaining the function signature.", "main.py",
     "def slug(text):\n    raise NotImplementedError\n", "def slug(text):\n    return '-'.join(text.lower().split())\n"),
    ("feature-stable-unique", "feature", "Implement unique(values) using list(dict.fromkeys(values)) to preserve first appearance.", "main.py",
     "def unique(values):\n    raise NotImplementedError\n", "def unique(values):\n    return list(dict.fromkeys(values))\n"),
    ("feature-chunks", "feature", "Implement chunks(values, size): raise ValueError('size must be positive') when size <= 0, then return list slices using range(0, len(values), size).", "main.py",
     "def chunks(values, size):\n    raise NotImplementedError\n", "def chunks(values, size):\n    if size <= 0:\n        raise ValueError('size must be positive')\n    return [values[index:index + size] for index in range(0, len(values), size)]\n"),
    ("feature-is-even", "feature", "Implement is_even(value) as a single return testing value modulo 2 equals 0.", "main.py",
     "def is_even(value):\n    raise NotImplementedError\n", "def is_even(value):\n    return value % 2 == 0\n"),
    ("feature-merge-maps", "feature", "Implement merged(left, right) as return {**left, **right}.", "main.py",
     "def merged(left, right):\n    raise NotImplementedError\n", "def merged(left, right):\n    return {**left, **right}\n"),
    ("feature-count-values", "feature", "Import Counter from collections, use two blank lines before counts, and implement it as return dict(Counter(values)).", "main.py",
     "def counts(values):\n    raise NotImplementedError\n", "from collections import Counter\n\n\ndef counts(values):\n    return dict(Counter(values))\n"),
    ("feature-safe-divide", "feature", "Implement divide(a, b): return None when b == 0, otherwise return a / b.", "main.py",
     "def divide(a, b):\n    raise NotImplementedError\n", "def divide(a, b):\n    if b == 0:\n        return None\n    return a / b\n"),
    ("test-addition-negative", "testing", "Append test_add_negative with a single assert add(-2, -3) == -5. Separate the existing test by two blank lines.", "test_main.py",
     "from main import add\n\n\ndef test_add_positive():\n    assert add(2, 3) == 5\n", "from main import add\n\n\ndef test_add_positive():\n    assert add(2, 3) == 5\n\n\ndef test_add_negative():\n    assert add(-2, -3) == -5\n"),
    ("test-empty-total", "testing", "Replace the placeholder test body with assert total([]) == 0 and rename test_placeholder to test_empty_total.", "test_main.py",
     "from main import total\n\n\ndef test_placeholder():\n    pass\n", "from main import total\n\n\ndef test_empty_total():\n    assert total([]) == 0\n"),
    ("test-zero-identity", "testing", "Replace the placeholder test with test_add_zero asserting add(7, 0) == 7.", "test_main.py",
     "from main import add\n\n\ndef test_placeholder():\n    pass\n", "from main import add\n\n\ndef test_add_zero():\n    assert add(7, 0) == 7\n"),
    ("test-reverse-involution", "testing", "Replace the placeholder test with test_reverse_twice asserting reverse(reverse('abc')) == 'abc'.", "test_main.py",
     "from main import reverse\n\n\ndef test_placeholder():\n    pass\n", "from main import reverse\n\n\ndef test_reverse_twice():\n    assert reverse(reverse('abc')) == 'abc'\n"),
    ("test-invalid-size", "testing", "Import pytest before the existing import. Replace the placeholder test with test_invalid_size containing with pytest.raises(ValueError): chunks([1], 0).", "test_main.py",
     "from main import chunks\n\n\ndef test_placeholder():\n    pass\n", "import pytest\nfrom main import chunks\n\n\ndef test_invalid_size():\n    with pytest.raises(ValueError):\n        chunks([1], 0)\n"),
    ("test-sort-immutability", "testing", "Replace placeholder with test_sort_preserves_input: assign values = [2, 1], assert sorted_values(values) == [1, 2], then assert values == [2, 1].", "test_main.py",
     "from main import sorted_values\n\n\ndef test_placeholder():\n    pass\n", "from main import sorted_values\n\n\ndef test_sort_preserves_input():\n    values = [2, 1]\n    assert sorted_values(values) == [1, 2]\n    assert values == [2, 1]\n"),
    ("ci-python-requirement", "build-ci", "Change requires-python to >=3.10 in pyproject.toml, retaining the rest byte-for-byte.", "pyproject.toml",
     "[project]\nname = 'fixture'\nversion = '1.0.0'\nrequires-python = '>=3.8'\n", "[project]\nname = 'fixture'\nversion = '1.0.0'\nrequires-python = '>=3.10'\n"),
    ("ci-pytest-path", "build-ci", "Fix testpaths in pytest.ini to tests instead of test, preserving the file format.", "pytest.ini",
     "[pytest]\ntestpaths = test\n", "[pytest]\ntestpaths = tests\n"),
    ("ci-module-pytest", "build-ci", "Change Makefile's test recipe to python -m pytest -q to bind pytest to the selected interpreter.", "Makefile",
     "test:\n\tpytest -q\n", "test:\n\tpython -m pytest -q\n"),
    ("ci-build-backend", "build-ci", "Correct build-backend to setuptools.build_meta, preserving the requires entry.", "pyproject.toml",
     "[build-system]\nrequires = ['setuptools>=61']\nbuild-backend = 'setuptools.buld_meta'\n", "[build-system]\nrequires = ['setuptools>=61']\nbuild-backend = 'setuptools.build_meta'\n"),
    ("ci-package-discovery", "build-ci", "Correct setuptools package discovery include to fixture* instead of fixtuer*.", "pyproject.toml",
     "[tool.setuptools.packages.find]\ninclude = ['fixtuer*']\n", "[tool.setuptools.packages.find]\ninclude = ['fixture*']\n"),
    ("ci-npm-test-command", "build-ci", "Set package.json's test script to node --test, preserving its existing compact indentation and trailing newline.", "package.json",
     "{\n  \"scripts\": {\n    \"test\": \"node test\"\n  }\n}\n", "{\n  \"scripts\": {\n    \"test\": \"node --test\"\n  }\n}\n"),
    ("security-constant-time", "security", "Import compare_digest from hmac and replace the token equality check with compare_digest(candidate, expected), preserving two blank lines before the function.", "main.py",
     "def token_matches(candidate, expected):\n    return candidate == expected\n", "from hmac import compare_digest\n\n\ndef token_matches(candidate, expected):\n    return compare_digest(candidate, expected)\n"),
    ("security-safe-yaml", "security", "Replace yaml.load(text, Loader=yaml.Loader) with yaml.safe_load(text), preserving the import and spacing.", "main.py",
     "import yaml\n\n\ndef parse(text):\n    return yaml.load(text, Loader=yaml.Loader)\n", "import yaml\n\n\ndef parse(text):\n    return yaml.safe_load(text)\n"),
    ("security-json-no-eval", "security", "Import json followed by two blank lines and replace eval(text) with json.loads(text).", "main.py",
     "def parse(text):\n    return eval(text)\n", "import json\n\n\ndef parse(text):\n    return json.loads(text)\n"),
    ("security-query-parameters", "security", "Replace string SQL interpolation with db.execute('SELECT name FROM users WHERE id = ?', (user_id,)), preserving the signature.", "main.py",
     "def lookup(db, user_id):\n    return db.execute('SELECT name FROM users WHERE id = ' + user_id)\n", "def lookup(db, user_id):\n    return db.execute('SELECT name FROM users WHERE id = ?', (user_id,))\n"),
    ("security-verify-tls", "security", "Set verify=True in the existing client.get call to enable TLS certificate verification.", "main.py",
     "def fetch(client, url):\n    return client.get(url, verify=False)\n", "def fetch(client, url):\n    return client.get(url, verify=True)\n"),
    ("security-no-shell", "security", "Replace subprocess.run(command, shell=True) with subprocess.run(command, shell=False, check=True); command is already an argv list.", "main.py",
     "import subprocess\n\n\ndef run(command):\n    return subprocess.run(command, shell=True)\n", "import subprocess\n\n\ndef run(command):\n    return subprocess.run(command, shell=False, check=True)\n"),
]

_SUPPORT = {
    "test-addition-negative": "def add(a, b):\n    return a + b\n",
    "test-empty-total": "def total(values):\n    return sum(values)\n",
    "test-zero-identity": "def add(a, b):\n    return a + b\n",
    "test-reverse-involution": "def reverse(text):\n    return text[::-1]\n",
    "test-invalid-size": "def chunks(values, size):\n    if size <= 0:\n        raise ValueError('size must be positive')\n    return [values[index:index + size] for index in range(0, len(values), size)]\n",
    "test-sort-immutability": "def sorted_values(values):\n    return sorted(values)\n",
}


def coding_suite() -> BenchmarkSuite:
    from firik_agent.benchmarks.fixtures import fixture_revision

    tasks = []
    for identifier, category, instruction, path, initial, expected in _TASKS:
        files = {path: initial}
        expected_files = {path: expected}
        if identifier in _SUPPORT:
            files["main.py"] = expected_files["main.py"] = _SUPPORT[identifier]
        tasks.append(TaskSpecification(
            id=identifier, category=category,
            prompt=(f"Work only on {path}. {instruction} Keep all other source bytes and filenames unchanged. "
                    "Read the file first. Create an update_plan step with verification 'builtin:verify' before using "
                    "the guarded edit_file tool, associating the edit with its step_id. Run run_tests with command "
                    "'builtin:verify', then mark the plan step completed with the returned evidence_id in evidence_ids. "
                    "These are small canonical-source tasks; "
                    "verification checks the requested source transformation and syntax without executing repository code."),
            repository=RepositoryFixture(id=identifier, starting_revision=fixture_revision(files), files=files),
            evaluation=EvaluationCriteria(expected_files=expected_files, description="Host-controlled exact canonical source and Python AST syntax; no fixture code execution."),
        ))
    return BenchmarkSuite(id="coding", version="1.0.0", mode="live", tasks=tasks,
        description="40 curated small canonical-source tasks across six categories. Exact source/AST oracles; not behavioral test execution or production-repository coverage. No live evaluations bundled.")


def coding_tasks() -> list[TaskSpecification]:
    return coding_suite().tasks


def task_manifest(task: TaskSpecification, *, timeout_seconds: float = 30.0, token_budget: int = 10_000) -> TaskManifest:
    """Adapt host-controlled task data to the existing isolated trial engine.

    Placeholder turns satisfy legacy manifest shape; a real provider never sees
    or consumes them. Expected bytes remain host-held, outside the repository.
    """
    return TaskManifest(id=task.id, prompt=task.prompt, mode="auto", files=task.repository.files,
        expected_files=task.evaluation.expected_files, allow_edits=True, require_native_evidence=True,
        turns=[ReplayTurn(text="Live-provider placeholder; never replayed.")],
        timeout_seconds=min(timeout_seconds, 30.0), token_budget=token_budget)
