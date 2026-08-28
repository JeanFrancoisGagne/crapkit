# Where the cut goes

Six shapes, in the order to try them. Each one moves decisions out of a function without
moving behaviour. The sketches are generic on purpose: match the shape, not the names.

## Subtract first, always

Before any extraction, delete. A branch nothing reaches, a flag one caller passes, a
validator the boundary already ran: each deletion drops ccn and adds no name to the file.
An extraction moves complexity; a deletion removes it. Extract only what survives the cut.

## 1. Guard-first early returns

Nesting is where ccn compounds. Answer the refusals at the top, one line each, and the
body left behind is straight-line.

```python
# before: the real work sits four levels deep
def apply(order, user):
    if user:
        if user.active:
            if order.items:
                ...
            else:
                return None
        else:
            return None
    else:
        return None

# after: three guards, then the work
def apply(order, user):
    if not user or not user.active:
        return None
    if not order.items:
        return None
    ...
```

## 2. Named-predicate extraction

A condition with three or more operators is a concept without a name. Give it one. The
predicate carries its own ccn, and both halves read as one decision.

```python
# before
if row.score > cap and not row.frozen and (row.owner == me or row.shared):

# after
if is_claimable(row, cap, me):
```

## 3. Dispatch table over an elif chain

Every `elif` is another branch. A mapping is one lookup, whatever its length, and adding a
case stops touching the function.

```python
# before: ccn grows with the case list
def render(kind, value):
    if kind == "int":
        return f"{value:d}"
    elif kind == "pct":
        return f"{value:.1%}"
    elif kind == "money":
        return money(value)
    return str(value)

# after: ccn is flat
RENDERERS = {"int": render_int, "pct": render_pct, "money": money}

def render(kind, value):
    return RENDERERS.get(kind, str)(value)
```

## 4. Loop-body extraction

A loop that decides as well as iterates holds two jobs. Leave the iteration, move the
decision to a function that takes one element and returns one result.

```python
# before: the loop owns the filtering, the parsing and the accumulation
for line in lines:
    if not line or line.startswith("#"):
        continue
    key, _, raw = line.partition("=")
    if raw:
        out[key.strip()] = coerce(raw)

# after
for line in lines:
    entry = parse_entry(line)
    if entry:
        out[entry.key] = entry.value
```

## 5. Comprehension splitting

A comprehension's `for` and `if` clauses count toward ccn exactly like a statement loop, so
a dense one hides its cost behind one line. Split the filter from the shape.

```python
# before: three clauses, one line, ccn 3
rows = [shape(r) for group in groups for r in group if r.live and r.owner]

# after: the filter is named and the comprehension is one clause
def is_live(row):
    return row.live and row.owner

rows = [shape(r) for r in every(groups) if is_live(r)]
```

## 6. Config record over parameter branching

Four booleans is sixteen paths, and the branching lives at the top of the body. Take one
record instead, built by the caller, and the function stops choosing.

```python
# before: every flag is a branch inside
def report(rows, wide, totals, header, sort_desc):
    if wide:
        ...
    if header:
        ...

# after: the shape decides, the function renders
def report(rows, style):
    return render(rows, style)
```

`style` is a frozen record with defaults, so callers that wanted none of the flags pass
nothing and the sixteen paths collapse to one.

## When no cut is right

One function that is a whole command handler rather than one decision does not split into
helpers. Every extraction hands a private helper the same six arguments, and the ceiling is
met by moving the mess rather than removing it. That is a redesign: name it as one, say
what the new seam would be, and stop instead of shipping a split that regrows.
