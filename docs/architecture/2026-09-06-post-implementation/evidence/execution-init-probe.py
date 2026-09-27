"""Compare root-scope and named-prefix ownership through init on temporary repos."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import crapkit
from crapkit.config import load_config_text
from crapkit.universe import assign_files

ROOT = Path(r'C:\Users\jfgag\crapkit')
assert Path(crapkit.__file__).resolve() == ROOT / 'src/crapkit/__init__.py'
print('VERIFIED_IMPORT', crapkit.__file__, flush=True)
CLI = ("import crapkit; from pathlib import Path; "
       f"assert Path(crapkit.__file__).resolve()==Path({str(ROOT / 'src/crapkit/__init__.py')!r}); "
       "from crapkit.cli import main; raise SystemExit(main())")

def run_case(scope_path):
    with tempfile.TemporaryDirectory(prefix='init-scope-', dir=Path(__file__).parent) as folder:
        root = Path(folder)
        subprocess.run(['git','init','--quiet'],cwd=root,check=True)
        child = root/'child'
        (child/'src').mkdir(parents=True)
        (child/'src/app.py').write_text('def f():\n    return 1\n')
        config = ('[[scope]]\nname="all"\npaths=['+json.dumps(scope_path)+']\nlanguages=["python"]\n')
        (root/'crapkit.toml').write_text(config)
        subprocess.run(['git','add','.'],cwd=root,check=True)
        admitted = assign_files(['child/src/app.py'], load_config_text(config))['all']
        proc = subprocess.run([sys.executable,'-c',CLI,'init','--repo',str(child)],
                              cwd=root, env={**os.environ,'PYTHONPATH':str(ROOT/'src'),'PYTHONDONTWRITEBYTECODE':'1'},
                              capture_output=True,text=True,encoding='utf-8',timeout=20)
        return {'ancestor_scope_path':scope_path,'parent_inventory_admits':admitted,
                'init_returncode':proc.returncode,'wrote_nested_config':(child/'crapkit.toml').exists(),
                'stdout':proc.stdout,'stderr':proc.stderr}

output=[run_case('.'),run_case('child')]
assert output[0]['parent_inventory_admits']==output[1]['parent_inventory_admits']==['child/src/app.py']
assert output[0]['init_returncode']==0 and output[0]['wrote_nested_config'],output[0]
assert output[1]['init_returncode']==3 and not output[1]['wrote_nested_config'],output[1]
(Path(__file__).parent/'execution-init-result.json').write_text(json.dumps(output,indent=2),encoding='utf-8')
print(json.dumps(output,indent=2))
