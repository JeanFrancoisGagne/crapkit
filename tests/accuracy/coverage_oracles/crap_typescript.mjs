// crap-typescript 0.5.2's reading of an istanbul artifact over one TypeScript file.
//
//     node crap_typescript.mjs <node_modules> <coverage-final.json> <source root> <file>
//
// Prints a JSON list, one entry per method the TypeScript compiler finds in
// <file>: its lines, crap-typescript's statement and branch coverage, and the
// combined coverage it scores (the smaller of the two measured percentages).
// Its attribution matches methods to fnMap entries by body span, then assigns
// each statement and branch to the innermost matched span by position.
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const [nodeModules, artifactPath, sourceRoot, file] = process.argv.slice(2);
const core = await import(pathToFileURL(
  join(nodeModules, "@barney-media", "crap-typescript-core", "dist", "index.js")));

const methods = await core.parseFileMethods(file);
const report = await core.parseCoverageReport(artifactPath, sourceRoot);
// crap-typescript keys its report by a normalized path: forward slashes, and on
// Windows lower case.
const normal = (path) => {
  const posix = path.replace(/\\/g, "/");
  return process.platform === "win32" ? posix.toLowerCase() : posix;
};
const fileCoverage = [...report.entries()].find(([path]) => normal(path) === normal(file))?.[1];
const coverage = core.coverageForMethods(methods, fileCoverage);

const metric = (value) => ({ status: value.status, percent: value.percent, reason: value.unknownReason });
process.stdout.write(JSON.stringify(methods.map((method, index) => ({
  name: method.functionName,
  start: method.startLine,
  end: method.endLine,
  statements: metric(coverage[index].statementCoverage),
  branches: metric(coverage[index].branchCoverage),
  combined: metric(coverage[index].coverage),
}))));
