from pathlib import Path
from tempfile import TemporaryDirectory
import json
import math
import crapkit
assert Path(crapkit.__file__).resolve() == Path(__file__).resolve().parents[5] / 'src/crapkit/__init__.py'
from crapkit import analyze, covstream, score
from crapkit.snapshot import InventoryRow
from crapkit.lizardtypescript import LizardExtension
import lizard

print('VERIFIED_IMPORT', crapkit.__file__)
source = 'const f = [\n (x) => x ? 1 : 2,\n (x) => x && 2\n];\n\nconst g = rows.map((x) => x || 0);\n'
extensions = [extension for extension in analyze._extensions_for('app.ts') if not isinstance(extension, LizardExtension)]
old = lizard.FileAnalyzer(extensions).analyze_source_code('app.ts', source).function_list
fresh = analyze.analyze_source('app.ts', source)
print('STOCK_ARROWS', [(f.long_name, f.start_line, f.end_line, f.cyclomatic_complexity) for f in old])
print('VERSION10_ARROWS', [tuple(f) for f in fresh])
print('VERSION', analyze.ANALYSIS_VERSION, analyze.fingerprint())
for suffix in ('js', 'jsx', 'ts', 'tsx', 'mts', 'cts'):
    print('READER', suffix, analyze._analysis_key('file.' + suffix, 'digest'))
with TemporaryDirectory(prefix='crapkit-final-json-') as temporary:
    path = Path(temporary) / 'coverage.json'
    region = {'start_line': 1, 'executed_lines': [1], 'missing_lines': [],
              'summary': {'covered_lines': 1, 'num_statements': 1, 'num_branches': 2, 'covered_branches': float('nan')}}
    report = {'meta': {'branch_coverage': True}, 'files': {'app.py': {'functions': {'f': region}}}}
    path.write_text(json.dumps(report), encoding='utf-8')
    cov, digest = covstream.parse_coveragepy_file(path, path_prefix='')
    row = InventoryRow('core', 'app.py', 'f', 1, 1, 7, 7, 7, 1, 0, 0, 0, 1)
    result = score.score_rows([row], cov, lane_scopes={'core'})[0]
    print('NONFINITE_COVERAGE', result.cov, result.crap, result.remedy,
          'over_target=', result.crap > 6, 'finite=', math.isfinite(result.crap))
