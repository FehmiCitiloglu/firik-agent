from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[4]
output = root / 'docs/Evidence/Voice/20261031'
output.mkdir(parents=True, exist_ok=True)
files = ['tests/test_ui_voice.py', 'tests/test_ui_chat.py', 'tests/test_ui_chat_workspace.py',
         'tests/test_ui_coding_workspace.py', 'tests/test_ui_model_selection.py']
collected = subprocess.run([sys.executable, '-m', 'pytest', '--collect-only', '-q', *files],
                           cwd=root, text=True, capture_output=True)
if collected.returncode:
    print(collected.stdout + collected.stderr)
    raise SystemExit(collected.returncode)
nodes = [line for line in collected.stdout.splitlines() if line.startswith('tests/') and '::' in line]
failed = []
with (output / 'desktop-tests.log').open('w') as log:
    for i, node in enumerate(nodes, 1):
        result = subprocess.run([sys.executable, '-m', 'pytest', '-q', node], cwd=root,
                                text=True, capture_output=True, timeout=90)
        line = f'{i}/{len(nodes)} {node}: {result.returncode}'
        print(line, flush=True)
        log.write(line + '\n' + result.stdout + result.stderr + '\n')
        log.flush()
        if result.returncode:
            failed.append(node)
    log.write(f'Cases: {len(nodes)}, failures: {len(failed)}\n')
print(f'Cases: {len(nodes)}, failures: {len(failed)}', flush=True)
if failed:
    print('\n'.join(failed), flush=True)
raise SystemExit(bool(failed))
