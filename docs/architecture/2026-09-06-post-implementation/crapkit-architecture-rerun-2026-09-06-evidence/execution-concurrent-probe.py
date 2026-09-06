"""Force two real CLI coverage runs to overlap around their shared artifact paths."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import crapkit

ROOT = Path(r'C:\Users\jfgag\crapkit')
assert Path(crapkit.__file__).resolve() == ROOT / 'src/crapkit/__init__.py'
print('VERIFIED_IMPORT', crapkit.__file__, flush=True)
CLI = ("import crapkit; from pathlib import Path; "
       f"assert Path(crapkit.__file__).resolve()==Path({str(ROOT / 'src/crapkit/__init__.py')!r}); "
       "from crapkit.cli import main; raise SystemExit(main())")
RUNNER = r"""
import json,os,time
from pathlib import Path
folder=Path('.crapkit')
folder.mkdir(exist_ok=True)
case=os.environ['CASE']
hit=int(case=='A')
loc={'start':{'line':1,'column':0},'end':{'line':3,'column':1}}
data={'src/app.js':{'path':'src/app.js','fnMap':{'0':{'name':'f','decl':loc,'loc':loc}},
     'f':{'0':hit},'statementMap':{'0':loc},'s':{'0':hit},'branchMap':{},'b':{}}}
(folder/'cov.json').write_text(json.dumps(data))
(folder/'junit.xml').write_text('<testsuites><testsuite tests="1"><testcase name="test"/></testsuite></testsuites>')
(folder/(case+'-measured.json')).write_text(json.dumps({'case':case,'expected_crap_load':1 if hit else 2,'hits':hit}))
(folder/(case+'-ready')).write_text('ready')
if case=='A':
    deadline=time.monotonic()+10
    while not (folder/'B-ready').exists():
        if time.monotonic()>deadline:
            raise SystemExit('B did not start')
        time.sleep(.01)
"""
def start(root, case):
    return subprocess.Popen([sys.executable,'-c',CLI,'coverage','--repo',str(root),'--json'],
        cwd=root,env={**os.environ,'CASE':case,'PYTHONPATH':str(ROOT/'src'),'PYTHONDONTWRITEBYTECODE':'1'},
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')

with tempfile.TemporaryDirectory(prefix='parallel-coverage-',dir=Path(__file__).parent) as directory:
    root=Path(directory)
    subprocess.run(['git','init','--quiet'],cwd=root,check=True)
    (root/'src').mkdir()
    (root/'src/app.js').write_text('function f() {\n  return 1;\n}\n')
    (root/'.gitignore').write_text('.crapkit/\n')
    (root/'measure.py').write_text(RUNNER)
    (root/'crapkit.toml').write_text('[[scope]]\nname="src"\npaths=["src"]\nlanguages=["javascript"]\n'
        '[[lane]]\nname="js"\nparser="istanbul"\nscopes=["src"]\ncommand='+json.dumps(f'"{sys.executable}" measure.py')+
        '\nartifact=".crapkit/cov.json"\nresults_artifact=".crapkit/junit.xml"\n')
    subprocess.run(['git','add','.'],cwd=root,check=True)
    subprocess.run(['git','-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--quiet','-m','fixture'],
                   cwd=root,check=True)
    a=start(root,'A')
    deadline=time.monotonic()+10
    while not (root/'.crapkit/A-ready').exists():
        assert time.monotonic()<deadline,'A did not start'
        time.sleep(.01)
    b=start(root,'B')
    stdout_a,stderr_a=a.communicate(timeout=15)
    stdout_b,stderr_b=b.communicate(timeout=15)
    measured_a=json.loads((root/'.crapkit/A-measured.json').read_text())
    measured_b=json.loads((root/'.crapkit/B-measured.json').read_text())
    report_a=json.loads(stdout_a)
    report_b=json.loads(stdout_b)
    output={'A':{'exit':a.returncode,'own_measurement':measured_a,'reported_crap_load':report_a['crap_load'],
                 'artifact_sha256':report_a['lanes']['js']['artifact_sha256'],'stderr':stderr_a},
            'B':{'exit':b.returncode,'own_measurement':measured_b,'reported_crap_load':report_b['crap_load'],
                 'artifact_sha256':report_b['lanes']['js']['artifact_sha256'],'stderr':stderr_b}}
    assert a.returncode==b.returncode==0,output
    assert report_a['crap_load']==report_b['crap_load']==2,output
    assert measured_a['expected_crap_load']==1,output
    assert output['A']['artifact_sha256']==output['B']['artifact_sha256'],output
(Path(__file__).parent/'execution-concurrent-result.json').write_text(json.dumps(output,indent=2),encoding='utf-8')
print(json.dumps(output,indent=2))
