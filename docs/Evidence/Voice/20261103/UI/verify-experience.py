"""Isolate every synthetic native voice UX case in a fresh Tk process."""
from pathlib import Path
import os
import subprocess
import sys

root=Path(__file__).resolve().parents[5]
output=Path(__file__).resolve().parent
collection=subprocess.run([sys.executable,'-m','pytest','--collect-only','-q','tests/test_ui_voice_experience.py'],cwd=root,text=True,capture_output=True)
if collection.returncode:
    print(collection.stdout+collection.stderr)
    raise SystemExit(collection.returncode)
nodes=[line for line in collection.stdout.splitlines() if line.startswith('tests/') and '::' in line]
failed=[]
with (output/'experience-native-tests.log').open('w') as log:
    for i,node in enumerate(nodes,1):
        env=dict(os.environ,FIRIK_CAPTURE_VOICE_UI=str(output))
        try:
            result=subprocess.run([sys.executable,'-m','pytest','-q',node],cwd=root,text=True,capture_output=True,timeout=60,env=env)
            line=f'{i}/{len(nodes)} {node}: {result.returncode}'
            log.write(line+'\n'+result.stdout+result.stderr+'\n')
            if result.returncode:
                failed.append(node)
        except subprocess.TimeoutExpired:
            line=f'{i}/{len(nodes)} {node}: TIMEOUT'
            failed.append(node)
            log.write(line+'\n')
        log.flush()
        print(line,flush=True)
    log.write(f'Cases: {len(nodes)}, failures: {len(failed)}\n')
print(f'Cases: {len(nodes)}, failures: {len(failed)}',flush=True)
raise SystemExit(bool(failed))
