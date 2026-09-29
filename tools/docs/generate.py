"""Refresh configuration schema and the marked operational facts in docs."""
from __future__ import annotations

import argparse
from collections import Counter
import html
import importlib.util
import json
from pathlib import Path
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[2]


def _module(path: Path, name: str = "test_schedule"):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # a dataclass reads its module back while it is built
    spec.loader.exec_module(module)
    return module


def _marks(path: str) -> tuple[str, str]:
    """A generated block's start and end lines, written as comments of the file's syntax."""
    if path.endswith(".toml"):
        return "# generated:{}", "# /generated:{}"
    return "<!-- generated:{} -->", "<!-- /generated:{} -->"


def _block(text: str, name: str, value: str, marks: tuple[str, str] = _marks(".md")) -> str:
    start, end = (mark.format(name) for mark in marks)
    before, rest = text.split(start, 1)
    _, after = rest.split(end, 1)
    return before + start + "\n" + value.rstrip() + "\n" + end + after


def _test_schedule(root: Path) -> str:
    schedule = _module(root / "tools/testing/run.py")
    lines = ["python tools/testing/run.py", *[" ".join(command) for command in schedule.test_commands()]]
    return "```sh\n" + "\n".join(lines) + "\n```"


# --- docs/accuracy.md: the calculations, rulings and conventions tables --------------------

ACCURACY_DOC = "docs/accuracy.md"
RULINGS = ("definition", "defect", "fixed")
RULINGS_HEADER = ("| Row | Calculation | Construct | crapkit | Oracle | Oracle's value | Ruling "
                  "| Support |\n|---|---|---|---|---|---|---|---|")


def _accuracy_kit(root: Path, name: str):
    """A module of tests/accuracy/kit, loaded by path so the docs need no test run."""
    return _module(root / "tests/accuracy/kit" / f"{name}.py", f"docs_accuracy_{name}")


def _cell(text: str) -> str:
    """A markdown table cell: HTML-escaped, pipes escaped, a dash when empty."""
    return html.escape(text, quote=False).replace("|", "\\|") or "-"


def _row(cells) -> str:
    return "| " + " | ".join(cells) + " |"


def _counts(rows, key) -> Counter:
    return Counter((key(row), row.ruling) for row in rows)


def _packet(row) -> str:
    return Path(row.source).parent.name


def _calc_line(calc, counts: Counter) -> str:
    test = calc.independent_test.removeprefix(f"tests/accuracy/{calc.packet}/")
    return _row([_cell(calc.calc), f"`{calc.packet}`", f"`{test}`",
                 *(str(counts[calc.calc, ruling]) for ruling in RULINGS)])


def _calcs_table(calcs: list, rulings: list) -> str:
    """One line per calcs.tsv row: its packet, its independent test and its rulings."""
    counts = _counts(rulings, lambda row: row.calc)
    lines = ["| Calculation | Packet | Independent test | Definitions | Open defects | Fixed |",
             "|---|---|---|---:|---:|---:|"]
    return "\n".join(lines + [_calc_line(calc, counts) for calc in calcs])


def _ruling_line(row) -> str:
    return _row(map(_cell, (row.id, row.calc, row.construct, row.crapkit_value, row.oracle,
                            row.oracle_value, row.ruling, row.outside_support)))


def _packet_rulings(packet: str, rulings: list) -> str:
    """One packet's rows, as a table folded under the packet's name."""
    rows = [row for row in rulings if _packet(row) == packet]
    return "\n".join([f"<details><summary><code>{packet}</code>: {len(rows)} rows</summary>", "",
                      RULINGS_HEADER, *map(_ruling_line, rows), "", "</details>"])


def _count_line(packet: str, counts: Counter) -> str:
    return _row([f"`{packet}`", *(str(counts[packet, ruling]) for ruling in RULINGS)])


def _rulings_tables(rulings: list) -> str:
    """A count per packet and ruling, then every row, one folded table per packet."""
    counts = _counts(rulings, _packet)
    packets = sorted({_packet(row) for row in rulings})
    lines = ["| Packet | Definitions | Open defects | Fixed |", "|---|---:|---:|---:|",
             *(_count_line(packet, counts) for packet in packets)]
    folded = [_packet_rulings(packet, rulings) for packet in packets]
    return "\n\n".join(["\n".join(lines), *folded])


CONVENTIONS = f"{ACCURACY_DOC}#conventions"


def _convention_line(row) -> str:
    return _row(map(_cell, (row.id, row.calc, row.construct, row.crapkit_value, row.oracle,
                            row.oracle_value, row.ruling)))


def _conventions_table(rulings: list) -> str:
    """Every row whose outside support is the maintainer's ruling in the page's Conventions."""
    rows = [row for row in rulings if row.outside_support == CONVENTIONS]
    header = ["| Row | Calculation | Construct | crapkit | Oracle | Oracle's value | Ruling |",
              "|---|---|---|---|---|---|---|"]
    return "\n".join(header + list(map(_convention_line, rows)))


def _mutmut_paths(modules: list[str]) -> str:
    return "paths_to_mutate = [\n" + "".join(f'    "{path}",\n' for path in modules) + "]"


def _accuracy_blocks(root: Path) -> list[tuple[str, str, str]]:
    """docs/accuracy.md's generated tables and the mutmut list, both read from the
    calcs.tsv and rulings.tsv tables; none in a tree without the accuracy page."""
    if not (root / ACCURACY_DOC).is_file():
        return []
    accuracy, calcs = root / "tests/accuracy", _accuracy_kit(root, "calcs")
    rows = calcs.load(accuracy)
    rulings = list(_accuracy_kit(root, "rulings").load(accuracy).values())
    return [(ACCURACY_DOC, "calcs", _calcs_table(rows, rulings)),
            (ACCURACY_DOC, "rulings", _rulings_tables(rulings)),
            (ACCURACY_DOC, "conventions", _conventions_table(rulings)),
            ("pyproject.toml", "mutmut-paths", _mutmut_paths(calcs.modules(rows)))]


def _applied(root: Path, blocks: list[tuple[str, str, str]]) -> dict[str, str]:
    """Each file with its blocks replaced in order, so one file can hold several."""
    result: dict[str, str] = {}
    for name, block, value in blocks:
        text = result.get(name) or (root / name).read_text(encoding="utf-8")
        result[name] = _block(text, block, value, _marks(name))
    return result


def generated(root: Path) -> dict[str, str]:
    """Updated complete files, with authored explanations left in place."""
    from crapkit.config_contract import schema

    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    minor = ".".join(version.split(".")[:2])
    support = f"| Version | Supported |\n| --- | --- |\n| {minor}.x | Yes |\n| < {minor} | No. Upgrade. |"
    blocks = [("SECURITY.md", "version-support", support),
              ("CONTRIBUTING.md", "test-schedule", _test_schedule(root)),
              ("AGENTS.md", "test-schedule", _test_schedule(root)), *_accuracy_blocks(root)]
    result = _applied(root, blocks)
    result["crapkit.schema.json"] = json.dumps(schema(), indent=2) + "\n"
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    stale = []
    for name, content in generated(ROOT).items():
        path = ROOT / name
        if path.read_text(encoding="utf-8") != content:
            stale.append(name)
            if not args.check:
                path.write_text(content, encoding="utf-8")
    if stale:
        print("generated files: " + ", ".join(stale))
    return int(args.check and bool(stale))


if __name__ == "__main__":
    raise SystemExit(main())
