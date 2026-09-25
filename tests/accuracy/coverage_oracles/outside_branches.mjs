// istanbul-lib-coverage's own totals for an istanbul artifact, and what lies outside every function.
//
//     node outside_branches.mjs <node_modules> <coverage-final.json>
//
// Prints one JSON object keyed by file: istanbul-lib-coverage 3.2.2's summary
// totals for branches and statements, its uncovered lines, and the branch arms
// and statements whose start line no fnMap span [decl.start.line, loc.end.line]
// holds. The Python side adds crapkit's per-function counts to the outside ones
// and compares each total with istanbul-lib's, covered and total as two
// separate numbers.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const [nodeModules, artifactPath] = process.argv.slice(2);
const lib = await import(pathToFileURL(join(nodeModules, "istanbul-lib-coverage", "index.js")));
const createCoverageMap = lib.createCoverageMap || lib.default.createCoverageMap;

function spans(data) {
  return Object.values(data.fnMap).map((fn) => [fn.decl.start.line, fn.loc?.end?.line ?? fn.decl.start.line]);
}

function inside(allSpans, line) {
  return allSpans.some(([start, end]) => start <= line && line <= end);
}

function outsideBranches(data, allSpans) {
  let total = 0;
  let covered = 0;
  for (const [id, branch] of Object.entries(data.branchMap)) {
    if (!inside(allSpans, branch.loc.start.line)) {
      const hits = data.b[id] || [];
      total += hits.length;
      covered += hits.filter((count) => count > 0).length;
    }
  }
  return { total, covered };
}

function outsideStatements(data, allSpans) {
  let total = 0;
  let covered = 0;
  for (const [id, statement] of Object.entries(data.statementMap)) {
    if (!inside(allSpans, statement.start.line)) {
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
  const allSpans = spans(data);
  out[file] = {
    branches: { total: summary.branches.total, covered: summary.branches.covered },
    statements: { total: summary.statements.total, covered: summary.statements.covered },
    uncoveredLines: coverage.getUncoveredLines().map(Number),
    outsideBranches: outsideBranches(data, allSpans),
    outsideStatements: outsideStatements(data, allSpans),
  };
}
process.stdout.write(JSON.stringify(out));
