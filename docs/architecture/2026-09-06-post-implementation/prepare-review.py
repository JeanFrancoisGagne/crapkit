"""Capture review coverage and ordering separately from the presentation."""
from datetime import datetime, timezone
import json
import gzip
from pathlib import Path

here = Path(__file__).resolve().parent
evidence = here/'crapkit-architecture-rerun-2026-09-06-evidence'
if not evidence.exists():
    evidence = here
inventory_path = evidence/'project-inventory.json'
inventory_text = (inventory_path.read_text(encoding='utf-8') if inventory_path.exists()
                  else gzip.decompress((evidence/'project-inventory.json.gz').read_bytes()).decode('utf-8'))
inventory = json.loads(inventory_text)
analysis = set('analyze _pygdefer lizardcognitive lizardpowershell lizardrust lizardshell lizardtypescript covstream coverage_py coverage_istanbul uncovered score universe keys cache churn churn_cache churn_log coupling coupling_cache dup diffparse gitio repotext'.split())
state = set('store snapshot verify ratchet ratchet_report override merge worklist packet digest report sarif sarifio errors'.split())
owners = {}
for row in inventory['python']:
    path = Path(row['path'])
    if not row['path'].startswith('src/'):
        continue
    owner = 'Analytical review' if path.stem in analysis else 'State review' if path.stem in state else 'Execution review'
    if row['path'] in ('src/crapkit/__init__.py', 'src/crapkit/__main__.py'):
        owner = 'Root review'
    owners[row['path']] = owner
assert len(owners) == 64
coverage = []
table = (evidence/'root-review.md').read_text(encoding='utf-8').split('| Area | Review disposition |', 1)[1].split('## Fresh self-use', 1)[0]
for line in table.splitlines():
    if line.startswith('| ') and '---' not in line:
        area, disposition = line.strip('| ').split(' | ', 1)
        coverage.append({'area': area, 'disposition': disposition})
order = ['EX1','EX3','AN1','ST4','ST1','RT1','AN2','EX2','ST2','ST5','ST6','RT2','ST3','AN3','AN4','RT5','RT3','RT4']
classes = {key:'Reproduced defect' for key in ('EX1','EX3','AN1','ST4','ST1','RT1','AN2','EX2','ST2','ST5','ST6')}
classes.update({'ST3':'Measured avoidable work','AN3':'Measured scaling cost','AN4':'Measured repeated work','RT2':'Verified CI gap','RT3':'Replayed release plan','RT4':'Verified reference drift','RT5':'Unmeasured optimization'})
data = {'reviewed_commit': inventory['commit'], 'created_at': datetime.now(timezone.utc).isoformat(),
        'review_type': 'Fresh whole-project architecture review; no product changes',
        'candidate_order': order, 'evidence_classes': classes, 'coverage': coverage,
        'module_owners': dict(sorted(owners.items())),
        'inventory': {'tracked_files':647,'production_modules':64,'production_lines':23194,'production_functions':1599,
                      'python_test_support_files':305,'test_support_lines':52272,'test_support_functions':4471,
                      'historical_architecture_files':167},
        'fresh_checks': {'focused_contract_tests':16,'full_suite_rerun':False,'wheel_build_and_extracted_import':'passed',
                         'independent_replays':['analysis cache','ratchet concurrent writes','override pruning','packet age','empty marks query','trend snapshot']}}
(here/'review-data.json').write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
log = here/'decisions.tsv'
with log.open('a',encoding='utf-8',newline='') as stream:
    if stream.tell() == 0:
        stream.write('ts\tphase\tdecision\twhy\tevidence\tresult\n')
    rows = [
        ('scope','Recorded fresh whole-project baseline','User asked for another complete review','499d9db4f9ff4d212fb94ea3975c7477b6b1c968;64 production modules','source unchanged'),
        ('admission','Promoted complete configuration admission','Malformed collections can remove the corpus','root-evidence/config-shapes.json;5 schema/runtime mismatches','reproduced'),
        ('proof','Independently replayed cache and state findings','Separate reader checks the demonstrated interleavings','root-evidence/independent-replay.json;cache hit1;marks10to20;trend0versus1','reproduced'),
        ('validation','Built a wheel in a disposable archive','Check distribution contents without changing editable installation','root-evidence/wheel-proof.json;build0;import0','passed with existing dependencies'),
        ('tests','Kept whole-suite testing out of this review','No production test or configuration edit was made','root-review.md;16 focused contract tests passed','limited checks complete'),
        ('ranking','Prioritized false verdicts and shared-state correctness','Metric counts do not expose these ownership failures','execution-reuse-result.json;reuse0;fresh8','EX1 first'),
        ('design','Retained useful existing modules','Deletion must reduce the knowledge callers need','analysis-review.md;state-review.md;root-review.md','no size-only splits')]
    for row in rows:
        cells = [data['created_at'],*row]
        stream.write('\t'.join(str(c).replace('\t',' ').replace('\n',' ') for c in cells)+'\n')
print(json.dumps({'module_owners':len(owners),'review_areas':len(coverage),'candidates_expected':len(order)}))
