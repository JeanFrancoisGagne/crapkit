<script lang="ts">

function ifElse(flag: boolean): string {
  if (flag) {
    return "yes";
  }
  return "no";
}

function ternary(value: number): string {
  return value > 0 ? "pos" : "neg";
}

function logical(left: unknown, right: unknown): unknown {
  return (left && right) || "none";
}

function nullish(value: string | null | undefined): string {
  return value ?? "default";
}

function defaultParam(value: number = 10): number {
  return value * 2;
}

function switchCase(kind: string): number {
  switch (kind) {
    case "a":
      return 1;
    case "b":
      return 2;
    default:
      return 0;
  }
}

function loop(values: number[]): number {
  let total = 0;
  for (const value of values) {
    total += value;
  }
  return total;
}

function withCallback(values: number[]): number[] {
  return values.map((value: number): number => {
    if (value) {
      return value;
    }
    return 0;
  });
}

function optionalChain(user?: { name?: string }): string | undefined {
  return user?.name;
}

function oneLine(value: number): number { return value + 1; }

const arrow = (value: number): number => value * 2;

/* istanbul ignore next */
function ignoredIstanbul(flag: boolean): number {
  if (flag) {
    return 1;
  }
  return 0;
}

/* v8 ignore next */
function ignoredV8(flag: boolean): number {
  if (flag) {
    return 1;
  }
  return 0;
}

async function asyncIf(flag: boolean): Promise<number> {
  if (flag) {
    return 1;
  }
  return 0;
}

class Box {
  method(value?: number): number {
    if (value === undefined) {
      return 0;
    }
    return value;
  }
}

export default { name: "Shapes" };
export { ifElse, ternary, logical, nullish, defaultParam, switchCase, loop, withCallback, optionalChain, oneLine, arrow, ignoredIstanbul, ignoredV8, asyncIf, Box };
</script>
