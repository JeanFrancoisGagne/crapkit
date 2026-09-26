// Every function the TypeScript 6.0.2 compiler finds in a list of source files.
//
// Usage: node ts_functions.cjs <typescript module dir> <file>...
// Prints one JSON object: {file: {functions: [{kind, name, start, column, end,
// endColumn, params, paramList, defaults, patternDefaults, returnType,
// features, shapes}],
// marks: [[shape, line, reach]]}}.
//
// A function is a node of kind FunctionDeclaration, FunctionExpression,
// ArrowFunction, MethodDeclaration, Constructor, GetAccessor or SetAccessor
// that has a body (an overload signature has none). Its start is the line of
// its first token, its end the line of its last, both 1-based (TypeScript's
// getLineAndCharacterOfPosition is 0-based). params is parameters.length with
// a `this` parameter left out, since it is a type annotation, not an argument
// (TypeScript handbook, "Declaring this in a Function"); defaults counts the
// parameters with an initializer, patternDefaults the default values inside
// destructuring patterns of the function's own code (`{ size = "md" }` in a
// parameter or a declaration). paramList holds, for each of those
// parameters, [the name's source text, the type annotation's source text or
// null, whether it is a rest parameter]; the name's text of `...rest` is
// `rest` and of `b?` is `b` (ParameterDeclaration keeps dotDotDotToken and
// questionToken apart from the name). features names the constructs in the
// function's own body (nested functions left out) that an oracle reads its own
// way: "or", "and", "nullish" (?? and ??=), "optional" (?.), "ternary",
// "nested_ternary", "negated_logical" (! over a parenthesized && or ||),
// "else", "switch", "label", "try", "recursion" (a call of the function's own
// name), "nested_loop" (a loop inside a loop) and "nested_function". shapes
// names the constructs crapkit is known to misread that reach the function
// (SHAPES below; test_js_corpus_oracles maps each to its rulings row), and
// marks gives every such construct's line. A .vue file is read
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

function declared(node) {
  return node.parameters.filter((p) => !(ts.isIdentifier(p.name) && p.name.text === "this"));
}

function params(node) {
  return declared(node).length;
}

function paramList(node) {
  return declared(node).map((p) => [p.name.getText(), p.type ? p.type.getText() : null,
    Boolean(p.dotDotDotToken)]);
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

const LOOPS = new Set([
  ts.SyntaxKind.ForStatement, ts.SyntaxKind.ForInStatement, ts.SyntaxKind.ForOfStatement,
  ts.SyntaxKind.WhileStatement, ts.SyntaxKind.DoStatement,
]);

// A loop with another loop between it and the function that holds it.
function nestedLoop(node, fn) {
  if (!LOOPS.has(node.kind)) return false;
  for (let up = node.parent; up && up !== fn; up = up.parent) {
    if (LOOPS.has(up.kind)) return true;
  }
  return false;
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
  ["nested_loop", nestedLoop],
];

// The features one node adds, each a name the header lists.
function featuresOf(node, fn) {
  const found = TESTS.filter(([, test]) => test(node, fn)).map(([name]) => name);
  if (logical(node)) found.push(OPERATORS[node.operatorToken.kind]);
  if (STATEMENTS[node.kind]) found.push(STATEMENTS[node.kind]);
  return found;
}

// The names a function is called by: its own (nameOf), and for a function
// assigned to a property (`a.b = function () {}`) the property's name.
function namesOf(fn) {
  const names = new Set([nameOf(fn)]);
  const parent = fn.parent;
  if (parent && ts.isBinaryExpression(parent) && ts.isPropertyAccessExpression(parent.left)) {
    names.add(parent.left.name.text);
  }
  return names;
}

// An identifier spelled like one of the function's names that is not a plain
// call of its own name: `this.router.route(path)` inside `route`, a variable
// or a parameter named like it.
function nameSpelled(node, fn) {
  const named = Boolean(fn) && ts.isIdentifier(node) && namesOf(fn).has(node.text);
  return named && node !== fn.name && !callsItself(node, fn);
}

// `node` is the callee of a plain call of fn's own name: direct recursion.
function callsItself(node, fn) {
  const parent = node.parent;
  const callee = Boolean(parent) && ts.isCallExpression(parent) && parent.expression === node;
  return callee && node.text === nameOf(fn);
}

// Words crapkit counts as a structure wherever they stand as a token:
// JavaScript's own keywords, and those of the other languages it reads.
const KEYWORD_NAMES = new Set(["catch", "if", "for", "while", "do", "case", "switch", "else",
  "try"]);
const FOREIGN_KEYWORDS = new Set(["def", "foreach", "elif", "except", "and", "or", "goto"]);

// A regular expression literal lizard's tokenizer leaves as code tokens: it
// joins one only when the token before it ends in one of `=,({[?:!&|;` with
// no space between, the literal holds no space and its flags are g, i or m
// (lizard 1.24.0 js_style_regex_expression). One holding a character that
// means something as code is the shape.
// The characters and words are spelled out, not written as a regular
// expression literal, which this very reader would misread.
const JOINS_AFTER = "=,({[?:!&|;";
const CODE_CHARACTERS = new Set(["?", "\"", "'", String.fromCharCode(96), "{", "}", "(", ")",
  "[", "]"]);
const CODE_WORDS = new Set(["if", "for", "while", "do", "case", "catch", "switch", "else"]);

function joinedByLizard(node, text) {
  const before = node.getSourceFile().text[node.getStart() - 1] || "";
  const flags = text.slice(text.lastIndexOf("/") + 1);
  const plain = !text.includes(" ") && !text.includes("\t");
  return JOINS_AFTER.includes(before) && plain && [...flags].every((flag) => "gim".includes(flag));
}

function readsAsCode(body) {
  const words = body.match(/[A-Za-z]+/g) || [];
  return [...body].some((ch) => CODE_CHARACTERS.has(ch)) || words.some((w) => CODE_WORDS.has(w));
}

// An escaped slash ends lizard's joined token early, so such a literal is
// code whatever stands before it.
const ESCAPED_SLASH = String.fromCharCode(92) + "/";

function regexAsCode(node) {
  if (node.kind !== ts.SyntaxKind.RegularExpressionLiteral) return false;
  const text = node.getText();
  if (text.includes(ESCAPED_SLASH)) return true;
  return !joinedByLizard(node, text) && readsAsCode(text.slice(1, text.lastIndexOf("/")));
}

// `a?.(x)` and `a?.[x]`: an optional call or element access.
function optionalCall(node) {
  return (ts.isCallExpression(node) || ts.isElementAccessExpression(node)) &&
    Boolean(node.questionDotToken);
}

// An arrow whose expression body starts on a line after its `=>`.
function arrowBodyBelow(node) {
  if (!ts.isArrowFunction(node) || ts.isBlock(node.body)) return false;
  const sf = node.getSourceFile();
  const lineOf = (pos) => sf.getLineAndCharacterOfPosition(pos).line;
  return lineOf(node.body.getStart()) > lineOf(node.equalsGreaterThanToken.getStart());
}

// A `?` inside a type: a conditional type, an optional member of a type
// literal or interface, an optional mapped member.
function typeQuestion(node) {
  if (ts.isConditionalTypeNode(node)) return true;
  const member = ts.isMappedTypeNode(node) || ts.isPropertySignature(node) ||
    ts.isMethodSignature(node);
  return member && Boolean(node.questionToken);
}

// A property's value that is a conditional over lines whose true branch is a
// call (`a: p` then `? g(c)` then `: h`): the call's node, whose line crapkit
// rows as a function.
function ternaryCallRow(node) {
  if (!ts.isConditionalExpression(node) || !ts.isPropertyAssignment(node.parent)) return false;
  const sf = node.getSourceFile();
  const lineOf = (child) => sf.getLineAndCharacterOfPosition(child.getStart()).line;
  const below = lineOf(node.whenFalse) > lineOf(node.whenTrue);
  return ts.isCallExpression(node.whenTrue) && below && node.whenTrue;
}

// A function or constructor type: its return type, the node crapkit lists.
function functionType(node) {
  return (ts.isFunctionTypeNode(node) || ts.isConstructorTypeNode(node)) && node.type;
}

// A type outside a type alias or an interface (a parameter's, a variable's, a
// property's), or one inside a conditional type.
function inCode(node) {
  const declared = (up) => ts.isTypeAliasDeclaration(up) || ts.isInterfaceDeclaration(up);
  const conditional = ts.findAncestor(node.parent, ts.isConditionalTypeNode) !== undefined;
  return conditional || ts.findAncestor(node.parent, declared) === undefined;
}

// A function that initializes a declaration whose type holds a function type:
// `const p: (e: E) => P = (e) => {...}`.
function typedInitializer(node) {
  const parent = node.parent;
  if (!isFunction(node) || !parent || !initializes(parent, node)) return false;
  return holdsFunctionType(parent.type);
}

function holdsFunctionType(type) {
  const typed = (n) => Boolean(functionType(n));
  return Boolean(type) && (typed(type) || holds(type, typed));
}

const FUNCTION_WORDS = new Set(["async", "function"]);

// A property's name or a member's name spelled async or function.
function functionWordKey(node) {
  if (!ts.isIdentifier(node) || !FUNCTION_WORDS.has(node.text)) return false;
  return isMemberName(node);
}

function isMemberName(node) {
  const parent = node.parent;
  const member = ts.isPropertyAssignment(parent) || ts.isPropertyAccessExpression(parent);
  return member && parent.name === node;
}

// A method named get or set in an object literal or a class: `{ get(k) {...} }`.
function getSetMethod(node) {
  return ts.isMethodDeclaration(node) && ts.isIdentifier(node.name) &&
    (node.name.text === "get" || node.name.text === "set");
}

function parenTypeParam(node) {
  return ts.isParenthesizedTypeNode(node) && ts.findAncestor(node, ts.isParameter) !== undefined;
}

function overloadSignature(node) {
  return (ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node)) && !node.body;
}

const STRUCTURES = new Set([ts.SyntaxKind.IfStatement, ts.SyntaxKind.ConditionalExpression,
  ts.SyntaxKind.SwitchStatement, ts.SyntaxKind.CatchClause, ...LOOPS]);
const isStructure = (node) => STRUCTURES.has(node.kind);

// The bodies of an if (its then branch, and its else branch unless that is an
// else if, which chains rather than nests) or of a loop.
function bodiesOf(node) {
  if (ts.isIfStatement(node)) {
    const chained = node.elseStatement && ts.isIfStatement(node.elseStatement);
    return chained ? [node.thenStatement] : [node.thenStatement, node.elseStatement];
  }
  return LOOPS.has(node.kind) ? [node.statement] : [];
}

// A body without braces that holds a structure.
function bracelessBody(node) {
  const braceless = bodiesOf(node).filter((body) => body && !ts.isBlock(body));
  return braceless.some((body) => isStructure(body) || holds(body, isStructure));
}

// A `function` with type parameters: `function f<T>`, `function <T>() {}`
// (a generic method or arrow gets its row).
function genericDeclaration(node) {
  const declared = ts.isFunctionDeclaration(node) || ts.isFunctionExpression(node);
  return declared && Boolean(node.typeParameters);
}

// A JSX opening tag that is a bare name, `<p>` or `<A.B>`, with no attribute.
const BARE_TAG = /^<[A-Za-z][A-Za-z0-9]*(\.[A-Za-z][A-Za-z0-9]*)*>$/;
const PLAIN_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;
const isTag = (node) => ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node);
const openingOf = (node) => (ts.isJsxElement(node) ? node.openingElement : node);
const bareTag = (node) => BARE_TAG.test(openingOf(node).getText());
const lineBreakOnly = (node) => ts.isJsxText(node) && node.containsOnlyTriviaWhiteSpaces &&
  node.text.includes("\n");

// An attribute `name="v"` or `name={v}` whose name is one plain word: no
// spread, no value-less attribute, no `data-x` or `ns:x`.
function plainAttribute(attribute) {
  const named = ts.isJsxAttribute(attribute) && PLAIN_NAME.test(attribute.name.getText());
  return named && Boolean(attribute.initializer);
}

// An opening tag with attributes, a plain word for a name and plain
// attributes only: `<div className="a" onClick={f}>`.
function plainAttributedTag(node) {
  const opening = openingOf(node);
  const named = PLAIN_NAME.test(opening.tagName.getText()) && !opening.typeArguments;
  return named && !bareTag(node) && opening.attributes.properties.every(plainAttribute);
}

const endsAChild = (node) => ts.isJsxExpression(node) || (isTag(node) && plainAttributedTag(node));

// A child tag with attributes that follows a child expression or such a tag
// and a line break, in an element whose opening tag has plain attributes:
// `<div a="1">` / `{x}` / `<p b="2">`.
function childTagLine(node) {
  const inTaggedParent = isTag(node) && ts.isJsxElement(node.parent) &&
    plainAttributedTag(node.parent);
  return inTaggedParent && !bareTag(node) && afterAChild(node.parent.children, node);
}

function afterAChild(siblings, node) {
  const at = siblings.indexOf(node);
  return at >= 2 && lineBreakOnly(siblings[at - 1]) && endsAChild(siblings[at - 2]);
}

// The shapes crapkit misreads. Each entry: [name, reach, test(node, fn)]. The
// reach says which functions a shape marks: "own", the function whose own body
// holds it; "self", the function it is; "tail", every function holding it and
// every function after it inside the outermost of those; "after", every
// function that ends after it; "container", every function from it to the end
// of the object literal or class holding it; "line", none. fn is null for the
// last four.
// Every shape also marks its line (the node a test returns, or the one it
// passed), for a row crapkit lists where the compiler has no function.
const SHAPES = [
  // A value opening with a parenthesis right after `=`, a property's `:` or `...`.
  ["paren_value", "own", (node) => parenValue(node)],
  // The same holding another parenthesis: `y = (g(n) * 2)`, `...(c ? ({}) : {})`.
  ["paren_value_call", "tail", (node) => parenValue(node) && node.getText().indexOf("(", 1) > 0],
  // The function's name spelled in its body other than as a call of itself.
  ["name_spelled", "own", nameSpelled],
  // An identifier spelled like a keyword: `promise.catch(f)`, `{ if: 1 }`.
  ["keyword_name", "own", (node) => ts.isIdentifier(node) && KEYWORD_NAMES.has(node.text)],
  // A property named async or function: `{ async: true }`.
  ["function_word_key", "after", functionWordKey],
  // An identifier spelled like another language's keyword: a parameter `def`.
  ["foreign_keyword", "own", (node) => ts.isIdentifier(node) && FOREIGN_KEYWORDS.has(node.text)],
  ["regex_as_code", "after", regexAsCode],
  ["optional_call", "tail", optionalCall],
  ["arrow_body_below", "tail", arrowBodyBelow],
  ["type_question", "own", typeQuestion],
  // A function type `(a: T) => R` or a constructor type `new () => T`, marked
  // at its return type, the line crapkit lists it on; inside a parameter list
  // or a conditional type it throws off the rest of the file.
  ["function_type", "line", functionType],
  ["function_type_nested", "after", (node) => Boolean(functionType(node)) && inCode(node)],
  ["typed_initializer", "self", typedInitializer],
  // Marks the method and the functions after it in the same literal or class.
  ["get_set_method", "container", getSetMethod],
  // A parenthesized type in a parameter list: `form?: "a" | (string & {})`.
  ["paren_type_param", "own", parenTypeParam],
  ["generic_declaration", "self", genericDeclaration],
  // A function or method signature with no body: an overload.
  ["overload_signature", "line", overloadSignature],
  ["ternary_call_row", "line", ternaryCallRow],
  // A JSX spread attribute `<div {...props} />`.
  ["jsx_spread", "after", (node) => ts.isJsxSpreadAttribute(node)],
  // A child tag with attributes after a child expression or tag on its own line.
  ["jsx_child_tag_line", "after", childTagLine],
  // An optional chain `a?.b` in a .tsx file.
  ["optional_chain_tsx", "own", (node) => node.kind === ts.SyntaxKind.QuestionDotToken &&
    node.getSourceFile().languageVariant === ts.LanguageVariant.JSX &&
    node.getSourceFile().fileName.toLowerCase().endsWith(".tsx")],
  // An if whose body is one statement without braces.
  ["braceless_if", "own", (node) => ts.isIfStatement(node) && !ts.isBlock(node.thenStatement)],
  // A structure inside the braceless body of an if or a loop.
  ["braceless_body", "own", bracelessBody],
];
const reaching = (reach) => SHAPES.filter(([, where]) => where === reach);

function shapesOf(node, fn, reach) {
  return reaching(reach).filter(([, , test]) => test(node, fn)).map(([name]) => name);
}

// A node below `node` (at any depth) that passes `test`.
function holds(node, test) {
  let found = false;
  const visit = (child) => {
    found = found || test(child);
    if (!found) ts.forEachChild(child, visit);
  };
  ts.forEachChild(node, visit);
  return found;
}

// The right side of `=`, a declaration's initializer or a property's value.
function isValueSlot(node) {
  const parent = node.parent;
  if (ts.isBinaryExpression(parent)) {
    return parent.operatorToken.kind === ts.SyntaxKind.EqualsToken && parent.right === node;
  }
  return isSpread(parent) ? parent.expression === node : initializes(parent, node);
}

const isSpread = (node) => ts.isSpreadAssignment(node) || ts.isSpreadElement(node);

function initializes(parent, node) {
  return (ts.isVariableDeclaration(parent) || ts.isPropertyAssignment(parent)) &&
    parent.initializer === node;
}

// The value of `=`, of a property's `:` or of a spread's `...` that opens with
// a parenthesis and is no arrow function: `x = (a || b)`, `{ v: (a) ? b : c }`.
function parenValue(node) {
  if (!node.parent || ts.isArrowFunction(node) || !isValueSlot(node)) return false;
  return node.getText().startsWith("(");
}

// A default value inside a destructuring pattern (ESLint's AssignmentPattern
// that is no parameter's own initializer).
function patternDefault(node) {
  return (ts.isBindingElement(node) && Boolean(node.initializer)) ||
    (ts.isShorthandPropertyAssignment(node) && Boolean(node.objectAssignmentInitializer));
}

// {features, shapes, patternDefaults} of the function's own code, nested
// functions left out.
function own(fn) {
  const found = new Set();
  const shapes = new Set(shapesOf(fn, fn, "self"));
  let patterns = 0;
  const visit = (node) => {
    if (node !== fn && KINDS.has(node.kind)) {
      found.add("nested_function");
      return;
    }
    featuresOf(node, fn).forEach((name) => found.add(name));
    shapesOf(node, fn, "own").forEach((name) => shapes.add(name));
    patterns += patternDefault(node);
    ts.forEachChild(node, visit);
  };
  visit(fn);
  return { features: [...found].sort(), shapes, patterns };
}

// Every shape in the file: {name, reach, pos, outer}, outer being the
// outermost function holding it (itself, for a function), or null.
function placedShapes(sf) {
  const found = [];
  const visit = (node, outer) => {
    const here = outer || (isFunction(node) ? node : null);
    SHAPES.forEach(([name, reach, test]) => {
      const at = hitNode(node, test(node, null));
      if (at) found.push({ name, reach, pos: at.getStart(sf), outer: here,
        container: node.parent });
    });
    ts.forEachChild(node, (child) => visit(child, here));
  };
  visit(sf, null);
  return found;
}

const isFunction = (node) => KINDS.has(node.kind) && Boolean(node.body);

// The node a shape test points at: the node itself for true, the node it
// returned, or null.
const hitNode = (node, hit) => (hit === true ? node : hit || null);

const REACHES = {
  after: (shape, fn) => fn.end > shape.pos,
  container: (shape, fn) => fn.getStart() >= shape.pos && fn.end <= shape.container.end,
  tail: (shape, fn) => shape.outer !== null && inTail(shape, fn),
};

function reaches(shape, fn) {
  const test = REACHES[shape.reach];
  return Boolean(test) && test(shape, fn);
}

// fn holds the shape, or starts after it inside the shape's outermost function.
function inTail(shape, fn) {
  const holding = fn.getStart() <= shape.pos && shape.pos < fn.end;
  return holding || (fn.getStart() > shape.pos && fn.end <= shape.outer.end);
}

function record(sf, node, offset, placed) {
  const end = sf.getLineAndCharacterOfPosition(node.end - 1);
  const { features, shapes, patterns } = own(node);
  placed.filter((shape) => reaches(shape, node)).forEach((shape) => shapes.add(shape.name));
  return {
    kind: ts.SyntaxKind[node.kind],
    name: nameOf(node),
    start: line(sf, node.getStart(sf), offset),
    column: sf.getLineAndCharacterOfPosition(node.getStart(sf)).character,
    end: end.line + 1 + offset,
    endColumn: end.character,
    params: params(node),
    paramList: paramList(node),
    defaults: node.parameters.filter((p) => p.initializer).length,
    patternDefaults: patterns,
    returnType: Boolean(node.type),
    features,
    shapes: [...shapes].sort(),
  };
}

// {functions, marks}: marks holds [shape, line, reach] for every shape in the file.
function listing(file) {
  const { text, kind, offset } = source(file);
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, kind);
  const placed = placedShapes(sf);
  const out = [];
  const visit = (node) => {
    if (isFunction(node)) out.push(record(sf, node, offset, placed));
    ts.forEachChild(node, visit);
  };
  visit(sf);
  const marks = placed.map((shape) => [shape.name, line(sf, shape.pos, offset), shape.reach]);
  return { functions: out, marks };
}

const result = {};
for (const file of process.argv.slice(3)) result[file] = listing(file);
process.stdout.write(JSON.stringify(result));
