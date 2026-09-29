// istanbul-lib-coverage's own totals for an istanbul artifact, and what lies outside every function.
//
//     node outside_branches.mjs <node_modules> <coverage-final.json>
//
// Prints one JSON object keyed by file: istanbul-lib-coverage 3.2.2's summary
// totals for branches and statements, its uncovered lines, and the counters no
// function holds, by the rule docs/lanes.md gives fnMap, branchMap and
// statementMap. A branch arm is a function's when the function's span, decl.start
// to loc.end, holds the arm's loc.start. A statement is a function's when its
// body, loc.start (else decl.start) to loc.end, holds the statement's start, so
// the declaration statement of `const f = (x) => x * 2`, which starts ahead of
// the arrow's body, lies outside the arrow. loc.end falls back to the end of
// decl.start's line. Positions compare the line, then the column, and a missing
// column stands for the whole line: the first column at a start, the last at an
// end. The Python side adds crapkit's per-function counts to the outside ones
// and compares each total with istanbul-lib's, covered and total as two
// separate numbers.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const [nodeModules, artifactPath] = process.argv.slice(2);
const lib = await import(pathToFileURL(join(nodeModules, "istanbul-lib-coverage", "index.js")));
const createCoverageMap = lib.createCoverageMap || lib.default.createCoverageMap;
const LAST = Number.POSITIVE_INFINITY;

function position(point, missing) {
  if (!point || point.line == null) {
    return [missing, missing];
  }
  return [point.line, point.column == null ? missing : point.column];
}

function atOrBefore(a, b) {
  return a[0] < b[0] || (a[0] === b[0] && a[1] <= b[1]);
}

function functions(data) {
  return Object.values(data.fnMap).map((fn) => {
    const start = position(fn.decl.start, 0);
    const end = position(fn.loc?.end, LAST);
    const body = position(fn.loc?.start, 0);
    return { start, body: body[0] ? body : start, end: end[0] === LAST ? [start[0], LAST] : end };
  });
}

function held(fns, point, from) {
  return fns.some((fn) => atOrBefore(fn[from], point) && atOrBefore(point, fn.end));
}

function outsideBranches(data, fns) {
  let total = 0;
  let covered = 0;
  for (const [id, branch] of Object.entries(data.branchMap)) {
    if (!held(fns, position(branch.loc?.start, 0), "start")) {
      const hits = data.b[id] || [];
      total += hits.length;
      covered += hits.filter((count) => count > 0).length;
    }
  }
  return { total, covered };
}

function outsideStatements(data, fns) {
  let total = 0;
  let covered = 0;
  for (const [id, statement] of Object.entries(data.statementMap)) {
    if (!held(fns, position(statement.start, 0), "body")) {
      total += 1;
      covered += (data.s[id] || 0) > 0 ? 1 : 0;
    }
  }
  return { total, covered };
}

const artifact = JSON.parse(readFileSync(artifactPath, "utf8"));
const map = createCoverageMap(artifact);
const out = {};
for (const file of map.files()) {
  const data = artifact[file];
  const coverage = map.fileCoverageFor(file);
  const summary = coverage.toSummary();
  const fns = functions(data);
  out[file] = {
    branches: { total: summary.branches.total, covered: summary.branches.covered },
    statements: { total: summary.statements.total, covered: summary.statements.covered },
    uncoveredLines: coverage.getUncoveredLines().map(Number),
    outsideBranches: outsideBranches(data, fns),
    outsideStatements: outsideStatements(data, fns),
  };
}
process.stdout.write(JSON.stringify(out));
