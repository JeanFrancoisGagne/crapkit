"use strict";
// The outside oracles `declare` asks about a moved JS, TS or Vue cell: ESLint's
// complexity rule in its classic variant (each `case` counts) and its modified
// variant (a switch counts once), and eslint-plugin-sonarjs's cognitive
// complexity rule (S3776), each run with its threshold at 0 so every function
// with a value above 0 is reported. It prints one JSON object:
//   {"functions": [[start line, body start line], ...],
//    "values": [{"rule": "classic" | "modified" | "cognitive", "line": L, "value": N}, ...]}
// where L is the line each rule reports a function at: a line of its head,
// between its start line and its body's start line.
// Usage: node eslint_values.cjs NODE_MODULES FILE
const { createRequire } = require("node:module");
const fs = require("node:fs");
const path = require("node:path");

const [modules, file] = process.argv.slice(2);
const load = createRequire(path.join(path.resolve(modules), "index.js"));
const { Linter } = load("eslint");
const sonarjs = load("eslint-plugin-sonarjs");
const tsParser = load("@typescript-eslint/parser");
const vueParser = load("vue-eslint-parser");

const suffix = path.extname(file).toLowerCase();
const typescript = [".ts", ".tsx", ".mts", ".cts"].includes(suffix);
const parserOptions = { ecmaVersion: "latest", ecmaFeatures: { jsx: suffix !== ".ts" } };
const languageOptions = suffix === ".vue"
  ? { parser: vueParser, parserOptions: { ...parserOptions, parser: tsParser } }
  : { parser: typescript ? tsParser : undefined, parserOptions };
languageOptions.sourceType = suffix === ".cjs" ? "commonjs" : "module";

const functions = [];
const note = (node) => functions.push([node.loc.start.line, node.body.loc.start.line]);
const collect = {
  meta: { type: "problem", schema: [] },
  create: () => ({ FunctionDeclaration: note, FunctionExpression: note,
                   ArrowFunctionExpression: note }),
};

const RULES = {
  classic: [{ "complexity": ["error", { max: 0, variant: "classic" }], "cc/collect": "error" },
            /complexity of (\d+)/],
  modified: [{ "complexity": ["error", { max: 0, variant: "modified" }] }, /complexity of (\d+)/],
  cognitive: [{ "sonarjs/cognitive-complexity": ["error", 0] }, /Complexity from (\d+) to/],
};

const source = fs.readFileSync(file, "utf8");
const linter = new Linter({ configType: "flat" });
const values = [];
for (const [name, [rules, pattern]] of Object.entries(RULES)) {
  const config = [{ files: [`**/*${suffix}`], languageOptions, rules,
                    plugins: { sonarjs, cc: { rules: { collect } } } }];
  for (const message of linter.verify(source, config, { filename: path.basename(file) })) {
    if (message.fatal || message.ruleId === null) {
      process.stdout.write(JSON.stringify({ error: message.message, line: message.line }));
      process.exit(0);
    }
    const found = pattern.exec(message.message);
    if (found) values.push({ rule: name, line: message.line, value: Number(found[1]) });
  }
}
process.stdout.write(JSON.stringify({ functions, values }));
