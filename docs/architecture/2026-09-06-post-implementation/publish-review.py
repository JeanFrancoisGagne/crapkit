"""Copy the review source and produce a checked local evidence bundle."""
import hashlib
import gzip
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import sys
import zipfile

NAME = 'crapkit-architecture-rerun-2026-09-06'
here = Path(__file__).resolve().parent
repo = Path(sys.argv[1]).resolve()
outputs = Path(sys.argv[2]).resolve()
target = repo/'docs/architecture/2026-09-06-post-implementation'
target.mkdir(parents=True,exist_ok=True)
evidence = target/(NAME+'-evidence')
evidence.mkdir(exist_ok=True)
root_files = {'build-review.py','prepare-review.py','publish-review.py','review-data.json','decisions.tsv',
              'root-candidates.json','analysis-candidates.json','state-candidates.json','execution-candidates.json',
              NAME+'.json',NAME+'.html'}
for path in here.iterdir():
    if path.is_file() and path.name in root_files:
        shutil.copyfile(path,target/path.name)
    elif path.name == 'project-inventory.json':
        (evidence/'project-inventory.json.gz').write_bytes(gzip.compress(path.read_bytes(),mtime=0))
    elif path.is_file() and path.suffix in ('.py','.json','.txt','.md') and path.name != 'inventory-summary.json':
        shutil.copyfile(path,evidence/path.name)
shutil.copytree(here/'root-evidence',evidence/'root-evidence',dirs_exist_ok=True)
(target/'.gitattributes').write_text('* -text whitespace=cr-at-eol\n',encoding='utf-8')
(target/'README.md').write_text('''# Fresh whole-project architecture review

Reviewed commit: `499d9db4f9ff4d212fb94ea3975c7477b6b1c968`.

Open `crapkit-architecture-rerun-2026-09-06.html` for the ranked visual review. It contains 18 fresh candidates and an 18-area coverage map. No product changes were made in this review.

`build-review.py` renders the four candidate inputs and `review-data.json`; run it from this directory to regenerate HTML and combined JSON. `prepare-review.py` reconstructs inventory/coverage metadata and appends decision checkpoints, so use it only when intentionally refreshing those inputs.

Raw review notes, disposable reproduction scripts and measured output live in `crapkit-architecture-rerun-2026-09-06-evidence/`. Script commands in candidate records name the original Windows workspace. Bind `PYTHONPATH` to the reviewed checkout's `src`, use the matching Python interpreter, and run the named script from the evidence folder. Several probes assert the original checkout path; adjust that assertion only when replaying the same reviewed source elsewhere. Do not point them at an unverified editable installation.

The scripts use disposable repositories, local subprocesses or in-memory execution adapters. Release-plan probes execute no remote publication. Synthetic measurements and controlled interleavings prove the recorded case, not its prevalence.

The HTML's Tailwind and Mermaid scripts load from their public CDNs. CSS module diagrams, candidate text, evidence and tables remain readable without those scripts. The prior implementation review remains in `../2026-09-06/`.

`manifest.json` binds the files included in the local archive. It intentionally excludes itself and the ZIP. Source locations in candidates refer to the reviewed commit; the report's own commit contains documentation and evidence only.
''',encoding='utf-8')

class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links=[]
        self.ids=[]
        self.articles=0
    def handle_starttag(self,tag,attrs):
        data=dict(attrs)
        if tag=='a' and 'href' in data:self.links.append(data['href'])
        if 'id' in data:self.ids.append(data['id'])
        if tag=='article':self.articles+=1

parser=Links()
parser.feed((target/(NAME+'.html')).read_text(encoding='utf-8'))
missing=[]
for link in parser.links:
    if link.startswith('#'):
        if link[1:] not in parser.ids:missing.append(link)
    elif not link.startswith(('https:','http:')) and not link.endswith('-evidence.zip'):
        if not (target/link).is_file():missing.append(link)
assert not missing,missing
assert parser.articles==18 and len(parser.ids)==len(set(parser.ids))
manifest={}
for path in sorted(target.rglob('*')):
    if path.is_file() and path.name!='manifest.json' and path.suffix!='.zip':
        manifest[path.relative_to(target).as_posix()]={'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
(target/'manifest.json').write_text(json.dumps({'reviewed_commit':'499d9db4f9ff4d212fb94ea3975c7477b6b1c968','files':manifest},indent=2)+'\n',encoding='utf-8')
outputs.mkdir(parents=True,exist_ok=True)
for suffix in ('.html','.json'):
    shutil.copyfile(target/(NAME+suffix),outputs/(NAME+suffix))
shutil.copytree(evidence,outputs/evidence.name,dirs_exist_ok=True)
archive=outputs/(NAME+'-evidence.zip')
with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
    for path in sorted(target.rglob('*')):
        if path.is_file():z.write(path,path.relative_to(target).as_posix())
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
    for name,record in manifest.items():
        assert hashlib.sha256(z.read(name)).hexdigest()==record['sha256'],name
print(json.dumps({'source':str(target),'html':str(outputs/(NAME+'.html')),'archive':str(archive),
                  'bound_files':len(manifest),'articles':parser.articles,'broken_links':len(missing)},indent=2))
