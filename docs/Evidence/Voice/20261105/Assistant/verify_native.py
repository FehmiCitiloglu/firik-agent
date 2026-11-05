import json
import os
from pathlib import Path
import subprocess
import sys
import time

root = Path(__file__).resolve().parents[5]
targets = [
    'tests/test_ui_voice_assistant.py',
    'tests/test_ui_voice_experience.py::test_voice_surface_and_compact_composer_fit_small_native_window_in_both_themes',
    'tests/test_ui_voice_experience.py::test_voice_surface_follows_runtime_theme_and_restores_outlined_focus',
    'tests/test_ui_voice_experience.py::test_session_mute_resume_and_window_close_release_capture_without_agent_cancel',
    'tests/test_ui_voice_settings_section.py::test_voice_form_fits_narrow_native_window_and_theme_without_horizontal_clipping',
]
collection = subprocess.run([sys.executable, '-m', 'pytest', '--collect-only', '-q', *targets], cwd=root, text=True, capture_output=True)
if collection.returncode:
    print(collection.stdout + collection.stderr, flush=True)
    raise SystemExit(collection.returncode)
nodes = [line.strip() for line in collection.stdout.splitlines() if line.startswith('tests/') and '::' in line]
env = dict(os.environ, FIRIK_CAPTURE_VOICE_UI='/private/tmp/firik-assistant-preview')
started = time.monotonic()
results = []
with Path('/private/tmp/evren-assistant-isolated.log').open('w') as log:
    for index, node in enumerate(nodes, 1):
        result = subprocess.run([sys.executable, '-m', 'pytest', '-q', node], cwd=root, env=env, text=True, capture_output=True)
        log.write(node + '\n' + result.stdout + result.stderr + '\n')
        log.flush()
        results.append({'test': node, 'exit_code': result.returncode})
        print(f'{index}/{len(nodes)} ' + ('PASS ' if result.returncode == 0 else 'FAIL ') + node, flush=True)
        if result.returncode:
            print(result.stdout + result.stderr, flush=True)
            break
report = {'mode': 'fresh native process per case', 'tests': results, 'passed': sum(row['exit_code'] == 0 for row in results), 'total': len(nodes), 'seconds': round(time.monotonic() - started, 2)}
Path('/private/tmp/evren-assistant-isolated.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
raise SystemExit(0 if len(results) == len(nodes) and all(row['exit_code'] == 0 for row in results) else 1)
