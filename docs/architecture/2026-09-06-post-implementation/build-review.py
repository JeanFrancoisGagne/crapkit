"""Build the fresh static review from reviewed candidate data and evidence."""
import collections
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import re
import shutil
import sys
import textwrap
import zipfile

HERE = Path(__file__).resolve().parent
NAME = "crapkit-architecture-rerun-2026-09-06"
VISUALS = {
    'AN1': (['Read A and calculate digest A', 'Parser reopens path and reads B', 'Cache digest A with records from B'],
            ['One source input', 'Matching digest and parsed records', 'Publish one consistent cache entry']),
    'ST1': (['Hook run exists; alert waits', 'Prune sees no audit and deletes run', 'Grant succeeds; public audit is empty'],
            ['Override intent', 'One store rule owns publication and retention', 'Grant remains attached to its audit']),
    'AN4': (['12 paths with one input identity', '12 parser jobs', 'One cache entry; 12 path-specific results'],
            ['12 paths with one input identity', 'One parser job', 'One cache entry; 12 path-specific results'])
}


def esc(value):
    return html.escape(str(value), quote=True)


def list_value(value):
    if isinstance(value, list):
        return value
    return [str(value)] if value else []


def readable(value):
    if isinstance(value, dict):
        return "; ".join(f"{k}: {readable(v)}" for k, v in value.items())
    if isinstance(value, list):
        return "; ".join(readable(v) for v in value)
    return str(value)


def diagram(items, after=False, variant=0):
    items = [readable(v) for v in list_value(items)]
    if len(items) == 1:
        items = re.split(r"\s*(?:→|->|\n)\s*", items[0])
    items = [i.strip() for i in items if i.strip()]
    blocks = []
    for index, value in enumerate(items):
        lines = textwrap.wrap(value, width=43)
        blocks.append(f'<div class="module {"deep" if after and index == len(items)//2 else ""}">{esc(value)}</div>')
        if index < len(items)-1:
            blocks.append('<div class="arrow" aria-hidden="true">↓</div>')
    return '<div class="diagram '+('after' if after else 'before')+'">'+''.join(blocks)+'</div>'


def card(candidate):
    c = candidate
    strength = c.get("strength", "Worth exploring")
    schematic = c.get('before_after_schematic', {})
    before = c.get("before", schematic.get('before', c.get("before_schematic", "Current callers → Split ownership → Observed cost")))
    after = c.get("after", schematic.get('after', c.get("after_schematic", "Callers → One owning module → Consistent result")))
    if c['id'] in VISUALS:
        before,after=VISUALS[c['id']]
    files = ''.join(f'<li>{esc(readable(f))}</li>' for f in list_value(c.get("files")))
    raw_evidence = c.get('evidence', [])
    refs_list = list(c.get('evidence_files', []))
    sources = []
    if isinstance(raw_evidence, dict):
        refs_list += raw_evidence.get('artifacts', [])
        refs_list += [raw_evidence[k].split(':')[0] for k in ('probe','result') if k in raw_evidence]
        sources = raw_evidence.get('sources', [])
        raw_evidence = [f'{k}: {readable(v)}' for k,v in raw_evidence.items() if k not in ('artifacts','sources','probe','result','repro_command','reproducible_command')]
    evidence = ''.join(f'<li>{esc(readable(e))}</li>' for e in list_value(raw_evidence))
    evidence += ''.join(f'<li>{esc(s["supports"])} <a href="{esc(s["url"])}">Official source</a></li>' for s in sources)
    refs = ''.join(f'<li><a href="{NAME}-evidence/{esc(f)}">{esc(f)}</a></li>' for f in dict.fromkeys(refs_list) if isinstance(f, str))
    benefit = [c.get("leverage", ""), c.get("locality", ""), c.get("test_benefits", "")]
    benefits = ''.join(f'<p>{esc(readable(b))}</p>' for b in benefit if b)
    adr = c.get("adr", c.get("adr_conflict", "No ADR conflict."))
    return f'''<article id="{esc(c['id'])}">
<div class="eyebrow">{esc(c['id'])} · {esc(c.get('category', 'in-process'))}</div>
<h2>{esc(c['title'])}</h2><div class="badges"><span class="badge {'strong' if strength == 'Strong' else 'explore'}">{esc(strength)}</span><span>{esc(c.get('review_kind', 'Design opportunity'))}</span></div>
<div class="pair"><section><h3>Before</h3>{diagram(before)}</section><section><h3>After</h3>{diagram(after, True)}</section></div>
<p class="problem">{esc(readable(c.get('problem', '')))}</p>
<p class="solution">{esc(readable(c.get('proposed_change', c.get('solution', ''))))}</p>
<div class="benefits">{benefits}</div>
<details><summary>Evidence, files and tradeoffs</summary><ul class="evidence">{evidence}</ul>
<h3>Source locations</h3><ul class="files">{files}</ul>
<p><b>Deletion test.</b> {esc(readable(c.get('deletion_test', '')))}</p>
<p><b>Cost and risk.</b> {esc(readable(c.get('cost_risk', ' '.join([c.get('cost', ''),c.get('risk', '')]))))}</p>
<p>{esc(readable(adr))}</p><ul class="files">{refs}</ul></details></article>'''


def load_candidates():
    candidates = []
    for family in ('execution', 'analysis', 'state', 'root'):
        raw = json.loads((HERE / f'{family}-candidates.json').read_text(encoding='utf-8-sig'))
        candidates += raw if isinstance(raw, list) else raw['candidates']
    assert len({c['id'] for c in candidates}) == len(candidates)
    return candidates


def render(data):
    candidates = data['candidates']
    rows = ''.join(f'<tr><td><a href="#{esc(c["id"])}">{esc(c["id"])}</a></td><td><a href="#{esc(c["id"])}">{esc(c["title"])}</a></td><td>{esc(c["strength"])}</td><td>{esc(c.get("review_kind", "Design opportunity"))}</td></tr>' for c in candidates)
    coverage = ''.join(f'<tr><td>{esc(r["area"])}</td><td>{esc(r["disposition"])}</td></tr>' for r in data['coverage'])
    strong = sum(c['strength'] == 'Strong' for c in candidates)
    cards = ''.join(card(c) for c in candidates)
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>crapkit · Fresh architecture review</title><script src="https://cdn.tailwindcss.com"></script>
<script type="module">import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';mermaid.initialize({{startOnLoad:true,theme:'neutral',securityLevel:'strict'}});</script>
<style>
:root{{--ink:#172b25;--paper:#f6f5ef;--line:#d5dbd3;--muted:#52645b;--green:#165d40;--amber:#965e0b}}*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.6 system-ui,sans-serif}}main{{max-width:1120px;margin:auto;padding:48px 28px 80px}}a{{color:var(--green);text-decoration:underline;text-underline-offset:3px}}h1,h2{{font-family:Georgia,serif;letter-spacing:-.025em;line-height:1.15}}h1{{font-size:clamp(38px,6vw,68px);margin:12px 0 20px}}h2{{font-size:32px;margin:9px 0 16px}}h3{{font-size:12px;text-transform:uppercase;letter-spacing:.12em;margin:14px 0 10px;color:var(--muted)}}p{{margin:12px 0}}header{{padding-bottom:30px;border-bottom:2px solid var(--ink)}}.eyebrow{{font-size:12px;text-transform:uppercase;letter-spacing:.15em;font-weight:600}}.meta{{font:13px/1.7 ui-monospace,monospace;color:var(--muted)}}.lead{{font-size:20px;max-width:880px}}.metrics{{display:flex;gap:28px;flex-wrap:wrap;margin:28px 0}}.metric b{{display:block;font:38px/1.2 Georgia,serif}}.metric span{{font-size:13px;color:var(--muted)}}.focus{{background:#e6eee6;border-left:4px solid var(--green);padding:18px 24px;margin:28px 0}}nav{{display:flex;gap:22px;flex-wrap:wrap;margin:22px 0;font-size:14px}}.table-wrap{{overflow-x:auto;margin:24px 0 44px}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{text-align:left;padding:12px 10px;border-bottom:1px solid var(--line);vertical-align:top}}th{{font-size:11px;text-transform:uppercase;letter-spacing:.08em}}td:first-child{{white-space:nowrap}}article{{padding:38px 0;border-top:1px solid var(--ink);scroll-margin-top:24px}}.badges{{display:flex;gap:16px;align-items:center;color:var(--muted);font-size:12px}}.badge{{padding:4px 10px;border-radius:2px}}.strong{{background:#d9eddd;color:#155533}}.explore{{background:#f5e8c9;color:#825006}}.pair{{display:grid;grid-template-columns:1fr 1fr;gap:22px;margin:18px 0 24px}}.diagram{{border:1px solid var(--line);background:#fff;padding:20px;min-height:258px;display:flex;flex-direction:column;justify-content:center}}.module{{border:1px solid #99aaa0;background:#f7f8f4;padding:12px 16px;text-align:center;font-size:14px;line-height:1.4}}.before .module:last-child{{border-color:#b97f73;color:#7d3427}}.deep{{background:#173f30;color:#fff;border:3px solid #173f30;padding:20px 16px}}.arrow{{text-align:center;color:#799487;height:28px}}.after{{border-top:3px solid var(--green)}}.problem,.solution{{max-width:980px}}.problem{{font-size:18px}}.solution{{color:var(--green)}}.benefits{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:18px;border-top:1px dashed var(--line);padding-top:10px;font-size:14px;color:var(--muted)}}details{{margin:16px 0;background:#eeeee7;padding:12px 18px}}summary{{cursor:pointer;font-size:14px;color:var(--muted)}}details ul{{list-style:disc;padding-left:22px}}details li{{margin:9px 0}}.files{{font:12px/1.6 ui-monospace,monospace;overflow-wrap:anywhere}}.limitations{{font-size:14px;color:var(--muted)}}footer{{border-top:2px solid var(--ink);padding-top:26px;margin-top:40px}}.legend{{font-size:12px;color:var(--muted)}}.map{{margin:28px 0;background:white;padding:20px;border:1px solid var(--line)}}@media(max-width:700px){{main{{padding:28px 18px}}.pair,.benefits{{grid-template-columns:1fr}}h2{{font-size:27px}}.diagram{{min-height:180px}}td:first-child{{white-space:normal}}}}@media print{{details{{display:block}}.pair{{break-inside:avoid}}article{{break-before:page}}nav{{display:none}}}}
body h1{{font-size:clamp(38px,6vw,68px);margin:12px 0 20px;line-height:1.15}}body h2{{font-size:32px;margin:9px 0 16px;line-height:1.15}}body h3{{font-size:12px;margin:14px 0 10px}}body p{{margin:12px 0}}body a{{color:var(--green);text-decoration:underline;text-underline-offset:3px}}
</style></head><body><main>
<header><div class="eyebrow">Independent source review · 6 September 2026</div><h1>crapkit, reviewed again.</h1>
<p class="lead">{len(candidates)} fresh opportunities across correctness, performance, maintenance and delivery. Start with the ownership of measurement inputs and shared state.</p>
<p class="meta">SOURCE 499d9db4f9ff4d212fb94ea3975c7477b6b1c968 · Review only · Production unchanged</p>
<div class="metrics"><div class="metric"><b>{len(candidates)}</b><span>fresh candidates</span></div><div class="metric"><b>{strong}</b><span>strong recommendations</span></div><div class="metric"><b>64</b><span>production modules</span></div><div class="metric"><b>{len(data['coverage'])}</b><span>review areas</span></div></div>
<p class="legend">Solid box: module · Arrow: flow · Dashed divider: seam · Dark box: concentrated ownership</p></header>
<nav><a href="#priorities">Ranked list</a><a href="#coverage">Coverage map</a><a href="#evidence">Verification</a><a href="{NAME}.json">Candidate data</a><a href="{NAME}-evidence.zip">Evidence bundle</a></nav>
<div class="focus"><b>First: automatic lane reuse.</b> A changed test input produces a passing trusted verdict with reuse, then exit 8 on a fresh run of the same tree. <a href="#EX1">Inspect the reproduced failure and proposed module ownership.</a></div>
<section id="priorities"><h2>Ranked opportunities</h2><div class="table-wrap"><table><thead><tr><th>ID</th><th>Opportunity</th><th>Recommendation</th><th>Evidence class</th></tr></thead><tbody>{rows}</tbody></table></div></section>
<section class="map"><h3>Where the strongest failures meet</h3><pre class="mermaid">flowchart LR
S[Source and test inputs] --> M[Measurement]
M --> A[Artifacts and cache]
A --> V[Verdict]
V --> R[Ratchet and audit]
R --> Q[Reports and packets]
classDef focus fill:#e6eee6,stroke:#165d40,stroke-width:2px;
class M,A,R focus;</pre></section>
<section id="candidates">{cards}</section>
<section id="coverage"><h2>Whole-project coverage</h2><p>All 64 production modules are mapped to a reviewer. Related tests, delivery files, configuration and docs were inspected at their interfaces. Test counts are inventory, not a claim that every test body was read.</p><div class="table-wrap"><table><thead><tr><th>Area</th><th>Disposition</th></tr></thead><tbody>{coverage}</tbody></table></div>
<details><summary>Production module assignment</summary><ul class="files">{''.join('<li>'+esc(p)+' · '+esc(owner)+'</li>' for p,owner in data['module_owners'].items())}</ul></details></section>
<section id="evidence"><h2>What was verified</h2><ul class="evidence"><li>Fresh self-worklist: run 72, reviewed SHA, stale=false. Fresh duplication: one nine-line pair at default settings.</li><li>Disposable CLI replays cover stale lane reuse, overlapping artifact writers and nested configuration ownership.</li><li>Controlled analysis and state interleavings reproduce wrong cache hits, non-monotone marks, lost audit history and inconsistent report reads.</li><li>Root independently replayed the cache, ratchet, audit, packet-age, empty-mark query and trend findings.</li><li>Wheel build and extracted-wheel import/version pass. Existing interpreter dependencies were used.</li><li>Sixteen focused schema, documentation and subprocess-coverage contract tests pass despite the demonstrated contract gaps.</li></ul>
<p class="limitations">This pass did not rerun the full coverage lane, Linux matrix, Docker build, release publication or exhaustive language conformance suite. Previous implementation verification remains in its original report. Reproductions establish failure modes, not their frequency. Synthetic and instrumented timings are labeled in the evidence; proposed speedups and code reductions remain unmeasured.</p>
<p class="limitations">Both accepted ADRs remain intact. The root-scope init finding restores ADR 0002's guard. No framework rewrite, wholesale private-test rewrite or size-only module split is recommended.</p></section>
<footer><h2>Top recommendation</h2><p>Start with <a href="#EX1">EX1: measurement inputs own automatic artifact reuse</a>. It closes a reproduced false passing verdict through one interface, then supplies the evidence model needed by hosted CI and concurrent execution.</p><p class="meta">Built from the candidate JSON and committed reproduction scripts. Source: docs/architecture/2026-09-06-post-implementation/. {esc(data['created_at'])}</p></footer>
</main></body></html>'''


def main():
    data = json.loads((HERE / 'review-data.json').read_text(encoding='utf-8'))
    data['candidates'] = load_candidates()
    order = data['candidate_order']
    assert set(order) == {c['id'] for c in data['candidates']}
    data['candidates'].sort(key=lambda c: order.index(c['id']))
    for c in data['candidates']:
        c['review_kind'] = data['evidence_classes'].get(c['id'], 'Design opportunity')
    (HERE / (NAME+'.json')).write_text(json.dumps(data, indent=2)+'\n', encoding='utf-8')
    (HERE / (NAME+'.html')).write_text(render(data), encoding='utf-8')
    print(json.dumps({'candidates':len(data['candidates']), 'strengths':dict(collections.Counter(c['strength'] for c in data['candidates']))}))


if __name__ == '__main__':
    main()
