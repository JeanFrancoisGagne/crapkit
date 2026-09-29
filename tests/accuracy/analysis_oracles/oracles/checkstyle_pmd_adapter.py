"""Checkstyle 14.1.0 and PMD 7 CognitiveComplexity, read per report line.

checkstyle() runs one process over the whole file list with
CyclomaticComplexity, NestedIfDepth, NestedForDepth and NestedTryDepth at
max 0, so every method prints its complexity and every nested if, for or try
prints its depth: `[WARN] <file>:<line>:<col>: <message> [<Check>]`.
Cyclomatic Complexity is reported on a method's first line (its first
annotation or modifier); a depth on the nested statement's line.

pmd_cognitive() runs `pmd check` once with CognitiveComplexity at
reportLevel 1, so every method or constructor scoring 1 or more prints
`<file>:<line>:\tCognitiveComplexity:\tThe ... has a cognitive complexity of
N, ...` on its first line. PMD exits 4 when it reports a violation and 5 on a
processing error.

Both answer with paths relative to root. The join to crapkit's rows lives in
test_java_oracles.py. No crapkit import.
"""
from __future__ import annotations

from pathlib import Path
import re

import hang_guard

CHECKSTYLE_CONFIG = """<?xml version="1.0"?>
<!DOCTYPE module PUBLIC "-//Checkstyle//DTD Checkstyle Configuration 1.3//EN"
  "https://checkstyle.org/dtds/configuration_1_3.dtd">
<module name="Checker"><property name="severity" value="warning"/>
<module name="TreeWalker">
<module name="CyclomaticComplexity"><property name="max" value="0"/>
<property name="switchBlockAsSingleDecisionPoint" value="false"/></module>
<module name="NestedIfDepth"><property name="max" value="0"/></module>
<module name="NestedForDepth"><property name="max" value="0"/></module>
<module name="NestedTryDepth"><property name="max" value="0"/></module>
</module></module>
"""
PMD_RULESET = """<?xml version="1.0"?>
<ruleset name="cognitive" xmlns="http://pmd.sourceforge.net/ruleset/2.0.0"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xsi:schemaLocation="http://pmd.sourceforge.net/ruleset/2.0.0
  https://pmd.sourceforge.io/ruleset_2_0_0.xsd">
<description>cognitive complexity of every method</description>
<rule ref="category/java/design.xml/CognitiveComplexity">
<properties><property name="reportLevel" value="1"/></properties></rule>
</ruleset>
"""
WARN = re.compile(r"^\[WARN\] (.+?):(\d+):\d+: (.+) \[(\w+)\]$")
NUMBER = re.compile(r" is (\d+) ")
PMD_LINE = re.compile(r"^(.+?):(\d+):\tCognitiveComplexity:\t.* cognitive complexity of (\d+),")
DEPTH_CHECKS = {"NestedIfDepth": "if", "NestedForDepth": "for", "NestedTryDepth": "try"}


def _relative(root: Path, path: str) -> str:
    """A report's path, relative or absolute, relative to root."""
    return (root / path).resolve().relative_to(root.resolve()).as_posix()


def checkstyle(root: Path, paths: list) -> list:
    """(path, line, check, number) for every report."""
    config = root / "checkstyle-accuracy.xml"
    config.write_text(CHECKSTYLE_CONFIG, encoding="utf-8")
    done = hang_guard.run(["checkstyle", "-c", str(config), *paths], cwd=root, text=True,
                          encoding="utf-8", errors="replace")
    assert "Audit done." in done.stdout, done.stdout[-2000:] + done.stderr
    reports = [WARN.match(line) for line in done.stdout.splitlines()]
    return [(_relative(root, m[1]), int(m[2]), m[4], int(NUMBER.search(m[3])[1]))
            for m in reports if m]


def pmd_cognitive(root: Path, paths: list) -> dict:
    """{(path, line): cognitive} for every method or constructor scoring 1 or more."""
    ruleset, listing = root / "pmd-accuracy.xml", root / "pmd-files.txt"
    ruleset.write_text(PMD_RULESET, encoding="utf-8")
    listing.write_text("\n".join(paths) + "\n", encoding="utf-8")
    done = hang_guard.run(["pmd", "check", "--no-cache", "--no-progress", "-f", "text",
                           "-R", str(ruleset), "--file-list", str(listing)], cwd=root, text=True,
                          encoding="utf-8", errors="replace")
    assert done.returncode in (0, 4), done.stdout[-2000:] + done.stderr
    reports = [PMD_LINE.match(line) for line in done.stdout.splitlines()]
    return {(_relative(root, m[1]), int(m[2])): int(m[3]) for m in reports if m}
