from pathlib import Path
from tempfile import TemporaryDirectory
import crapkit
assert Path(crapkit.__file__).resolve() == Path(__file__).resolve().parents[5] / 'src/crapkit/__init__.py'
from crapkit.analyze import analyze_files
from crapkit.errors import ToolError

print('VERIFIED_IMPORT', crapkit.__file__)
source = 'const f = [x => x < 0, x => x + 1];\n'
with TemporaryDirectory(prefix='crapkit-reader-cache-final-') as temporary:
    root = Path(temporary)
    (root / 'case.jsx').write_text(source, encoding='utf-8')
    (root / 'case.tsx').write_text(source, encoding='utf-8')
    jsx, hits, cache = analyze_files(root, ['case.jsx'], cache={})
    print('COLD_JSX', hits, len(jsx['case.jsx']))
    try:
        analyze_files(root, ['case.tsx'], cache={})
    except ToolError as exc:
        print('COLD_TSX_REFUSAL', str(exc))
    tsx, hits, _ = analyze_files(root, ['case.tsx'], cache=cache)
    print('WARM_TSX_ACCEPTED', hits, len(tsx['case.tsx']), [r.path for r in tsx['case.tsx']])
