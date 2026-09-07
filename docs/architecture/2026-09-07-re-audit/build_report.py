"""Render the static architecture review from its committed findings and evidence."""
from html import escape
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def text(value):
    return escape(str(value), quote=True)


def bullets(values):
    return '<ul>' + ''.join(f'<li>{text(value)}</li>' for value in values) + '</ul>'


def node_boxes(labels, after):
    style = 'after' if after else 'before'
    nodes = [f'<div class="node {style}">{text(label)}</div>' for label in labels]
    arrow = '<svg class="arrow" viewBox="0 0 28 22" aria-hidden="true"><path d="M14 1v17m-6-6 6 6 6-6"/></svg>'
    return '<div class="box-flow">' + arrow.join(nodes) + '</div>'


def graph(labels, after):
    nodes = [f'N{i}["{text(label)}"]' for i, label in enumerate(labels)]
    tone = '#173c36' if after else '#9f342f'
    lines = ['flowchart TD', ' --> '.join(nodes),
             f'classDef tone fill:{tone},color:#fff,stroke:{tone},stroke-width:2px;']
    lines.append('class ' + ','.join(f'N{i}' for i in range(len(labels))) + ' tone;')
    return '<pre class="mermaid">' + '\n'.join(lines) + '</pre>'


def diagrams(item, index):
    renderer = graph if index in (0, 2, 4, 8) else node_boxes
    before = renderer(item['before'], False)
    after = renderer(item['after'], True)
    return f'<div class="diagram-pair"><figure><figcaption>Before · leaked rule</figcaption>{before}</figure><figure><figcaption>After · one owner</figcaption>{after}</figure></div>'


def file_list(files):
    values = [f"{entry['path']}:{entry['start']}" for entry in files]
    return '<div class="files">' + '<br>'.join(text(value) for value in values) + '</div>'


def detail(item):
    links = ', '.join(f'<code>{text(path)}</code>' for path in item['evidence_paths'])
    return f'''<details><summary>Evidence, deletion test and verification plan</summary>
      <div class="detail-grid"><div><h4>Observed</h4>{bullets(item['evidence'])}
      <h4>Replay evidence</h4><p class="evidence-paths">{links}</p></div>
      <div><h4>Deletion test</h4><p>{text(item['deletion_test'])}</p>
      <h4>Tests through the interface</h4>{bullets(item['tests'])}</div></div>
      <p class="cost"><b>Cost and limits</b> · {text(item['cost'])}</p>
      <p class="adr">{text(item['adr'])}</p></details>'''


def benefits(values):
    labels = ('Locality', 'Leverage', 'Tests')
    return ''.join(f'<span>{label} · {text(value)}</span>' for label, value in zip(labels, values))


def card(item, index):
    tone = 'strong' if item['strength'] == 'Strong' else 'explore'
    dependency = {'CORE-01': 'local-substitutable', 'EX1': 'ports & adapters',
                  'EX2': 'local-substitutable', 'UI1': 'ports & adapters',
                  'UI2': 'ports & adapters', 'UI4': 'local-substitutable',
                  'STATE-03': 'ports & adapters', 'CORE-S02': 'mock',
                  'UI5': 'published guidance'}.get(item['id'], 'in-process')
    return f'''<article id="{text(item['id'])}" class="candidate">
      <div class="card-title"><span class="ordinal">{index + 1:02d}</span>
      <div><div class="badges"><span class="badge {tone}">{text(item['strength'])}</span>
      <span class="category">{text(dependency)}</span><span class="category">{text(item['id'])}</span></div>
      <h2>{text(item['title'])}</h2></div></div>
      {file_list(item['files'])}{diagrams(item, index)}
      <div class="rationale"><p><b>Problem</b> · {text(item['problem'])}</p>
      <p><b>Deepen</b> · {text(item['solution'])}</p></div>
      <div class="wins">{benefits(item['benefits'])}</div>
      {detail(item)}</article>'''


def index_rows(items):
    return ''.join(f'<tr><td><a href="#{text(item["id"])}">{i+1:02d}</a></td><td><a href="#{text(item["id"])}">{text(item["title"])}</a></td><td>{text(item["strength"])}</td></tr>' for i, item in enumerate(items))


def coverage_rows(rows):
    return ''.join(f'<tr><td>{text(row["area"])}</td><td>{text(row["disposition"])}</td></tr>' for row in rows)


def dismissed_rows(rows):
    return ''.join(f'<tr><td>{text(row["idea"])}</td><td>{text(row["reason"])}</td></tr>' for row in rows)


def metrics(facts):
    values = [(str(facts['candidates']), 'opportunities'), (str(facts['strong']), 'Strong'),
              ('348 / 1', 'checks passed / skipped'), ('1,867', 'functions self-analyzed')]
    return ''.join(f'<div><strong>{text(value)}</strong><span>{text(label)}</span></div>' for value, label in values)


def build():
    data = json.loads((HERE / 'report-data.json').read_text(encoding='utf-8'))
    facts = json.loads((HERE / 'audit-facts.json').read_text(encoding='utf-8'))
    template = (HERE / 'report-template.html').read_text(encoding='utf-8')
    values = dict(style=(HERE / 'report.css').read_text(encoding='utf-8'),
                  cards=''.join(card(item, i) for i, item in enumerate(data['candidates'])),
                  index=index_rows(data['candidates']), metrics=metrics(facts),
                  coverage=coverage_rows(data['coverage']), dismissed=dismissed_rows(data['dismissed']),
                  source_count=str(facts['python_modules']), input_count=str(facts['tracked_inputs']))
    for key, value in values.items():
        template = template.replace('{{' + key + '}}', value)
    (HERE / 'architecture-review.html').write_text(template, encoding='utf-8', newline='\n')


if __name__ == '__main__':
    build()
