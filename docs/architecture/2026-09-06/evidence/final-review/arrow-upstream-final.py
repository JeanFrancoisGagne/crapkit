"""Replay inspected upstream assertions using a local reader instance extension."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest

import lizard
import crapkit
assert Path(crapkit.__file__).resolve() == Path(__file__).resolve().parents[5] / 'src/crapkit/__init__.py'
from crapkit.lizardtypescript import LizardExtension

HERE = Path(__file__).parent


def summary(functions):
    fields = ("name", "long_name", "start_line", "end_line", "nloc", "token_count",
              "cyclomatic_complexity", "parameter_count")
    return [{key: getattr(fn, key) for key in fields} for fn in functions]


all_results = []
for language, suffix in (("TypeScript", "ts"), ("JavaScript", "js"), ("TSX", "tsx")):
    spec = importlib.util.spec_from_file_location("upstream_tests", HERE / f"upstream-test{language}.py")
    upstream = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(upstream)
    helper = f"get_{suffix}_function_list"
    original = getattr(upstream, helper)
    sources = []

    def record(source):
        sources.append(source)
        return original(source)

    def corrected(source):
        extensions = [LizardExtension(), *lizard.get_extensions([])]
        return lizard.FileAnalyzer(extensions).analyze_source_code(f"a.{suffix}", source).function_list

    print(f"{language} STOCK", flush=True)
    setattr(upstream, helper, record)
    stock = unittest.TextTestRunner(stream=sys.stdout, verbosity=1).run(
        unittest.defaultTestLoader.loadTestsFromModule(upstream))
    print(f"{language} LOCAL EXTENSION", flush=True)
    setattr(upstream, helper, corrected)
    result = unittest.TextTestRunner(stream=sys.stdout, verbosity=1).run(
        unittest.defaultTestLoader.loadTestsFromModule(upstream))
    changes = [{"source": source, "before": summary(original(source)), "after": summary(corrected(source))}
               for source in sources if summary(original(source)) != summary(corrected(source))]
    all_results.append({"language": language, "tests": result.testsRun,
                        "stock_ok": stock.wasSuccessful(), "local_ok": result.wasSuccessful(),
                        "skipped": len(result.skipped), "parser_fixtures": len(sources), "changes": changes})
    print(f"RECORD CHANGES {len(changes)} / {len(sources)} parser fixtures", flush=True)
(HERE / "arrow-upstream-rerun.json").write_text(json.dumps(all_results, indent=2), encoding="utf-8")
sys.exit(not all(result["local_ok"] for result in all_results))
