// crap-typescript-core's CRAP on each case, computed the way the tool computes it.
//
// usage: node crap_typescript_scores.mjs NODE_MODULES < cases.json > scores.json
// cases.json is [[ccn, covered, total], ...]. The tool turns a (covered, total)
// pair into a percent as (covered / total) * 100 (dist/coverageNormalization.js)
// and scores it with calculateCrapScore(complexity, percent) (dist/crapScore.js).
// Each score prints as a JSON number, which round-trips the double exactly.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const root = process.argv[2];
const entry = join(root, "@barney-media", "crap-typescript-core", "dist", "index.js");
const { calculateCrapScore } = await import(pathToFileURL(entry).href);
const cases = JSON.parse(readFileSync(0, "utf8"));
const scores = cases.map(([ccn, covered, total]) => calculateCrapScore(ccn, (covered / total) * 100));
process.stdout.write(JSON.stringify(scores));
