import json
from pathlib import Path
import subprocess
import sys
import time
root = Path(__file__).resolve().parents[4]
pytest = [sys.executable, '-m', 'pytest']
targets = ['tests/test_ui_browser.py', 'tests/test_ui_coding_workspace.py', 'tests/test_ui_chat_workspace.py']
collection = subprocess.run(pytest + ['--collect-only', '-q', *targets], cwd=root, text=True, capture_output=True)
if collection.returncode:
    print(collection.stdout + collection.stderr, flush=True)
    raise SystemExit(collection.returncode)
nodes = [line.strip() for line in collection.stdout.splitlines() if line.startswith('tests/') and '::' in line]
results = []
started = time.monotonic()
evidence = Path(__file__).resolve().parent
evidence.mkdir(parents=True, exist_ok=True)
with (evidence / 'native-ui.log').open('w') as log:
    for i, node in enumerate(nodes, 1):
        result = subprocess.run(pytest + ['-q', node], cwd=root, text=True, capture_output=True, timeout=90)
        results.append({'test': node, 'exit_code': result.returncode})
        log.write(node + '\n' + result.stdout + result.stderr + '\n')
        log.flush()
        print(f'{i}/{len(nodes)} ' + ('PASS ' if result.returncode == 0 else 'FAIL ') + node, flush=True)
        if result.returncode:
            print(result.stdout + result.stderr, flush=True)
report = {'mode': 'fresh native macOS process per case', 'tests': results, 'passed': sum(row['exit_code'] == 0 for row in results), 'total': len(nodes), 'seconds': round(time.monotonic()-started, 2)}
(evidence / 'native-ui.json').write_text(json.dumps(report, indent=2) + '\n')
raise SystemExit(0 if len(results) == len(nodes) and all(row['exit_code'] == 0 for row in results) else 1)
