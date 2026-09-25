// Coverage probe shapes. ground_truth.tsv names each function's branch arms.

function ifElse(flag) {
  if (flag) {
    return "yes";
  }
  return "no";
}

function ternary(value) {
  return value > 0 ? "pos" : "neg";
}

function logical(left, right) {
  return (left && right) || "none";
}

function nullish(value) {
  return value ?? "default";
}

function defaultParam(value = 10) {
  return value * 2;
}

function switchCase(kind) {
  switch (kind) {
    case "a":
      return 1;
    case "b":
      return 2;
    default:
      return 0;
  }
}

function loop(values) {
  let total = 0;
  for (const value of values) {
    total += value;
  }
  return total;
}

function withCallback(values) {
  return values.map((value) => {
    if (value) {
      return value;
    }
    return 0;
  });
}

function optionalChain(user) {
  return user?.name;
}

function oneLine(value) { return value + 1; }

const arrow = (value) => value * 2;

/* istanbul ignore next */
function ignoredIstanbul(flag) {
  if (flag && flag > 0) {
    return 1;
  }
  return 0;
}

/* v8 ignore next */
function ignoredV8(flag) {
  if (flag && flag > 0) {
    return 1;
  }
  return 0;
}

async function asyncIf(flag) {
  if (flag) {
    return 1;
  }
  return 0;
}

class Box {
  method(value) {
    if (value === undefined) {
      return 0;
    }
    return value;
  }
}

export { ifElse, ternary, logical, nullish, defaultParam, switchCase, loop, withCallback, optionalChain, oneLine, arrow, ignoredIstanbul, ignoredV8, asyncIf, Box };
