"""Disposable replays for the independent analytical architecture review."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from time import perf_counter
import json
import io
import statistics

import crapkit
from crapkit import analyze, covstream

ROOT = Path(r"C:\Users\jfgag\crapkit")
assert Path(crapkit.__file__).resolve() == ROOT / "src/crapkit/__init__.py"


def cache_race():
    source_a = "def f(x):\n    return x\n"
    source_b = "def f(x):\n    if x:\n        return 1\n    return 0\n"
    with TemporaryDirectory() as directory:
        root = Path(directory)
        path = root / "m.py"
        path.write_text(source_a, encoding="utf-8")
        original_jobs = analyze.analyze_jobs

        def write_between_reads(*args, **kwargs):
            path.write_text(source_b, encoding="utf-8")
            return original_jobs(*args, **kwargs)

        with patch.object(analyze, "analyze_jobs", write_between_reads):
            first, _, cache = analyze.analyze_files(root, ["m.py"], cache={}, workers=1)
        path.write_text(source_a, encoding="utf-8")
        second, hits, _ = analyze.analyze_files(root, ["m.py"], cache=cache, workers=1)
        expected = analyze.analyze_source("m.py", source_a)
        return {"first_records": [list(row) for row in first["m.py"]],
                "second_records": [list(row) for row in second["m.py"]],
                "expected_records": [list(row) for row in expected],
                "second_cache_hits": hits,
                "wrong_metrics": second["m.py"] != expected}


def cold_duplicates():
    source = "\n".join(f"def f{i}(x):\n    return 1 if x else 0\n" for i in range(100))
    with TemporaryDirectory() as directory:
        root = Path(directory)
        paths = [f"m{i}.py" for i in range(12)]
        for path in paths:
            (root / path).write_text(source, encoding="utf-8")
        with patch.object(analyze, "analyze_one", wraps=analyze.analyze_one) as observed:
            started = perf_counter()
            records, _, cache = analyze.analyze_files(root, paths, cache={}, workers=1)
            elapsed = perf_counter() - started
            calls = observed.call_count
        started = perf_counter()
        warm, hits, _ = analyze.analyze_files(root, paths, cache=cache, workers=1)
        return {"files": len(paths), "function_records": sum(map(len, records.values())),
                "cache_entries": len(cache["entries"]), "cold_parser_calls": calls,
                "cold_seconds": elapsed, "warm_seconds": perf_counter() - started,
                "warm_hits": hits, "outputs_equal": records == warm}


def deleted_between_reads():
    source = "def f(x):\n    if x:\n        return 1\n    return 0\n"
    with TemporaryDirectory() as directory:
        root = Path(directory)
        path = root / "m.py"
        path.write_text(source, encoding="utf-8")
        original_jobs = analyze.analyze_jobs

        def delete_after_hash(*args, **kwargs):
            path.unlink()
            return original_jobs(*args, **kwargs)

        with patch.object(analyze, "analyze_jobs", delete_after_hash):
            first, _, cache = analyze.analyze_files(root, ["m.py"], cache={}, workers=1)
        cache_path = root / '.crapkit/cache.json'
        analyze.save_cache(cache_path, cache)
        path.write_text(source, encoding="utf-8")
        second, hits, _ = analyze.analyze_files(root, ["m.py"], cache=analyze.load_cache(cache_path), workers=1)
        return {"first_function_count": len(first['m.py']), "restored_function_count": len(second['m.py']),
                "expected_function_count": len(analyze.analyze_source('m.py', source)), "cache_hits": hits}


class CountingDecoder:
    def __init__(self, decoder):
        self.decoder = decoder
        self.calls = 0
        self.characters_offered = 0

    def raw_decode(self, text, start=0):
        self.calls += 1
        self.characters_offered += len(text) - start
        return self.decoder.raw_decode(text, start)


def member_scaling():
    results = []
    for mib in (1, 2, 4, 8, 16):
        raw = json.dumps({"large.py": {"contexts": ["x" * 100] * (mib * 1048576 // 104)}}).encode()
        samples = []
        for _ in range(3):
            decoder = CountingDecoder(covstream._DECODER)
            started = perf_counter()
            with patch.object(covstream, "_DECODER", decoder):
                window = covstream._Window(io.BytesIO(raw))
                output = list(covstream.split_window(window))
            samples.append(perf_counter() - started)
        started = perf_counter()
        whole = json.loads(raw)
        whole_seconds = perf_counter() - started
        results.append({"bytes": len(raw), "decode_attempts": decoder.calls,
                        "characters_offered": decoder.characters_offered,
                        "seconds": samples, "median_seconds": statistics.median(samples),
                        "whole_decode_seconds": whole_seconds,
                        "outputs_equal": dict(output) == whole})
    return results


def public_member_scaling():
    results = []
    with TemporaryDirectory() as directory:
        path = Path(directory) / 'coverage.json'
        for mib in (1, 4, 16):
            report = {"meta": {"branch_coverage": True}, "files": {"large.py": {
                "functions": {"f": {"start_line": 1, "executed_lines": [1, 2], "missing_lines": [],
                                     "summary": {"num_branches": 2, "covered_branches": 2,
                                                 "num_statements": 2, "covered_lines": 2}}},
                "missing_lines": [], "contexts": {"2": ["test_" + "x" * 95] * (mib * 1048576 // 104)}}}}
            raw = json.dumps(report).encode()
            path.write_bytes(raw)
            samples = []
            for _ in range(3):
                started = perf_counter()
                output = covstream.parse_coveragepy_both_file(path, path_prefix='')
                samples.append(perf_counter() - started)
            started = perf_counter()
            single = covstream.parse_coveragepy_both_file(path, path_prefix='', chunk=len(raw) + 1)
            results.append({"bytes": len(raw), "seconds": samples, "median_seconds": statistics.median(samples),
                            "one_chunk_seconds": perf_counter() - started, "outputs_and_digest_equal": output == single})
    return results


if __name__ == "__main__":
    print(json.dumps({"source": str(crapkit.__file__), "cache_race": cache_race(),
                      "deleted_between_reads": deleted_between_reads(),
                      "cold_duplicates": cold_duplicates(), "member_scaling": member_scaling(),
                      "public_member_scaling": public_member_scaling()}, indent=2))
