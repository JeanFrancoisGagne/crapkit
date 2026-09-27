# Upgrading Crapkit

Upgrade the CLI with the installer that owns it, then refresh the measurements in
each repository. Saved runs describe the rules and source they measured; an upgrade
does not turn those runs into measurements of the new reader.

| Installation | Upgrade command |
|---|---|
| pip in the active environment | `python -m pip install --upgrade crapkit` |
| pip with the Python coverage extra | `python -m pip install --upgrade "crapkit[py]"` |
| uv tool | `uv tool upgrade crapkit` |

Check `crapkit --version` in the environment your shell, hook and MCP client use.
For a source checkout, follow [Development](../README.md#development). Stop a live
MCP server before upgrading on Windows; see [launcher locks](#windows-launcher-locks).

## Measure before changing marks

The release after 0.8.0 moves the reader to analysis version 12, and 0.8.0 moved it
to 11, so a marks file stamped under an older version needs one re-seed; [analysis
version 12](#analysis-version-12) and [analysis version 11](#analysis-version-11) say
what moved.
The package upgrade rebuilds the versioned analysis cache automatically, and the
first `inventory` or `coverage` after it analyzes every file again. That run's
[twin-key note](ratchet.md#twins-one-name-several-functions) names the first five
files that give one name to several functions and ends with `... and N more file(s)
define a name more than once`. Restart each client's MCP session after upgrading so
its running server uses the new code.

Keep a copy of the committed ratchet and its diff before an upgrade. In each repo:

```sh
crapkit doctor
crapkit coverage --export .crapkit/current-functions.tsv
```

Resolve doctor failures, then inspect the fresh run. `coverage` writes a measurement
without applying the ratchet. Compare the saved marks with that measurement before
running `ratchet seed`, and finish with `crapkit verify` after reviewing and committing
any mark changes.

| What changed | Required action |
|---|---|
| Analysis or lizard stamp | Follow the [metric stamp rules](ratchet.md#the-metric-stamp). Comparisons refuse incompatible stamps. |
| Function membership or same-line identity | Review the [saved-mark mapping](ratchet.md#reconcile-saved-marks) before changing keys or stamps. |
| Coverage or JUnit producer | Run a fresh lane and resolve [artifact admission errors](lanes.md#a-junit-that-says-the-run-did-not-finish). |
| Shared exports or portable baselines | Upgrade readers before writing [encoded records](portable-records.md) for them. |

### Analysis version 12

The release after 0.8.0 raises the analysis version to 12. Scores move on functions
nobody edited, so every marks file re-seeds once, with the same three commands as
[version 11](#analysis-version-11): `crapkit coverage`, `crapkit ratchet prune`, then
`crapkit ratchet seed`. Prune drops the marks of rows that go away or change key, and
seed marks the new ones. Commit the marks file. The sections below say what moves.

#### Score arithmetic

CRAP cubes `1 - cov` with two products where it called `** 3`. `pow()` differs between
C libraries, so Windows and Linux gave some scores different last bits, and a few
scores move at the 4 dp a mark is stored at. No function changes its name for this.

Over ccn 1 to 60 and every coverage fraction up to 240ths, measured on Windows:

- 21 scores print a different 4 dp value. 12 fall by 0.0001: CRAP(36, 53/120) is
  exactly 261.57225 and now prints 261.5722, not 261.5723. 9 rise by 0.0001:
  CRAP(20, 3/200) now prints 402.2687, not 402.2686.
- One score prints a different 2 dp value: CRAP(25, 19/50) is exactly 173.955 and now
  prints 173.96, not 173.95.

Seed tightens a mark whose score fell. It never raises one, so a marked function whose
score rose keeps its old mark, and `crapkit verify` reports a ratchet regression on a
function nobody edited, such as `402.2686 -> 402.2687`. Raise that mark by hand in
`crapkit-ratchet.tsv` to the value verify prints and commit it; see [a mark never rises
through verify](ratchet.md#overrides-and-the-audit-trail).

A CRAP exactly at its ceiling now reads at it. CRAP(18, 2/3) is exactly 30, but its
double is 30.000000000000004, so at `target = 30` its remedy said `add-tests`, the run
totals counted it over target and seed marked it. It now reads `ok`, seed leaves it
unmarked, and a mark it already has leaves the marks file at the next `verify` that
passes.

Two numbers that are not stored move too. `est_uncovered_paths` rounds
`(1 - cov) * ccn` half to even on the exact product, so (1 - 5/12) * 6 = 3.5 reads 4
where it read 3. `crap_load` adds the scores exactly and rounds once, so a load at a
2 dp tie can move by 0.01. The first `trend` or `report` after upgrading sums every
stored run again, once; on a store of about a million scored rows that takes a few
seconds.

#### Coverage lands on the function that owns it

Scores move on functions nobody edited:

- Under an istanbul lane a counter is placed by line and column. A statement counts
  from a function's body on and a branch from its declaration on. The statement
  istanbul writes for `const f = (x) => ...` runs at import and starts ahead of the
  arrow's body, so it now counts for the code around the arrow: an arrow no test calls
  reads 0 where it read 0.5. A ternary or `&&` that opens ahead of a callback on its
  line moves from the callback to the function around it. See [what the istanbul parser
  reads](lanes.md#what-the-istanbul-parser-reads).
- Under a coverage.py lane whose report comes from coverage.py 7.6 up to 7.13.0,
  which names no `start_line`, a nested def reads its own region where it read its
  encloser's, and a def whose body holds nothing but one-line defs reads its own where it
  read the first one's. See [where a function's region
  starts](lanes.md#where-a-functions-region-starts).
- A function coverage.py or istanbul was told to leave out (`# pragma: no cover`,
  `istanbul ignore next`, `v8 ignore next`, and from coverage.py 7.10.1 a stub whose
  body is `...`) reads the new flag `excluded` at `crap = ccn`, where it read cov 0 at
  `ccn^2 + ccn`. Its remedy turns from `add-tests` to `ok` or `decompose`. A client that
  checks `flag` against the four older values, or reads the coverage summary's four
  counts, should accept `excluded` too. See
  [flags](../README.md#flags-why-a-coverage-number-is-missing).

#### Rust rows

Rust's numbers move:

- A signature decides nothing. A `where` clause, a `?Sized` bound and a `for<'a>`
  binder no longer add to ccn.
- A `for<'a>` binder and the `for` of `impl Trait for Type` are no loop in ccn or
  cognitive inside a function's body too, and a `?Sized` bound adds no ccn anywhere. A
  test function that implements a trait for its stub falls by 1 or more.
- A `||` or `&&` with no operand before it is no operator. `move || n`, `f(|| 0)` and
  `|&&x|` cost nothing in ccn, cognitive or nesting.
- A let-else counts one decision in ccn, like the `if let` it replaces.
- `?` costs nothing in cognitive or nesting and keeps its 1 in ccn.
- `loop` is a loop in cognitive and nesting.
- `catch`, `switch`, `foreach`, `case` and `def` are names, not structures.
- A trait's required method, a foreign function and a `fn` pointer type have no row,
  and a function one of them swallowed gets its own. Prune drops the marks of the
  rows that are gone, and seed marks the new ones.
- `params` counts a parameter of tuple or generic type, or one written as a tuple,
  array or struct pattern, once.
- A parameter typed `&&T` reads `& &` in the function's long name, so that function
  takes a new ratchet key. Prune drops the old one.
- In Python and shell, a name spelled `switch` no longer adds to ccn_mod. The gated
  ccn is unchanged.

#### Go, Zig and shell readers, and `//` comments

crapkit reads Go and Zig signatures to where the language ends them and ends a `//`
comment at its line in every language but C, C++ and Objective-C. Here is what moves:

- Rows appear. A function that had no row is listed: one after a package-level function
  type or a Zig `extern fn` prototype, a Zig function named `@"..."`, a Go method after
  a `}` that closes nothing, such as the one a Go type switch left, and a function whose
  signature sits on the line after a `//` comment that ends in a backslash (`// C:\dir\`)
  in Go, Zig, Java, JavaScript, TypeScript, TSX, Vue, Swift or Rust.
- Rows go away. A function type such as `var cb func(int) error` no longer opens a
  function, and a composite literal of functions, `[]func(){f, g}`, is no longer an
  anonymous row.
- Keys change for two kinds of literal. A function literal inside a package-level
  composite literal reads `(i int)` where it read ` i int`, and a package-level literal
  whose result is a function type reads `(a int)` where it read `(a int)func b int`. No
  named function's long name changes.
- `ccn` rises. The function around a function type gets back the block the type took.
  A function with a Go type switch reads its whole body, where it ended at the switch's
  `}`, and so does one whose result type holds braces, one with a `//` comment ending in
  a backslash in any of the languages above, and a Zig function whose multiline string
  holds a `}`. A Go `select`
  with a case reads one decision where it read none. A shell `a ? b : c` inside `(( ))`
  or `$(( ))` counts one. Any of these can be over its ceiling and fails the gate the
  next time its file changes.
- `ccn` falls. A Zig switch reads as one decision in `ccn`, the smaller of the two
  columns, where it read one per prong and one more for `else =>`. An `if`, `and` or
  `or` in the text of a Zig multiline string no longer counts. A shell `for ((;;))`
  reads one lower.
- Reporting columns move. `params` moves for parameters of function type or with a
  braced type, and for a package-level literal's parameters, which read 0. A Zig `try`,
  an optional's `?` and an error-set `||` stop adding to `cognitive`, and `try` and `?`
  stop adding to `nesting`. A shell glob's `?` stops adding to `cognitive`.

#### Line ends

Some shell numbers, the line numbers of functions that sit below certain comments, and
some JavaScript and TypeScript coverage move:

- The shell reader ends a heredoc line at LF only, as bash does. It used to end one at
  a vertical tab, form feed, `\x1c`-`\x1e`, NEL, U+2028 and U+2029 too. A body line
  such as `note<FF>EOF` closed the body early and the lines up to the real `EOF`
  counted as code; code after one of those characters on the line that opens a heredoc
  read as body. `ccn`, cognitive, nesting and NLOC move for the function that holds
  such a line, up where body text had counted and down where code had been blanked. A
  function the change puts over its ceiling fails the gate the next time its file
  changes.
- lizard counted a comment's lines with Python's `str.splitlines`, which also ends a
  line at a vertical tab, a form feed, `\x1c`, `\x1d`, `\x1e`, U+0085, U+2028 and
  U+2029. A comment holding one of them moved every function below it down one line
  per character. A comment now counts one line per LF, as git, Python's compiler,
  coverage.py, c8 and `@vitest/coverage-v8` count it. Every such function's `start` and
  `end` move up to the lines it sits on, and a function the move had pushed onto its
  neighbour's lines now reads its own coverage and its own CRAP.
- An istanbul lane's line numbers land on crapkit's own lines. `@vitest/coverage-v8`
  ends a JavaScript line at LF only; Babel (jest, nyc, `@vitest/coverage-istanbul`) and
  TypeScript source maps also end one at U+2028 and U+2029; crapkit ends one at LF,
  CRLF and a lone CR. Below a lone CR, or a U+2028 in a string or a comment, the
  coverage of one function went to a neighbor, so `cov`, CRAP and the uncovered lines
  `verify` checks a diff against move for JavaScript and TypeScript functions in such
  files. A function that reads less covered now can go over its ceiling.

### Next analysis version: C, C++, Objective-C and Java rows

The release after 0.8.0 reads the functions and parameters of the C family and Java in
new ways, so it raises the analysis version and every marks file re-seeds once, with the
same three commands as version 11 below.

- `params` counts each declaration in a C, C++, Objective-C or Java parameter list,
  named or not: `f(int*, char)` reads 2, `f(const int arr[4])` and Java's
  `main(String args[])` read 1, and an Objective-C method counts its arguments.
  `params` is reported and never gated.
- Functions that had no row get one: those after a `<` comparison in a default
  template argument or a member initializer, which lizard read as a template bracket,
  a function returning a function pointer after a return type that ends in `*` or
  `&`, `char *(*get(void))(void)`, a C++20 function with a trailing requires-clause,
  `void f(T t) requires C<T>`, the functions of a namespace whose head holds a macro
  or an attribute, `namespace std _GLIBCXX_VISIBILITY(default)` in every libstdc++
  header, a constructor with a function-try-block, `S::S(int a) try : x(a) {`, the
  function after a constructor whose member initializer list ends in a pack
  expansion, `S(B... b) : B(b)... {`, and the member functions of a class defined
  inside a function. The function around such
  a class no longer pays for its members' decisions, so its `ccn` and `cognitive`
  fall. In Java, methods get rows after an
  annotated local variable, an enum constant with a body or an annotation element
  with a default, and inside a constant's body, an interface field's anonymous class,
  or a record or interface declared in a method, whose `ccn` falls the same way. A
  method whose anonymous or local class ended on a field or an abstract method gets
  its row back under its own name, with the class's field lines in its `nloc`. A Java
  text block is one string, so a `{` or an `&&` between two quotes in its text is no
  longer code that hides the next method or adds to `ccn`. A newly listed function
  over its ceiling fails the gate the next time its file changes, and `ratchet seed`
  marks it.
- Rows that were not functions go: a declaration whose trailing return type holds
  braces, an Objective-C instance-variable block, a C++20 concept's requires-expression
  (`requires( T a)`), a row named after the first statement of a function with a
  requires-clause (`if( t)`), a namespace read as one function named after its
  head's macro (`_GLIBCXX_VISIBILITY( default)`), each handler of a function-try-block
  (`catch( ...)`), a constructor's first member initializer read as the constructor
  (`x( a)`), a Java enum constant, an annotation element with a braced default, a Java
  field's anonymous class, and a Java record declared first in a class or interface
  body, which read as a method named after it.
- Rows named after an attribute take the function's name: `__attribute__((noinline))`,
  `API_AVAILABLE( ios(10))`, `LOCKS_EXCLUDED( mu)` after a constructor or destructor,
  or `)` for an Objective-C method, and Java rows named
  after an annotation with arguments, `InlineMe( replacement = ...)`. A member of a
  class declared with an export macro or an attribute, `class Q_CORE_EXPORT QString`
  or `class __declspec(dllexport) Foo`, reads `QString::size`, where it read
  `Q_CORE_EXPORTQString::size`, or had no class in its name, and a member of `namespace
  ns ABI_TAG` or `namespace a::inline b` reads `ns::f` or `a::b::f`, where it read
  `nsABI_TAG::f` or `a::inlineb::f`. A C-family function whose
  declarator sits in parentheses takes its own name: `int (*get(int k))(int)` reads
  `get( int k)`, where it read `int( * get(int k))( int)`, `static constexpr T
  (max)()` reads `max()`, where it read `T( max)()`, and a name a macro builds,
  `STRINGLIB(find)(...)`, reads `find(...)`. A Java method
  inside a method's anonymous or local class reads `A::go.run()`, where it read
  `A::A::go.run()` or took the name of a class declared before the method. A Java
  method inside an enum, an interface or a record carries its name, `A::F::g()` where
  it read `A::g()`, and a sealed class's methods read `Shape::area()`, where they read
  `ShapepermitsCircle::area()`. The name is a new ratchet key, and so is the long name of a
  function after one whose default argument holds a parenthesized `<`:
  `g( int a , int c)` where it read `g(int a,int c)`.
  `ratchet prune` drops the old mark and `ratchet seed` marks the function under its
  new name if it is over its ceiling.
- A C++ `&&` that declares a reference costs nothing: `for (auto&& x : r)`,
  `static_cast<T&&>(v)`, `auto&& w = f();` and a lambda taking `auto&&` lose the `ccn`,
  `cognitive` or `nesting` point it cost, so marks can tighten on the next seed. A
  logical `&&` followed by an assignment in the same condition, `while (n > 0 && (p =
  next(p)) != 0)`, counts in `ccn` again, which can put a function over its ceiling
  and fail the gate the next time its file changes. A function whose parameter list
  held a `&&` can read one `nesting` level deeper, the depth the same body reads
  without it; `nesting` is never gated.
- A function with a function-try-block, `int main() try { ... } catch (...) { ... }`,
  counts its handlers: each `catch` adds 1 to `ccn` and 1 to `cognitive`, as it does
  in a try statement, which can put the function over its ceiling and fail the gate
  the next time its file changes.
- A constructor whose member initializer list ends in a pack expansion, `S(B... b) :
  B(b)... {`, counts its body's decisions, where it read `ccn` 1, which can put it
  over its ceiling and fail the gate the next time its file changes.

### Unreleased: the Swift and Rust readers

The next release reads Swift and Rust in new ways and moves to the next analysis
version, so every marks file re-seeds once, with the same three commands as
[version 11](#analysis-version-11): `crapkit coverage`, `crapkit ratchet prune`,
`crapkit ratchet seed`.

Swift:

- A function that `super.init(...)`, `.init(...)`, `r.get()`, `case .get`,
  `Socket(protocol: p)`, `return type`, a `#fileID` default, `if #available(...) {` or a
  closure after a comma hid is listed, and the function that held it reads only its own
  lines. A failable `init?` or `init!` is listed as `init`. A function named by a raw
  identifier, ``func `keeps onboarding if offline`()``, is listed under that name,
  backticks included; a Swift Testing suite gains a row per such test. A newly listed
  function can be over its ceiling and fails the gate the next time its file changes.
- A function listed before keeps its long name, so its mark keeps its key. A row that
  named no function is gone: `init id : id` for a `super.init(id: id)` call, `get` for
  `r.get()`. `ratchet prune` drops its mark.
- ccn falls where the `case` of `if case`, a keyword argument label (`for name:`) or an
  optional mark (`(any Error)?`) counted. It rises by 1 for each `??` and for each `?`
  of an optional chain after a name (`a?.b`, `self?.done()`), which counted nothing.
  `Empty?.none` and `Int?.some(1)` name a member of the optional type and chain
  nothing. A chain after `)` or `]` (`f()?.g`) keeps its 1 and loses the nesting level
  it opened. `params` and `nesting` fall where a comma inside one parameter or a `try`
  counted; neither is in the score.
- A function whose string interpolation holds a string with a brace in it,
  `"\(f("{"))"`, is listed; it had no row. A `&&`, `||`, `??` or `?:` inside `\( )`
  now counts in ccn and cognitive, and an `if` or `for` in the text of a multi-line
  string no longer does.

Rust:

- A function with an attribute on the same line as its `fn`, `#[inline] fn f() {`, is
  listed; it had no row. A decision or a brace after a raw string (`r#"..."#`), a raw
  identifier (`r#type`) or an attribute on the same line now counts.

### Next analysis version: shell and PowerShell rows

The release after 0.8.0 moves shell's and PowerShell's numbers and names, so it raises
the analysis version and every marks file re-seeds once, with the same three commands
as version 11 below. Prune matters here: some rows are renamed and some phantom rows
go, and their marks are left under names the run no longer has.

- A shell function's `nesting` reads how deep its blocks go. lizard's ND column closed
  a level only on a `}` or at a `;`, and shell closes `if`, loops and `case` with `fi`,
  `done` and `esac`, so every block leaked a level: seven ifs side by side read 6, four
  nested read 3, and a `case` read 0. They read 1, 4 and 1 now, and `&&` or `||` opens
  no level. `nesting` is reported and never gated, so no verdict moves with it.
- A command inside a quoted substitution counts: `x="$(cmd || true)"` reads ccn 2
  where it read 1, as `x=$(cmd || true)` always did. Backticks inside quotes, `$(( ))`
  and a substitution inside `${v:-...}` count the same way. A heredoc opened inside a
  quoted substitution, as in `v="$(node - "$f" <<'JS'`, is a body, so its program adds
  no ccn and no NLOC, and so is one opened on the line that closes a multi-line
  quoted substitution. On a large consumer repo 146 of 1,613 shell functions rose by 1
  to 20, with this and the `#` change below, and 2 fell, by 1 and 23. A function the
  rise puts over its ceiling fails the gate the next time its file changes.
- A case statement inside a quoted substitution counts its arms, and a function whose
  body is a subshell, `f() ( case ... esac )`, ends at its own `)` rather than at the
  first pattern's. Such a function can gain lines and ccn: one on a large consumer repo
  went from 68 lines and ccn 26 to 140 lines and ccn 46.
- A shell reserved word counts only where shell reads one, so `echo done`,
  `git for-each-ref`, `done=1` and a `--exit-if-exists)` pattern no longer add a
  decision or open or close a block, and a `;;` in `for ((;;))` is no case arm. These
  only lower numbers: on a large consumer repo 3 functions fell, by up to 1 in ccn and
  2 to 24 in cognitive.
- A `#` inside a word, as in `(( 8#$mode ))` or a regex's `[#/]`, is part of the word
  and opens no comment. The rest of such a line had counted nothing, so a function
  with an `&&` after it rises in ccn (5 on a large consumer repo, by 1 to 5), and a
  `))` or `then` hidden there had left the function open to the end of its file. A
  file like that gains every function after it: one script went from 16 rows to 101,
  and a new row can be over its ceiling.
- A shell `?` outside arithmetic, as in `ls a?b` or a `=~` regex, no longer costs a
  ternary's cognitive +1 and its nesting, nor do the words of `echo break 2`, an array
  literal's words on later lines, or `goto`. These only lower numbers (on a large
  consumer repo 14 functions fell in cognitive, by 1 to 4), except that a `?:` inside
  `$(( ))` or `(( ))` now adds 1 to ccn, as a C ternary does.
- A PowerShell expression inside a `$( )` subexpression in a double-quoted string
  counts: `"$($a -and $b)"` reads ccn 2 where it read 1, up to eight levels of
  parens deep. Its cognitive score rises by the same decisions, and an `if` or loop
  there opens a `nesting` level. On a large consumer repo 6 of 339 PowerShell
  functions rose by 1 in ccn and 1 or 2 in cognitive, and one by a nesting level.
  Quotes inside the subexpression pair among themselves, so a function that held
  `"$(Get-Item "x{")"` and had no row now has one, and can be over its ceiling.
- PowerShell keywords count in any case where a statement starts: a capitalized `IF`,
  `-Or` or `ForEach` now costs what its lower-case spelling costs, and a `Default` arm
  is free. A keyword word that is a command, an argument or a member costs nothing in
  any case (`$xs | foreach { }`, `git switch main`), and neither does the `?` in `$?`.
  PowerShell 7's `&&`, `||`, `??`, `?.` and `?[` count once each. Gated `ccn` can rise
  or fall. `-and` and `-or` stop adding a nesting level.
- A PowerShell switch arm costs one point whatever its pattern or subject holds, and
  `ccn_mod` no longer reads one above `ccn_std` for each switch. The arms still cost
  their points in both columns, so the gate reads a twelve-arm switch as 13, as before.
- PowerShell rows appear for `Function Name`, `function script:Name`, `function
  Get.Name` and a function that followed a stray `configuration` or `filter` word, and
  the phantom rows those words opened go. A class method's decisions leave the function
  that declares the class. A function that gains a row, or keeps one and gains
  decisions, can be over its ceiling and fails the gate the next time its file changes.
- A shell function whose name holds `-`, `.` or `:` keeps the whole name: `do-thing`
  read `thing`, and `function log::info` had no row. A keyword inside a longer word,
  such as `select` in `xcode-select`, counts nothing, so some cognitive scores fall
  sharply.

See [the per-language gotchas](configuration.md#per-language-gotchas) for what each
reader counts.

### Next analysis version: cognitive complexity per language

The release after 0.8.0 reads `cognitive` by each language's own rules, so it raises
the analysis version and every marks file re-seeds once, with the same three commands
as [version 11](#analysis-version-11): `crapkit coverage`, `crapkit ratchet prune`,
then `crapkit ratchet seed`. `ccn`, coverage and every function's name stay as they
were, so this change moves no mark and no gate verdict: the re-seed only stamps the
marks with the new version. Commit the marks file.

Expect the `cognitive` column to change in `next-item --json`, `brief`, exports and
the MCP tools on the first run after upgrading, most often by 1:

- It drops where the pass charged recursion that was not there, the common case in
  Python: a method that calls another object's method of the same name, as an
  `__init__` calls `super().__init__()`, a method that wraps the module function it
  is named after, a local variable or import named like its function, or a C++, Java
  or Swift overload that forwards to another overload of its name.
  It rises in Go, shell, PowerShell, Java and C++ functions that call themselves,
  which cost nothing before, and in a Rust, Swift or Zig function that calls itself
  through its type's name (`R::spin(n - 1)`).
- A sequence of logical operators continued on the next line, or split by a comma in a
  call's arguments, costs +1 once where it cost 2. A negated group such as
  `a && !(b && c)` costs its own +1, and `??` costs nothing. Each operand of a
  conditional expression holds a sequence of its own, so `x && y ? a && b : c && d`
  costs 4 where it cost 2, and so does a group compared: `a && (b && c) == d && e`
  costs 2 where it cost 1.
- Swift's `repeat` and `guard`, Rust's `loop`, Go's `select`, PowerShell's `trap` and
  a Python `match` statement cost what a loop, an `if` or a `switch` costs; they cost
  nothing before. Words that are a keyword in another language, such as Python's
  `c.do(1)` or JavaScript's `p.then(g).catch(h)`, cost nothing.
- A structure inside a braceless body (`for (...) if (x) visit(x);`) costs one more in
  C, C++, Objective-C, Java, JavaScript, TypeScript and Zig. A structure after a block
  that holds a JavaScript or TypeScript arrow with a block body costs one less.

In Python the same pass measures `nesting`, which moves too: a comprehension's level
closes with its bracket, so `[p for p in a] + [q for q in b]` reads 1 where it read 2,
and a `match` statement opens a level. The other languages keep lizard's `nesting`.
Over 12,432 functions in 20 open-source projects, 1,082 move `cognitive`, 851 of
them by 1 and 1,014 by 3 or less, and 69 Python rows move `nesting`. The
[changelog](../CHANGELOG.md) lists every rule with an example.

### Next analysis version: nesting in every language

The release after 0.8.0 moves `nesting` outside Python, and `cognitive` in a few
functions in every language, so it raises the analysis version and every marks file
re-seeds once, with the same three commands as version 11 below.

- A function's `nesting` in C, C++, Objective-C, Java, JavaScript, TypeScript, Go,
  Rust, Swift, Zig, PowerShell and shell reads the depth crapkit's cognitive pass
  measures, the way Python has since 0.5.0. lizard's ND column, which those rows read
  before, opened a level for `&&`, `||`, `case` and `try` (PowerShell's `-and` and
  `-or` too) and lost one at a `}` or a `;`, so most rows that move go down: a switch
  reads 1 whatever its case count, a condition's operators add nothing, and a shell
  line such as `[ "$a" ] && [ "$b" ] || echo no` opens no level. Rows go up where ND
  lost a level, as with nested loops, an `if` inside an `else`, a Rust `match`, or a
  shell or PowerShell `case`/`switch`, which ND read as no level.
- `cognitive` drops in a function where a guard without braces (`if (a) return;`)
  comes before a block that is not a structure's, such as a bare `{`,
  `synchronized`, `@autoreleasepool` or a lambda's body: the structures inside that
  block no longer pay a level of nesting for the guard. 9 of 21,099 functions in the
  measuring corpus move.
- Both columns go up where a structure's header holds a `{` of its own, a literal or
  a lambda's: Go's `for _, x := range []string{"a", "b"} {` and table-driven tests,
  C++ initializer lists, Java array initializers, destructuring in a `for`. The body
  after that header now counts as the structure's, so the structures inside it read
  one level deeper and cost one more. In Go code expect about 1 function in 100 to
  cost more; 264 of 24,540 did in the Go standard library and actionlint. Both
  columns go down in a Swift function with an argument label spelled `for`, whose
  body no longer reads as a level. In a PowerShell function with a `[switch]`
  parameter only `cognitive` goes down: lizard's ND never read the word, so its
  `nesting` stays.
- `cognitive` goes down where a word spelled like a structure keyword is a name: a
  `do` in Go and Zig, which have no do-while (`do(n)`, a method or closure named
  `do`), a `do` after a `.` in any language but Python (`obs.do(fn)`), and an `if`,
  loop, `switch` or `catch` followed by a `:`, such as an object's key `{if: 1}` or a
  Swift argument label `g(for: x)`. The word no longer costs +1, and in Go the
  structures inside a method named `do` no longer pay a level of nesting for it. 6
  of the 24,540 Go functions moved, one of them from 45 to 9. A keyword key inside a
  function also stops opening a `nesting` level per key. A Go variable named `while`
  stops costing +1 at each use, since Go has no `while` loop.
- Both columns go up, in every language, where a `while` loop comes right after a
  `}` that closed anything but a do-while's block, such as an `if` block, an object
  literal or a Python dict. The loop read as the tail of a do-while, cost nothing and
  opened no level. 16 of the 21,099 functions in the measuring corpus move
  `cognitive`, 3 of them in Python, and 6 move `nesting`.
- In Zig, `nesting` goes down in a function whose switch has an `else =>` prong with
  a block, or where an else with a payload and no braces (`else |err| return err;`)
  comes before another block. `cognitive` goes down where `else |err| if` links an
  else-if chain, which now costs +1 as `else if` does. 37 of 3,475 functions in the
  Zig standard library lose a level and 12 cost less; 8 gain a level, where a
  `switch` follows `else |err|` and now sits in the else's body as it does after a
  plain `else`.
- In Zig both columns go down in a function whose return type holds an `if`
  (`fn f(x: anytype) if (A) u8 else u16 {`), whose whole body read one level deep,
  as it did in 0.8.0. 3 of the 3,487 Zig standard library functions lose a level
  and cost less.
- `nesting` and `cognitive` are reported and never gated, so no verdict moves with
  them. Expect both columns to change in `next-item --json`, exports and `brief` on
  the first run after upgrading; the [`nesting` row](agent-json.md#item-fields) says
  what opens a level.

### Analysis version 11

0.8.0 reads Python defs in five new ways. Each one changes some functions' names or
numbers, and the stamp records the rules, so every marks file re-seeds once:

- A def with a PEP 695 type parameter list is named by its name. `def f[T](a: int):`
  read `]( a : int )`, and every generic def in a file that took the same parameters
  collided on that key. A file refused for a generic def with no annotated parameter
  now scores.
- A def nested three or more deep names each enclosing def once: `a.b.c( x )`, where
  it read `a.a.b.c( x )`.
- A def whose body sits on its colon line, such as `def one(x): return x`, is listed
  as its own function. Before, no report showed it and the lines after it counted
  toward it. A def that encloses one can gain conditions it had lost. A same-named
  def after it moves to the next twin key: where a one-line `f( x )` sits above a
  multi-line `f( x )`, the multi-line def is now `f( x )#2`, a mark recorded under
  `f( x )` binds the one-line def, and `ratchet seed` marks `f( x )#2` if it is over
  its ceiling.
- Cognitive complexity and nesting count a def's body from the colon that ends its
  signature, so a one-line body counts and a signature's continuation lines do not.
- A Python file that ends inside a def's signature is refused and names that def;
  before, only a nested def was, and the file scored without it.

The one-line change moves `ccn` for the def and for the defs whose lines it used to
take, and a generic def with a constrained bound and a line break after a default,
which read two lines at ccn 1, now reads its whole body. A newly listed def, or an
enclosing def that read short before, can be over its ceiling and fails the gate the
next time its file changes. Under a coverage.py lane a def whose body starts on the
line its signature ends, a one-line def or a body on the last line of a signature
that spans several lines, scores as uncovered with remedy `split-lines`, because
coverage.py reads that body as the `def` statement that runs at import.

Version 11 also reads JavaScript and TypeScript template literals whole. lizard ended
a template at the first backtick inside it, so a template nested in another's `${...}`,
an escaped backtick, or a brace inside a string within `${...}` hid every function
after it in the file: each was folded into the function around it or not listed at
all. Those functions are now listed, and the function that held them reads only its
own lines and branches. A newly listed function can be over its ceiling and fails the
gate the next time its file changes. A function written inside `${...}` is still not
listed.

After upgrading, in each repo:

```sh
crapkit coverage
crapkit ratchet prune
crapkit ratchet seed
```

`coverage` measures under version 11, and `ratchet prune` drops the marks left under
the old names. `ratchet seed` then stamps the marks with the metric of the run it
reads, so a seed from a run 0.7.x measured keeps the old stamp. Prune goes first
because a marks file with no `# crapkit-keys=1` line keeps the old key format while
any mark names a function the run lacks, and that format cannot key a function that
shares its start line with another, so seed refuses to add one. When a failed verify
pins the baseline, seed and prune both read the pinned run: pass the new run's id to
each, `crapkit ratchet prune --baseline N` then `crapkit ratchet seed --baseline N`;
their lines and verify's refusal name it. Review the diff and commit it before the
next `crapkit verify`.

### Analysis version 10

Analysis version 10, in 0.7.0, replaced version 9 from 0.6.0. It separates
JavaScript and TypeScript expression callbacks that older readers missed. Current
rows also carry an `occurrence` for functions sharing a start line. These changes can
shift anonymous ordinals even when functions begin on different lines. Fresh
coverage cannot prove which old function owned a mark. A refusal asks for that
review; replacing a stamp alone does not complete it.

Unambiguous legacy keys remain readable. Ambiguous groups and anonymous JS/TS marks
without current reader proof require the procedure in
[same-line function identity](ratchet.md#same-line-function-identity). Existing claims
without that proof continue to hold their whole name group until released,
removed by an explicit `runs prune` under its age rule, or the whole group becomes
healthy. Ordinary queue reads do not expire claims.

## Saved state and command behavior

Keep `.crapkit/crap.sqlite`: it holds run history, test baselines and override audits.
Commands manage disposable analysis and history caches themselves. Use
`crapkit runs list` to inspect the trusted baseline; a failed verify still prevents
a newer coverage run from silently becoming the baseline.

Automatic reuse requires the same clean HEAD and unchanged configuration,
environment and artifact bytes. Since 0.8.0 a lane can list the paths its command
reads as `inputs`, and `--reuse-unchanged` then reuses it across commits while
nothing under those paths, its lane table or its `env` changed. The reuse proof
covers that field, so the first `--reuse-unchanged` after upgrading to 0.8.0 reruns
every lane once, and an older stamp without the proof reruns its lane; each rerun
prints `lane 'x': rerunning:` and the reason. Ignored files, installed dependencies and
external services remain outside this proof. See
[artifact reuse](lanes.md#reusing-artifacts) before choosing an explicit
saved-artifact read.

The full-suite guard reads a lane command the way the shell that runs it does, operators
and redirections that touch a word included. Lanes it refused for `py.json&& python -m
coverage json`, `>"lane.log"` or `tests>lane.log` now load, and so do these Windows
shapes: `>lane.log ; 2>&1`, where cmd.exe drops the `;` between the two redirections,
and a block such as `(python -m pytest --cov=src ) > lane.log`, where `doctor` now finds
python instead of naming `(python`. One Windows shape that loaded now exits 3: a
caret-escaped quote around an `&`, as in `-k ^"x & python -m pytest pylib/unit^"`,
because cmd.exe starts a second pytest there and the coverage came from its narrowed
run. [How a lane command is read](lanes.md#how-a-lane-command-is-read) has every rule.
No score moves.

`test_retention_days` and `test_retention_count` are ignored. `crapkit doctor` warns
once for each key a config sets; delete them. Test evidence retention is now the
development runner's `--retention-days` and `--retention-count`, and
`crapkit clean` recovers abandoned mutation checkouts only.

Every mutation worker uses a detached worktree, including a single worker.
The normal pool is retained; concurrent callers use temporary worktrees that
cleanup removes. Budget disk space for the pool and use
`crapkit mutate --drop-pool` to reclaim it.
[Mutation worktrees](configuration.md#mutation-worktrees) owns the input, link,
concurrency and cleanup rules. [Command cleanup](lanes.md#the-kill-takes-the-whole-process-tree)
describes Windows Jobs and POSIX process groups. These are process-lifetime controls,
not a sandbox for configured test commands.

A portable baseline from `verify --emit-baseline` names the tests its run failed,
and `verify --baseline-tsv` forgives them as a verify against the store does.
Re-emit a committed baseline file once on the default branch. Until then it
forgives no failure, as before, and verify warns when it reports a new failure
against it. [The portable baseline's first
line](portable-records.md#the-portable-baselines-first-line) gives the format.

Git filenames retain their literal identity through scoring and output. Coverage
paths still have to name the measured tree. Use the documented
[CLI path rules](configuration.md#file-paths-and-root-discovery) and
[portable record reader](portable-records.md) when automating around exports.
JSON stays at `schema: 1`; consumers must accept added fields.

### Unreleased: the churn window ends at HEAD's commit date

The churn window reaches `churn_window_months` back from HEAD's commit date, never
from the day of the run. 0.8.0 and earlier cut it with `git log --since=12.months.ago`,
which git reads against today's date, so a repo measured a year after its last commit
had no churn and `worklist` listed every file as dormant.

- Churn weights, `risk`, the order `worklist` and `next-item` hand out, and the pairs
  `coupling`, `brief` and `worklist --batches` rank change for any repo whose HEAD
  commit is older than the day it is measured. The longer the repo has sat still,
  the more history enters the window. CRAP scores and ratchet marks do not change,
  so nothing re-seeds.
- The churn caches move to `churn-cache-v3.json`, `churn-log-v3.z` and
  `coupling-cache-v2.json`. The files 0.4.5 to 0.8.0 wrote hold a window cut at the
  wall clock, so this version never reads them: its first churn read walks the
  window once, seconds on a large history, then stays warm. It leaves those files
  alone, so an older crapkit that shares the working tree, a pinned CI install say,
  keeps its caches warm. Once no older crapkit runs there, delete
  `.crapkit/churn-cache-v2.json`, `.crapkit/churn-log-v2.z`,
  `.crapkit/churn-log-v2.json` and `.crapkit/coupling-cache-v1.json`; the log alone
  can hold several megabytes.

## Plugin and MCP clients

After upgrading the intended CLI, refresh Claude Code's marketplace before updating
its user-scope plugin:

```sh
claude plugin marketplace update crapkit
claude plugin update crapkit@crapkit --scope user
crapkit doctor --plugin-root
```

Restart existing Claude Code sessions to apply the plugin update. The doctor check
compares the installed plugin with the `crapkit` launcher on PATH. It does not reload
an existing session. A failed, malformed or undecodable launcher probe is a failure,
not a version match.

For an installed Codex plugin, refresh its marketplace and install the current copy:

```sh
codex plugin marketplace upgrade crapkit
codex plugin add crapkit@crapkit
codex plugin list --marketplace crapkit --json
crapkit doctor --plugin-root PATH
```

Use the installed Codex plugin directory for `PATH`, not the marketplace's source
checkout. In the default cache this is
`~/.codex/plugins/cache/crapkit/crapkit/VERSION`, using the installed version from
the listing. With no explicit path, doctor checks Claude Code's cache instead.
Use the three skills and MCP server in Codex. The advisory hook instructions in
the README configure Claude Code's PostToolUse event. Start a new Codex task to
load updated plugin skills and tools.

Start fresh MCP sessions after upgrading so their server uses the installed code. Other
MCP clients use the [stdio setup](agent-json.md#mcp-server); skill copies and custom
hook entries need their own update. Run packet commands as supplied, in the
environment that owns the intended CLI, to retain literal arguments and exit codes.

## Windows launcher locks

A running `crapkit.exe mcp` can hold the console launcher open. An upgrade then
fails with Windows error 32 even if some package files were already updated.

1. Stop the Crapkit MCP server or the agent session that owns it.
2. Rerun the same upgrade command and require a successful installer result.
3. Check `crapkit --version`, restart the client, and check plugin compatibility.

Use the installer to repair the launcher instead of copying executables between
environments. The CLI version alone does not prove an interrupted install finished.

## Release evidence

The [implementation report](architecture/2026-09-07-implementation/REPORT.md)
records complete Windows source and Linux installed-wheel verification, independent
reviews and repeatable performance probes. Its benchmark tables distinguish
synthetic duplication input, fixture setup and a fixed unit subset from complete
suite runs. They do not claim a whole-suite speedup. Hosted CI dispatch and macOS
runtime execution were outside that local verification.
