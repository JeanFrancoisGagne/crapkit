// Every function the TypeScript 6.0.2 compiler finds in a list of source files.
//
// Usage: node ts_functions.cjs <typescript module dir> <file>...
// Prints one JSON object: {file: [{kind, name, start, column, end, endColumn,
// params, defaults, returnType, features}]}.
//
// A function is a node of kind FunctionDeclaration, FunctionExpression,
// ArrowFunction, MethodDeclaration, Constructor, GetAccessor or SetAccessor
// that has a body (an overload signature has none). Its start is the line of
// its first token, its end the line of its last, both 1-based (TypeScript's
// getLineAndCharacterOfPosition is 0-based). params is parameters.length with
// a `this` parameter left out, since it is a type annotation, not an argument
// (TypeScript handbook, "Declaring this in a Function"); defaults counts the
// parameters with an initializer. features names the constructs in the
// function's own body (nested functions left out) that an oracle reads its own
// way: "or", "and", "nullish" (?? and ??=), "optional" (?.), "ternary",
// "nested_ternary", "negated_logical" (! over a parenthesized && or ||),
// "else", "switch", "label", "try", "recursion" (a call of the function's own
// name) and "nested_function". A .vue file is read
// from its first <script> block, parsed as TypeScript when lang="ts", with
// lines counted from the top of the file (Vue SFC spec, "Language Blocks").
// No crapkit: this file only reads the compiler.
"use strict";
const fs = require("fs");
const path = require("path");

const ts = require(process.argv[2]);
const KINDS = new Set([
  ts.SyntaxKind.FunctionDeclaration, ts.SyntaxKind.FunctionExpression,
  ts.SyntaxKind.ArrowFunction, ts.SyntaxKind.MethodDeclaration,
  ts.SyntaxKind.Constructor, ts.SyntaxKind.GetAccessor, ts.SyntaxKind.SetAccessor,
]);
const SCRIPT = /<script\b([^>]*)>([\s\S]*?)<\/script>/i;

function scriptKind(file) {
  const ext = path.extname(file).toLowerCase();
  if (ext === ".tsx") return ts.ScriptKind.TSX;
  if (ext === ".ts") return ts.ScriptKind.TS;
  if (ext === ".jsx") return ts.ScriptKind.JSX;
  return ts.ScriptKind.JS;
}

// {text, kind, offset}: the code to parse and the lines above it.
function source(file) {
  const text = fs.readFileSync(file, "utf8");
  if (path.extname(file).toLowerCase() !== ".vue") {
    return { text, kind: scriptKind(file), offset: 0 };
  }
  const found = SCRIPT.exec(text);
  const before = text.slice(0, found.index + found[0].indexOf(">") + 1);
  const kind = /lang\s*=\s*["']ts["']/.test(found[1]) ? ts.ScriptKind.TS : ts.ScriptKind.JS;
  return { text: found[2], kind, offset: before.split("\n").length - 1 };
}

function declaredName(node) {
  const parent = node.parent;
  const named = parent && ts.isVariableDeclaration(parent) && ts.isIdentifier(parent.name);
  return named ? parent.name.text : null;
}

function nameOf(node) {
  if (node.name) return ts.isIdentifier(node.name) ? node.name.text : node.name.getText();
  const declared = declaredName(node);
  if (declared) return declared;
  return node.kind === ts.SyntaxKind.Constructor ? "constructor" : "(anonymous)";
}

function params(node) {
  return node.parameters.filter((p) => !(ts.isIdentifier(p.name) && p.name.text === "this"))
    .length;
}

function line(sf, pos, offset) {
  return sf.getLineAndCharacterOfPosition(pos).line + 1 + offset;
}

const OPERATORS = {
  [ts.SyntaxKind.BarBarToken]: "or", [ts.SyntaxKind.BarBarEqualsToken]: "or",
  [ts.SyntaxKind.AmpersandAmpersandToken]: "and",
  [ts.SyntaxKind.AmpersandAmpersandEqualsToken]: "and",
  [ts.SyntaxKind.QuestionQuestionToken]: "nullish",
  [ts.SyntaxKind.QuestionQuestionEqualsToken]: "nullish",
};
const STATEMENTS = {
  [ts.SyntaxKind.SwitchStatement]: "switch", [ts.SyntaxKind.LabeledStatement]: "label",
  [ts.SyntaxKind.TryStatement]: "try", [ts.SyntaxKind.QuestionDotToken]: "optional",
};

function unwrap(node) {
  while (ts.isParenthesizedExpression(node)) node = node.expression;
  return node;
}

function logical(node) {
  return ts.isBinaryExpression(node) && OPERATORS[node.operatorToken.kind] !== undefined;
}

function calls(node, name) {
  return ts.isCallExpression(node) && ts.isIdentifier(node.expression) &&
    node.expression.text === name;
}

function nestedTernary(node) {
  return ts.isConditionalExpression(node) && [node.whenTrue, node.whenFalse].some(
    (branch) => ts.isConditionalExpression(unwrap(branch)));
}

function negatedLogical(node) {
  return ts.isPrefixUnaryExpression(node) && node.operator === ts.SyntaxKind.ExclamationToken &&
    logical(unwrap(node.operand));
}

const TESTS = [
  ["ternary", (node) => ts.isConditionalExpression(node)],
  ["nested_ternary", nestedTernary],
  ["negated_logical", negatedLogical],
  ["else", (node) => ts.isIfStatement(node) && Boolean(node.elseStatement)],
  ["recursion", (node, fn) => calls(node, nameOf(fn))],
];

// The features one node adds, each a name the header lists.
function featuresOf(node, fn) {
  const found = TESTS.filter(([, test]) => test(node, fn)).map(([name]) => name);
  if (logical(node)) found.push(OPERATORS[node.operatorToken.kind]);
  if (STATEMENTS[node.kind]) found.push(STATEMENTS[node.kind]);
  return found;
}

function features(fn) {
  const found = new Set();
  const visit = (node) => {
    if (node !== fn && KINDS.has(node.kind)) {
      found.add("nested_function");
      return;
    }
    featuresOf(node, fn).forEach((name) => found.add(name));
    ts.forEachChild(node, visit);
  };
  visit(fn);
  return [...found].sort();
}

function record(sf, node, offset) {
  const end = sf.getLineAndCharacterOfPosition(node.end - 1);
  return {
    kind: ts.SyntaxKind[node.kind],
    name: nameOf(node),
    start: line(sf, node.getStart(sf), offset),
    column: sf.getLineAndCharacterOfPosition(node.getStart(sf)).character,
    end: end.line + 1 + offset,
    endColumn: end.character,
    params: params(node),
    defaults: node.parameters.filter((p) => p.initializer).length,
    returnType: Boolean(node.type),
    features: features(node),
  };
}

function functions(file) {
  const { text, kind, offset } = source(file);
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, kind);
  const out = [];
  const visit = (node) => {
    if (KINDS.has(node.kind) && node.body) out.push(record(sf, node, offset));
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return out;
}

const result = {};
for (const file of process.argv.slice(3)) result[file] = functions(file);
process.stdout.write(JSON.stringify(result));
