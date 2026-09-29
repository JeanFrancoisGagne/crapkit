// ESLint 10.11.0 and eslint-plugin-sonarjs 4.2.1 over a list of source files.
//
// Usage: node eslint_probe.mjs <node_modules> <mode>[,<mode>...] <file>...
// Prints one JSON object: {"fatal": {file: message}, mode: {file: [{line,
// column, message}]}, ...}, for each mode the messages of the one rule it
// names, set to report from 0 so every function (or block) is reported with
// its number:
//
//   complexity-classic   complexity, variant "classic": McCabe's count
//   complexity-modified  complexity, variant "modified": a switch counts once
//   max-depth            max-depth: the depth of each nested block
//   cognitive            sonarjs/cognitive-complexity: the Sonar paper's count
//
// One process lints the whole list once per mode, so a caller starts one node
// process per shard of files. Inline configuration is off (linterOptions
// noInlineConfig): a file's own `eslint-disable-next-line complexity` or
// `/* eslint max-depth: ... */` comment would hide or reset a number, and only
// the named rule's messages are kept. A file the parser rejects is listed
// under "fatal" with its first fatal message and gets no messages.
//
// .ts and .tsx parse with @typescript-eslint/parser 8.70.1, .vue with
// vue-eslint-parser 10.4.1 (its <script> through the TypeScript parser), the
// rest with ESLint's own parser, JSX on. No crapkit: this file only reads ESLint.
import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

const [nodeModules, modes, ...files] = process.argv.slice(2);
const require = createRequire(path.join(nodeModules, "package.json"));
const load = async (name) => {
  const loaded = await import(pathToFileURL(require.resolve(name)).href);
  return loaded.default ?? loaded;
};

const RULES = {
  "complexity-classic": ["complexity", ["warn", { max: 0, variant: "classic" }]],
  "complexity-modified": ["complexity", ["warn", { max: 0, variant: "modified" }]],
  "max-depth": ["max-depth", ["warn", 0]],
  cognitive: ["sonarjs/cognitive-complexity", ["warn", 0]],
};

const { ESLint } = await import(pathToFileURL(require.resolve("eslint")).href);
const tsParser = await load("@typescript-eslint/parser");
const vueParser = await load("vue-eslint-parser");
const sonarjs = await load("eslint-plugin-sonarjs");
const jsx = { ecmaFeatures: { jsx: true } };

function config(ruleId, setting) {
  return [
    { files: ["**/*.js", "**/*.jsx", "**/*.mjs", "**/*.cjs"],
      languageOptions: { ecmaVersion: "latest", sourceType: "module", parserOptions: jsx } },
    { files: ["**/*.ts", "**/*.tsx"],
      languageOptions: { parser: tsParser, parserOptions: jsx } },
    { files: ["**/*.vue"],
      languageOptions: { parser: vueParser,
                         parserOptions: { parser: tsParser, sourceType: "module" } } },
    { linterOptions: { noInlineConfig: true, reportUnusedDisableDirectives: "off" },
      plugins: { sonarjs }, rules: { [ruleId]: setting } },
  ];
}

const relative = (file) => path.relative(process.cwd(), file).split(path.sep).join("/");

function kept(result, ruleId) {
  return result.messages.filter((m) => m.ruleId === ruleId)
    .map((m) => ({ line: m.line, column: m.column, message: m.message }));
}

async function lint(mode, fatal) {
  const [ruleId, setting] = RULES[mode];
  const eslint = new ESLint({ cwd: process.cwd(), overrideConfigFile: true,
                              overrideConfig: config(ruleId, setting), ignore: false });
  const out = {};
  for (const result of await eslint.lintFiles(files)) {
    const file = relative(result.filePath);
    const broken = result.messages.find((m) => m.fatal);
    if (broken) fatal[file] = `${broken.line}:${broken.column} ${broken.message}`;
    out[file] = broken ? [] : kept(result, ruleId);
  }
  return out;
}

const fatal = {};
const found = { fatal };
for (const mode of modes.split(",")) found[mode] = await lint(mode, fatal);
process.stdout.write(JSON.stringify(found));
