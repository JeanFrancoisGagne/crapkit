"""Build the final review from the saved ledger, measurements and validation."""
import csv
import html
import json
from pathlib import Path
import sys
from urllib.parse import quote

HERE = Path(__file__).parent
REF = sys.argv[1] if len(sys.argv) > 1 else "architecture/implementation-20260906"
REPO = "https://github.com/JeanFrancoisGagne/crapkit/blob/"


def read(name):
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def esc(value):
    return html.escape(str(value), quote=True)


def boxes(items, after=False):
    return '<div class="flow ' + ('after' if after else 'before') + '">' + ''.join(
        '<div class="node">' + esc(item) + '</div>' for item in items) + '</div>'


def files(items, ref):
    links = []
    for item in items:
        path, _, line = item.partition(":")
        url = REPO + quote(ref, safe="/") + "/" + quote(path, safe="/")
        if line:
            url += "#L" + line
        links.append('<a href="' + esc(url) + '">' + esc(item) + '</a>')
    return '<div class="files">' + ' · '.join(links) + '</div>'


baseline = read("baseline-candidates.json")
structure = read("structure-measurements.json")
performance = read("analysis-measurements.json")
baseline_cost = read("baseline-measurements.json")
validation_path = HERE / "validation.json"
validation = read("validation.json") if validation_path.exists() else {"complete": False, "checks": []}
review_path = HERE / "fresh-findings.json"
fresh = read("fresh-findings.json") if review_path.exists() else []
fresh.sort(key=lambda row: row["priority"])
with (HERE / "completion.tsv").open(encoding="utf-8", newline="") as handle:
    ledger = list(csv.DictReader(handle, delimiter="\t"))
by_id = {item["id"]: item for item in ledger}

cards = []
for item in baseline:
    done = by_id[item["id"]]
    cards.append(f'''<article id="{esc(item['id'])}" class="candidate">
      <div class="eyebrow">{esc(item['priority'])} · {esc(item['strength'])} · {esc(item['effort'])}</div>
      <h2>{esc(item['title'])}</h2>
      <span class="badge">{esc(done['status'])}</span>
      <div class="pair"><section><h3>Before</h3>{boxes(item['before'])}</section>
      <section><h3>Implemented</h3>{boxes(item['after'], True)}</section></div>
      <p>{esc(item['problem'])}</p><p>{esc(done['implementation'])}</p>
      <p class="small">Deletion test: {esc(item['deletion'])}</p>
      <p class="small">Tradeoff: {esc(item['risk'])}</p>
      <div class="evidence">{esc(done['validation'])}</div>
      {files(item['files'], structure['baseline'])}
      <p class="small">Source links above pin the original finding to the release baseline.</p>
    </article>''')

before, after = structure["before"]["production"], structure["after"]["production"]
tools_before, tools_after = structure["before"]["tools"], structure["after"]["tools"]
tests_before, tests_after = structure["before"]["tests"], structure["after"]["tests"]
measure_rows = ''.join(f'<tr><td>{label}</td><td>{before[key]:,}</td><td>{after[key]:,}</td>'
                       f'<td>{after[key] - before[key]:+,}</td></tr>'
                       for key, label in (("files", "Production Python modules"),
                                          ("lines", "Production source lines"),
                                          ("functions", "Production functions")))
for label, old, new in (
        ("Release and developer tooling lines", tools_before["lines"], tools_after["lines"]),
        ("Production plus tooling lines", before["lines"] + tools_before["lines"],
         after["lines"] + tools_after["lines"]),
        ("Test support and regression lines", tests_before["lines"], tests_after["lines"])):
    measure_rows += f'<tr><td>{label}</td><td>{old:,}</td><td>{new:,}</td><td>{new - old:+,}</td></tr>'
perf_rows = ''.join(f'<tr><td>{label}</td><td>{performance[key]["median_warm_before"]["peak_python_mib"]:.4f}</td>'
                    f'<td>{performance[key]["median_warm_after"]["peak_python_mib"]:.4f}</td>'
                    f'<td>{performance[key]["median_warm_before"]["seconds"]:.4f}</td>'
                    f'<td>{performance[key]["median_warm_after"]["seconds"]:.4f}</td></tr>'
                    for key, label in (("contexts", "Selected file contexts"),
                                       ("twins", "Twin candidates with no matches"),
                                       ("folding", "Dead-line folding")))
history_rows = ''.join(f'<tr><td>{row["runs"]:,}</td><td>{row["before_ms"]:.4f}</td>'
                       f'<td>{row["after_ms"]:.4f}</td></tr>' for row in baseline_cost["timings"])
ledger_rows = ''.join(f'<tr><td>{esc(row["opportunity"])}</td><td>{esc(row["status"])}</td>'
                      f'<td>{esc(row["implementation"])}</td></tr>' for row in ledger)
check_rows = ''.join(f'<tr><td>{esc(row["check"])}</td><td>{esc(row["result"])}</td>'
                     f'<td>{esc(row.get("evidence", ""))}</td></tr>' for row in validation["checks"])
fresh_cards = ''.join(f'''<article class="repair"><div class="eyebrow">{esc(row['priority'])} · Strong</div>
    <h3>{esc(row['title'])}</h3><div class="pair"><section><h4>Reproduced</h4>{boxes(row['before'])}</section>
    <section><h4>Repaired</h4>{boxes(row['after'], True)}</section></div>
    <p>{esc(row['evidence'])}</p>{files(row['files'], REF)}</article>''' for row in fresh)
state = validation.get("status_label") or (
    "Required verification passed" if validation["complete"] else "Implementation verification in progress")
qualification = ('<p class="evidence">' + esc(validation["qualification"]) + '</p>'
                 if validation.get("qualification") else '')
document = f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Crapkit architecture, implementation and fresh review</title>
<script src="https://cdn.tailwindcss.com"></script>
<script type="module">import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
mermaid.initialize({{startOnLoad:true,theme:"neutral",securityLevel:"strict"}});</script>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#f7f6f2;color:#1c2926;font:16px/1.55 system-ui,sans-serif}}
main{{max-width:1120px;margin:auto;padding:54px 30px}}header{{border-top:7px solid #176347;padding-top:26px;margin-bottom:42px}}
body h1{{font:600 clamp(32px,5vw,56px)/1.12 Georgia,serif;max-width:850px;margin:14px 0 20px}}
body h2{{font:600 28px/1.25 Georgia,serif;margin:12px 0 16px}}body h3{{font-weight:650;margin:8px 0 14px}}body h4{{font-weight:650;margin:0 0 8px}}
body p{{margin:12px 0}}body a{{color:#176347;text-decoration:underline;text-underline-offset:3px}}section.major{{margin:50px 0;scroll-margin-top:24px}}
.eyebrow{{font-size:12px;text-transform:uppercase;letter-spacing:.12em;color:#547066}}
.badge{{display:inline-block;padding:4px 10px;border-radius:4px;background:#e3f0e8;font-size:13px}}
.candidate,.repair{{background:#fff;border:1px solid #d7dfd9;border-radius:8px;padding:26px;margin:22px 0}}
.pair{{display:grid;grid-template-columns:1fr 1fr;gap:24px;margin:22px 0}}.flow{{display:grid;gap:9px}}
.node{{padding:12px 15px;border:1px solid #cbd4ce;border-radius:4px;position:relative;min-height:49px;font-size:14px}}
.node+.node:before{{content:'↓';position:absolute;top:-19px;left:48%;color:#73847a;background:white;line-height:19px}}
.before .node:last-child{{border-color:#be7870;background:#fcf1ee}}.after .node{{background:#edf5ef;border-color:#83a992}}
.after .node:last-child{{background:#1a4435;color:white;border:2px solid #1a4435}}
.evidence{{border-left:3px solid #86a08c;padding:10px 14px;margin-top:20px;background:#f2f6f2;font-size:14px}}
.files{{font:12px/1.8 ui-monospace,monospace;overflow-wrap:anywhere;margin-top:16px}}.small{{font-size:13px;color:#596b60}}
table{{width:100%;border-collapse:collapse;font-size:14px;background:white}}th,td{{text-align:left;vertical-align:top;padding:12px;border-bottom:1px solid #dce2dc}}th{{background:#eaf0e9;font-weight:650}}
.table-wrap{{overflow-x:auto}}.summary{{padding:25px;background:#173e30;color:#fff;border-radius:6px}}.summary a{{color:#cbeed7}}
nav{{display:flex;gap:18px;flex-wrap:wrap;font-size:14px}}.mermaid{{background:white;padding:18px;border:1px solid #d7dfd9}}
@media(max-width:680px){{main{{padding:28px 18px}}.pair{{grid-template-columns:1fr}}.candidate,.repair{{padding:19px}}}}
@media print{{body{{background:white}}main{{max-width:none}}.candidate,.repair{{break-inside:avoid}}nav{{display:none}}}}
</style></head><body><main>
<header><div class="eyebrow">Crapkit · whole-project reassessment · 6 September 2026</div>
<h1>Whole-project changes.<br>Measured results and remaining limits.</h1>
<span class="badge">{esc(state)}</span>{qualification}<p class="small">Solid boxes are modules or owned decisions. Red marks the failure. Dark green marks the resulting shared responsibility.</p>
<nav><a href="#results">Results</a><a href="#fresh">Fresh review</a><a href="#original">Original opportunities</a><a href="#ledger">Complete ledger</a><a href="#validation">Validation</a></nav></header>
<section id="results" class="major"><h2>Measured change</h2>
<div class="table-wrap"><table><thead><tr><th>Measure</th><th>Before</th><th>After</th><th>Change</th></tr></thead><tbody>{measure_rows}</tbody></table></div>
<p class="small">AST inventory against release 20f00e1. Source lines include comments and blanks. Production removes repeated decisions, while identity migration and release checks add rules the old code did not enforce. The combined source count shows that cost; this is not a net code-reduction claim.</p>
<div class="table-wrap"><table><thead><tr><th>Synthetic operation</th><th>Before MiB</th><th>After MiB</th><th>Before seconds</th><th>After seconds</th></tr></thead><tbody>{perf_rows}</tbody></table></div>
<p class="small">Median of five warmed in-process samples with tracemalloc. Every result matched. These are operation measurements, not production command latency. Folding shows no measured speed or allocation improvement; its change removes shared process-global ownership.</p>
<div class="table-wrap"><table><thead><tr><th>History rows</th><th>Baseline selection before, ms</th><th>After, ms</th></tr></thead><tbody>{history_rows}</tbody></table></div>
<p class="small">The ordered baseline walk matched {baseline_cost['equivalent_histories']:,} generated histories. This isolates history selection, excluding database and CLI startup time.</p>
<p class="small">Correctness has a cost too. The new coverage identity check makes one linear pass over source spans. A 100,000-function fixture kept peak Python allocation at 35.778 MiB; warm median time changed from 0.933 to 1.119 seconds under concurrent load. Treat that timing as a cost observation, not a stable percentage.</p>
<pre class="mermaid">flowchart LR
CLI[CLI families] --> C[Configuration rules]
CLI --> S[Transactional snapshot store]
CLI --> L[Lane execution]
L --> P[Bounded process owner]
L --> R[Streaming report reader]
R --> F[Per-run missing lines]
S --> K[Function identity]
M[MCP adapter] --> CLI
classDef deep fill:#173e30,color:#fff,stroke:#173e30,stroke-width:3px
class C,S,P,R,K deep</pre></section>
<section id="fresh" class="major"><h2>What the fresh review found</h2>
<p>Independent reviewers rotated away from their implementation areas. The second review covered commands and delivery, execution, state and ranking, and analysis and core rules.</p>{fresh_cards}
<p class="small">Coverage maps and replay evidence live beside this source in core-review.md and the final-review records. No ADR was replaced.</p></section>
<section id="original" class="major"><h2>The original opportunities, reassessed</h2>{''.join(cards)}</section>
<section id="ledger" class="major"><h2>All 21 opportunities and follow-up checks</h2><div class="table-wrap"><table><thead><tr><th>Opportunity</th><th>Disposition</th><th>Result</th></tr></thead><tbody>{ledger_rows}</tbody></table></div></section>
<section id="validation" class="major"><h2>Verification evidence</h2><div class="table-wrap"><table><thead><tr><th>Check</th><th>Result</th><th>Evidence</th></tr></thead><tbody>{check_rows}</tbody></table></div>
<p class="small">{esc(validation.get('limits', 'Final validation remains pending.'))}</p></section>
<section class="summary"><h2>Top recommendation</h2><p>{esc(validation.get('recommendation', 'Finish the reproduced repairs and complete the configured coverage and verification run before shipping.'))}</p>
<p class="small" style="color:#d3e2d8">Source: docs/architecture/2026-09-06/final-review.html. Regenerate with build-final-report.py. Baseline report remains unchanged. Code reference: {esc(REF)}.</p></section>
</main></body></html>'''
(HERE / "final-review.html").write_text(document, encoding="utf-8")
(HERE / "final-candidates.json").write_text(json.dumps({"code_ref": REF, "ledger": ledger,
    "fresh_findings": fresh, "validation": validation}, indent=2), encoding="utf-8")
print(HERE / "final-review.html")
