"""Runs probe.py for the coverage.py reports beside it.

Recorded under CPython 3.11 from this directory, per coverage.py version V,
with --branch for the branch reports and without it for the statement ones:

    coverage run --branch --data-file=.cov --include=probe.py drive.py .
    coverage json --data-file=.cov -o report.json

Each report is then cut down to the fields crapkit reads: meta's format,
version and branch_coverage, the file's missing_lines, and per region its
lines, branches, start_line and summary counts.
"""
import asyncio
import sys

sys.path.insert(0, sys.argv[1])
import probe  # noqa: E402

probe.outer_full(True, True, True)
probe.outer_full(False, False, False)
probe.outer_one(True)
probe.outer_one(False)
probe.outer_multi(True)
probe.two_liners_then_multi(True)
probe.top_called(3)
probe.nested_after_statement(1)
probe.nested_after_statement(0)
probe.decorated_host(True)
probe.decorated_top(True)
probe.Shape().two(True)
probe.class_host(True)
probe.class_host(False)
probe.genexpr_first([1, 5])
probe.outer_deep(1)
asyncio.run(probe.async_one(1))
probe.wrap([1, 2])
probe.wrap_gen([1, 9])
probe.top_one_statement(1)
probe.class_host_first(True)
probe.nested_doc_then_one(True)
probe.deep_one(1)
probe.lambda_first([3, 1])
probe.pass_host(True)
