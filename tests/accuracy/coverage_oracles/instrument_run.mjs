// istanbul-lib-instrument 6.0.3 over one ES module, run in this process.
//
//     node instrument_run.mjs <node_modules> <file> <work dir> <calls json>
//
// Instruments <file>, writes the result into <work dir> as a module, imports it,
// makes each call in <calls json> (a list of [export name, [arguments]]), and
// prints the coverage object the instrumented module filled in: {<file>: its
// fnMap, statementMap, branchMap and counters}, the shape nyc and vitest write
// to coverage-final.json. No test runner and no crapkit code take part.
import { readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const [nodeModules, file, work, callsJson] = process.argv.slice(2);
const require = createRequire(join(nodeModules, "instrument_run.cjs"));
const { createInstrumenter } = require("istanbul-lib-instrument");

const instrumenter = createInstrumenter({ esModules: true, coverageVariable: "__coverage__" });
const instrumented = join(work, "instrumented.mjs");
writeFileSync(instrumented, instrumenter.instrumentSync(readFileSync(file, "utf8"), file));
const module = await import(pathToFileURL(instrumented).href);
for (const [name, args] of JSON.parse(callsJson)) {
  module[name](...args);
}
process.stdout.write(JSON.stringify(globalThis.__coverage__));
