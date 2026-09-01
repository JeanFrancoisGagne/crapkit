"""One static HTML page for a scored run: the ranked worklist, the per-scope
grades, the trend series, and a banner when the artifacts behind them are stale.

The whole module is a pure function from two payloads the CLI already answers to
a string. It measures nothing and reads no file, so the page can never say
something `crapkit worklist --json` and `crapkit trend --json` do not.

Three decisions are load-bearing.

The page renders the worklist AT ITS DEFAULT, `worklist_top` rows. Rendering
every admitted row was measured: 46,567 rows of openclaw came to 9.85 MB of HTML
and 46,567 DOM rows, which is a page that hangs the tab rather than one a
teammate opens. `report_top` refuses anything past REPORT_ROW_CEILING.

There is no per-function drill-down, because no --json surface carries
per-function CRAP or coverage repo-wide: a worklist row has `risk` and `weight`,
not `crap` and `cov`. Each row prints `crapkit explain PATH NAME` instead, so the
detail is one command away and the file stays in the tens of KB.

Every value that reaches the page goes through `_esc`. A function name is source
text, not markup: `read_chunk( path , max_bytes = 1 < < 20 )` is a real recorded
signature, and unescaped its `<` opens a tag that swallows the rest of the row.

The palette is the handbook's, copied verbatim so the tool has one look; the
contract test compares the two.
"""
from __future__ import annotations

from html import escape

from .errors import ConfigError
from .invocation import _self

# Measured at 46,567 rows / 9.85 MB. A few thousand rows is already a page
# nobody scrolls; past that it is a page nobody opens.
REPORT_ROW_CEILING = 2000

_CHART_W = 660
_CHART_H = 130

_WORKLIST_COLUMNS = ("Risk", "CCN", "nloc", "Churn", "Scope", "Function",
                     "Verdict", "Drill down")
_SCOPE_COLUMNS = ("Scope", "Functions", "Over target", "CRAP load", "Grade")
_TREND_COLUMNS = ("Run", "Commit", "When", "Functions", "Over target",
                  "CRAP load", "Average")

_EMPTY_RUN = {"run_id": 0, "commit": "", "created_at": "", "functions": 0,
              "over_target": 0, "crap_load": 0.0, "avg": 0.0, "by_scope": {}}


def report_top(worklist_top: int) -> int:
    """The row cap, or a refusal naming the knob that set it.

    A refusal beats a 10 MB page: the reader can lower `worklist_top` and get a
    page, and cannot un-hang a tab.
    """
    if worklist_top > REPORT_ROW_CEILING:
        raise ConfigError(
            f"report renders at most {REPORT_ROW_CEILING} rows and worklist_top is "
            f"{worklist_top}: lower [crapkit] worklist_top, or read the whole "
            f"ranking with `{_self()} worklist --json`")
    return worklist_top


def render_report(payload: dict) -> str:
    """The whole page.

    `payload` carries `repo`, `generated_at`, `target`, `lanes` (name and the
    note saying why its artifact cannot name line numbers, "" when it can), and
    the `worklist` and `trend` payloads verbatim.
    """
    body = [_header(payload), _banner(payload), _scopes(payload),
            _worklist(payload), _trend(payload), _footer(payload)]
    return (f"{_DOC_OPEN}<title>crapkit report: {_esc(payload['repo'])}</title>\n"
            f"<style>{_STYLE}</style>\n</head>\n<body>\n<div class=\"shell\">\n"
            + "\n".join(body) + "\n</div>\n</body>\n</html>\n")


def _esc(value) -> str:
    """Every value on the page passes through here. Paths, signatures, scope
    names and lane notes are all source text somebody else wrote."""
    return escape(str(value), quote=True)


# --- the page's parts --------------------------------------------------------

def _header(payload: dict) -> str:
    wl = payload["worklist"]
    return (f'<header class="hero"><p class="kicker">crapkit report &middot; '
            f'self-contained &middot; generated {_esc(payload["generated_at"])}</p>'
            f'<h1>{_esc(payload["repo"])}</h1>'
            f'<p class="standfirst">Run {_esc(wl["run_id"])} at '
            f'<code>{_esc(wl["commit"][:11])}</code>, target ccn {_esc(payload["target"])}. '
            f'Every number here comes from <code>crapkit worklist --json</code> and '
            f'<code>crapkit trend --json</code> at their defaults.</p></header>')


def _footer(payload: dict) -> str:
    wl = payload["worklist"]
    return (f'<footer>Ranked by risk (ccn times recency-weighted churn) over a '
            f'{_esc(wl["churn_window_months"])}-month window, admitted at '
            f'ccn &gt;= {_esc(wl["floor"])}. Per-function CRAP and coverage are not on this '
            f'page: no repo-wide payload carries them. Run the drill-down command on a row '
            f'to get them.</footer>')


# --- the banner --------------------------------------------------------------

def _banner(payload: dict) -> str:
    """Loud when anything makes the numbers below untrustworthy, quiet otherwise.

    Three separate faults read the same on the surface and want the same move,
    so they stack into one block rather than competing for the top of the page.
    """
    lanes = payload["lanes"]
    reasons = (_no_lane_reason(lanes) + _stale_lane_reason(lanes)
               + _behind_head_reason(payload["worklist"]))
    if reasons:
        return ('<div class="banner stale"><p class="shout">Read this before the '
                'numbers below</p>' + "".join(reasons) + "</div>")
    return (f'<div class="banner fresh"><p>All {len(lanes)} lane artifact(s) still '
            f'describe this working tree, and the run sits on HEAD.</p></div>')


def _no_lane_reason(lanes: list[dict]) -> list[str]:
    if lanes:
        return []
    return ["<p>This repo declares no [[lane]], so no artifact can say which lines "
            "any test ran. Coverage reads 0 by default here, not by measurement.</p>"]


def _stale_lane_reason(lanes: list[dict]) -> list[str]:
    """The blackout, stated at its real size.

    A single stale lane makes `load_uncovered` return no line numbers for ANY
    path, not just that lane's. Uncommitted edits count, which is the normal
    state of a tree somebody generates a report from.
    """
    stale = [lane for lane in lanes if lane["note"]]
    if not stale:
        return []
    return [f"<p><b>{len(stale)} of {len(lanes)} lanes are stale.</b> One stale lane "
            "blacks out line-level coverage repo-wide, not just its own scopes. "
            f"Commit or revert the edits, then rerun <code>{_self()} coverage</code>.</p>"
            + _lane_list(stale)]


def _lane_list(stale: list[dict]) -> str:
    items = "".join(f'<li><b>{_esc(lane["name"])}</b>: {_esc(lane["note"])}</li>'
                    for lane in stale)
    return f'<ul class="lanes">{items}</ul>'


def _behind_head_reason(wl: dict) -> list[str]:
    if not wl["stale"]:
        return []
    return [f'<p>The snapshot is run {_esc(wl["run_id"])} at '
            f'<code>{_esc(wl["commit"][:11])}</code> and HEAD has moved on. Fresh '
            f'artifacts do not rescue a run measured at another commit: rerun '
            f'<code>{_self()} coverage</code>.</p>']


# --- per-scope grades --------------------------------------------------------

def _newest_run(trend: dict) -> dict:
    """The run the grades describe. An empty history grades nothing rather than
    raising: a repo with one inventory run still gets a page."""
    runs = trend["runs"]
    return runs[-1] if runs else _EMPTY_RUN


def _scopes(payload: dict) -> str:
    newest = _newest_run(payload["trend"])
    rows = "".join(_scope_row(name, block)
                   for name, block in sorted(newest["by_scope"].items()))
    return _section("scopes", "Grades by scope",
                    _table(_SCOPE_COLUMNS, rows or _empty_row(len(_SCOPE_COLUMNS),
                                                              "no scored run yet")))


def _scope_row(name: str, block: dict) -> str:
    return (f'<tr data-scope="{_esc(name)}"><td class="mono">{_esc(name)}</td>'
            f'<td class="mono">{_esc(block["functions"])}</td>'
            f'<td class="mono">{_esc(block["over_target"])}</td>'
            f'<td class="mono">{_esc(block["crap_load"])}</td>'
            f'<td>{_grade_chip(block["grade"])}</td></tr>')


def _grade_chip(grade: str) -> str:
    return f'<span class="chip {_grade_tone(grade)}">{_esc(grade)}</span>'


def _grade_tone(grade: str) -> str:
    """One letter, one colour. `score.grade` answers A+, A, B, C, D or F: green
    through B, amber at C, red at D and F."""
    if grade.startswith(("A", "B")):
        return "o"
    if grade.startswith("C"):
        return "w"
    return "a"


# --- the worklist ------------------------------------------------------------

def _worklist(payload: dict) -> str:
    wl = payload["worklist"]
    rows = "".join(_worklist_row(entry) for entry in wl["active"])
    table = _table(_WORKLIST_COLUMNS,
                   rows or _empty_row(len(_WORKLIST_COLUMNS), "nothing admitted"))
    return _section("worklist", f'Worklist: {len(wl["active"])} ranked, '
                                f'{_esc(wl["dormant_count"])} dormant', table)


def _worklist_row(entry: dict) -> str:
    """One ranked function. `risk` prints as the payload carries it, so a number
    read off the page and one read off `worklist --json` are the same number."""
    return (f'<tr class="wl"><td class="mono">{_esc(entry["risk"])}</td>'
            f'<td class="mono">{_esc(entry["ccn"])}</td>'
            f'<td class="mono">{_esc(entry["nloc"])}</td>'
            f'<td class="mono">{_esc(entry["commits"])}c/{_esc(entry["authors"])}a</td>'
            f'<td class="mono">{_esc(entry["scope"])}</td>'
            f'<td>{_function_cell(entry)}</td>'
            f'<td>{_verdict_cell(entry)}</td>'
            f'<td><code>{_drill_down(entry)}</code></td></tr>')


def _function_cell(entry: dict) -> str:
    return (f'<div class="fn mono">{_esc(entry["function"])}</div>'
            f'<div class="loc">{_esc(entry["path"])}:{_esc(entry["start"])}</div>')


def _verdict_cell(entry: dict) -> str:
    """`flag` and `remedy` are the run's own verdict, null on an inventory run."""
    chips = [_chip(entry["flag"], _flag_tone(entry["flag"])),
             _chip(entry["remedy"], _remedy_tone(entry["remedy"]))]
    return "".join(c for c in chips if c)


def _chip(text, tone: str) -> str:
    return "" if text is None else f'<span class="chip {tone}">{_esc(text)}</span>'


def _flag_tone(flag) -> str:
    return "i" if flag == "measured" else "w"


def _remedy_tone(remedy) -> str:
    return "o" if remedy == "ok" else "a"


def _drill_down(entry: dict) -> str:
    """The command that answers what this page deliberately does not carry."""
    return _esc(f'{_self()} explain {entry["path"]} "{entry["function"]}"')


# --- the trend series --------------------------------------------------------

def _trend(payload: dict) -> str:
    runs = payload["trend"]["runs"]
    rows = "".join(_trend_row(run) for run in runs)
    table = _table(_TREND_COLUMNS, rows or _empty_row(len(_TREND_COLUMNS), "no scored run yet"))
    return _section("trend", f"Trend: {len(runs)} scored run(s)", _chart(runs) + table)


def _trend_row(run: dict) -> str:
    return (f'<tr data-run="{_esc(run["run_id"])}">'
            f'<td class="mono">{_esc(run["run_id"])}</td>'
            f'<td class="mono">{_esc(run["commit"][:11])}</td>'
            f'<td class="mono">{_esc(run["created_at"])}</td>'
            f'<td class="mono">{_esc(run["functions"])}</td>'
            f'<td class="mono">{_esc(run["over_target"])}</td>'
            f'<td class="mono">{_esc(run["crap_load"])}</td>'
            f'<td class="mono">{_esc(run["avg"])}</td></tr>')


def _chart(runs: list[dict]) -> str:
    """CRAP load per run as one polyline. Two runs is the smallest series with a
    direction; one run is a dot nobody can read a trend off."""
    loads = [run["crap_load"] for run in runs]
    if len(loads) < 2:
        return ""
    points = " ".join(f"{x},{y}" for x, y in _chart_points(loads))
    return (f'<figure class="panel"><svg viewBox="0 0 {_CHART_W} {_CHART_H}" role="img" '
            f'aria-label="CRAP load across {len(loads)} runs">'
            f'<polyline class="series" points="{points}"/></svg>'
            f'<figcaption>CRAP load across {len(loads)} scored runs, oldest at the left. '
            f'Peak {_esc(max(loads))}. Down is better.</figcaption></figure>')


def _chart_points(loads: list[float]) -> list[tuple[float, float]]:
    """Load scaled to the box, oldest first. A flat-zero series draws along the
    floor rather than dividing by nothing."""
    top = max(loads) or 1.0
    step = _CHART_W / (len(loads) - 1)
    return [(round(i * step, 1), round(_CHART_H - 8 - (value / top) * (_CHART_H - 16), 1))
            for i, value in enumerate(loads)]


# --- markup helpers ----------------------------------------------------------

def _section(anchor: str, title: str, body: str) -> str:
    return f'<section id="{anchor}">\n<h2>{title}</h2>\n{body}\n</section>'


def _table(columns: tuple[str, ...], rows: str) -> str:
    head = "".join(f"<th>{column}</th>" for column in columns)
    return (f'<div class="tw"><table><thead><tr>{head}</tr></thead>'
            f"<tbody>{rows}</tbody></table></div>")


def _empty_row(width: int, text: str) -> str:
    return f'<tr><td colspan="{width}" class="empty">{_esc(text)}</td></tr>'


_DOC_OPEN = ('<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
             '<meta name="viewport" content="width=device-width, initial-scale=1">\n')

# Copied from docs/handbook.html so the tool has one look. The contract test
# compares every colour token here against that page.
_STYLE = """
  :root {
    --paper: #FAF8F4; --ink: #211C17; --muted: #6E6459; --faint: #94897C;
    --line: #E5DFD5; --card: #FFFFFF; --card-line: #EAE4DA;
    --accent: #A6391F; --accent-soft: #F7E9E4;
    --ok: #2E7D46; --ok-soft: #E7F2EA;
    --warn: #A66A00; --warn-soft: #F8EFDC;
    --info: #3B5B8C; --info-soft: #E8EEF7;
    --data1: #A6391F; --data2: #B8860B; --data3: #3E6B8F; --data4: #2E7D46;
    --code-bg: #F1EDE5; --grid: #E9E3D8;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --paper: #17140F; --ink: #EAE4DB; --muted: #A79C8D; --faint: #7E7466;
      --line: #322C24; --card: #1F1B15; --card-line: #373128;
      --accent: #E0714F; --accent-soft: #33211B;
      --ok: #6FBF8A; --ok-soft: #1E2E23;
      --warn: #D9A250; --warn-soft: #302716;
      --info: #8AA8D4; --info-soft: #1E2634;
      --data1: #E0714F; --data2: #D9A250; --data3: #7FA3C8; --data4: #6FBF8A;
      --code-bg: #262119; --grid: #2C271F;
    }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--paper); color: var(--ink);
         font: 16px/1.62 system-ui, "Segoe UI", Roboto, "Helvetica Neue", sans-serif; }
  .shell { max-width: 1080px; margin: 0 auto; padding: 40px 24px 90px; }
  code, .mono { font-family: ui-monospace, Consolas, "Cascadia Mono", Menlo, monospace; }
  code { font-size: .85em; background: var(--code-bg); padding: 1px 5px; border-radius: 3px;
         white-space: nowrap; }

  header.hero { padding: 30px 0 24px; border-bottom: 3px solid var(--accent);
                position: relative; overflow: hidden; }
  header.hero::after { content: "ccn2"; position: absolute; right: -10px; top: -30px;
    font: 700 170px/1 Charter, Cambria, Georgia, serif; color: var(--accent);
    opacity: .06; pointer-events: none; user-select: none; }
  .kicker { font: 600 12px/1 system-ui, sans-serif; letter-spacing: .16em;
            text-transform: uppercase; color: var(--accent); margin: 0 0 16px; }
  h1 { font-family: Charter, "Bitstream Charter", Cambria, Georgia, serif;
       font-size: clamp(38px, 6vw, 56px); font-weight: 700; line-height: 1.02;
       letter-spacing: -.015em; margin: 0 0 14px; }
  .standfirst { max-width: 66ch; font-size: 17px; color: var(--muted); margin: 0; }
  h2 { font-family: Charter, "Bitstream Charter", Cambria, Georgia, serif;
       font-size: 28px; font-weight: 700; margin: 56px 0 10px; padding-top: 20px;
       border-top: 1px solid var(--line); }
  p { max-width: 72ch; margin: 0 0 12px; }

  .banner { margin: 24px 0 0; padding: 16px 20px; border-radius: 8px;
            border-left: 5px solid var(--ok); background: var(--ok-soft); }
  .banner p { margin: 0 0 8px; font-size: 14.5px; }
  .banner p:last-child { margin-bottom: 0; }
  .banner.stale { border-left-color: var(--accent); background: var(--accent-soft); }
  .banner.stale .shout { font: 700 15px/1.3 system-ui, sans-serif;
                         text-transform: uppercase; letter-spacing: .05em;
                         color: var(--accent); }
  ul.lanes { margin: 6px 0 0; padding-left: 22px; font-size: 13.5px; color: var(--muted); }
  ul.lanes li { margin-bottom: 5px; }
  ul.lanes b { color: var(--ink); font-family: ui-monospace, Consolas, monospace; }

  .tw { overflow-x: auto; border: 1px solid var(--card-line); border-radius: 8px;
        background: var(--card); margin: 16px 0; }
  table { border-collapse: collapse; width: 100%; min-width: 720px; font-size: 14px; }
  th { font: 700 11px/1.3 system-ui, sans-serif; letter-spacing: .09em;
       text-transform: uppercase; text-align: left; color: var(--muted);
       padding: 11px 14px 9px; border-bottom: 2px solid var(--line); }
  td { padding: 9px 14px; border-bottom: 1px solid var(--line); vertical-align: top; }
  tr:last-child td { border-bottom: none; }
  tbody tr:hover td { background: color-mix(in srgb, var(--accent) 4%, transparent); }
  td.mono { white-space: nowrap; font-size: 13px; }
  td.empty { color: var(--faint); font-style: italic; }
  .fn { font-size: 13px; word-break: break-word; }
  .loc { font-size: 12px; color: var(--faint); font-family: ui-monospace, Consolas, monospace; }

  .chip { display: inline-block; font: 700 10.5px/1 system-ui, sans-serif;
          letter-spacing: .07em; text-transform: uppercase; padding: 4px 8px 3px;
          border-radius: 3px; white-space: nowrap; margin: 0 4px 3px 0; }
  .chip.a { background: var(--accent-soft); color: var(--accent); }
  .chip.o { background: var(--ok-soft); color: var(--ok); }
  .chip.w { background: var(--warn-soft); color: var(--warn); }
  .chip.i { background: var(--info-soft); color: var(--info); }

  figure.panel { margin: 18px 0; background: var(--card); border: 1px solid var(--card-line);
                 border-radius: 10px; padding: 16px 18px 10px; overflow-x: auto; }
  figcaption { font-size: 13px; color: var(--faint); margin-top: 8px; }
  svg { display: block; max-width: 100%; height: auto; }
  svg .series { fill: none; stroke: var(--data1); stroke-width: 2.5;
                stroke-linejoin: round; stroke-linecap: round; }

  footer { margin-top: 60px; padding-top: 20px; border-top: 1px solid var(--line);
           color: var(--faint); font-size: 13.5px; max-width: 76ch; }
  ::selection { background: var(--accent-soft); }
  @media (max-width: 640px) { .shell { padding: 28px 16px 60px; }
    header.hero::after { display: none; } }
  @media print { body { background: #fff; color: #111; }
    header.hero::after { display: none; } .tw, figure { break-inside: avoid; } }
"""
