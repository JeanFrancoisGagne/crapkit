// ESLint 10.11.0 and eslint-plugin-sonarjs 4.2.1 over a list of source files.
//
// Usage: node eslint_probe.mjs <node_modules> <mode> <file>...
// Prints one JSON object: {file: [{line, column, message}]}, the messages of
// the one rule the mode names, each set to report from 0 so every function
// (or block) is reported with its number:
//
//   complexity-classic   complexity, variant "classic": McCabe's count
//   complexity-modified  complexity, variant "modified": a switch counts once
//   max-depth            max-depth: the depth of each nested block
//   cognitive            sonarjs/cognitive-complexity: the Sonar paper's count
//
// .ts and .tsx parse with @typescript-eslint/parser 8.70.1, .vue with
// vue-eslint-parser 10.4.1 (its <script> through the TypeScript parser), the
// rest with ESLint's own parser, JSX on. No crapkit: this file only reads ESLint.
import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

const [nodeModules, mode, ...files] = process.argv.slice(2);
const require = createRequire(path.join(nodeModules, "package.json"));
const load = async (name) => {
  const loaded = await import(pathToFileURL(require.resolve(name)).href);
  return loaded.default ?? loaded;
};

const RULES = {
  "complexity-classic": { complexity: ["warn", { max: 0, variant: "classic" }] },
  "complexity-modified": { complexity: ["warn", { max: 0, variant: "modified" }] },
  "max-depth": { "max-depth": ["warn", 0] },
  cognitive: { "sonarjs/cognitive-complexity": ["warn", 0] },
};

const { ESLint } = await import(pathToFileURL(require.resolve("eslint")).href);
const tsParser = await load("@typescript-eslint/parser");
const vueParser = await load("vue-eslint-parser");
const sonarjs = await load("eslint-plugin-sonarjs");
const jsx = { ecmaFeatures: { jsx: true } };

const config = [
  { files: ["**/*.js", "**/*.jsx", "**/*.mjs", "**/*.cjs"],
    languageOptions: { ecmaVersion: "latest", sourceType: "module", parserOptions: jsx } },
  { files: ["**/*.ts", "**/*.tsx"],
    languageOptions: { parser: tsParser, parserOptions: jsx } },
  { files: ["**/*.vue"],
    languageOptions: { parser: vueParser,
                       parserOptions: { parser: tsParser, sourceType: "module" } } },
  { plugins: { sonarjs }, rules: RULES[mode] },
];

const eslint = new ESLint({ cwd: process.cwd(), overrideConfigFile: true,
                            overrideConfig: config, ignore: false });
const results = await eslint.lintFiles(files);
const out = {};
for (const result of results) {
  const fatal = result.messages.filter((m) => m.fatal);
  if (fatal.length) throw new Error(`${result.filePath}: ${fatal[0].message}`);
  out[path.relative(process.cwd(), result.filePath).split(path.sep).join("/")] =
    result.messages.map((m) => ({ line: m.line, column: m.column, message: m.message }));
}
process.stdout.write(JSON.stringify(out));
