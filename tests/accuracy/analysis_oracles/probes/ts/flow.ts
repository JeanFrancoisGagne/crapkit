export function straight(a: number): number {
  const b = a + 1;
  const c = b * 2;
  return c;
}

export function grade(score: number): string {
  if (score >= 90) {
    return "A";
  } else if (score >= 80) {
    return "B";
  } else {
    return "C";
  }
}

export function nestedInElse(a: boolean, b: boolean): number {
  if (a) {
    return 1;
  } else {
    if (b) {
      return 2;
    }
  }
  return 0;
}

export function fourDeep(a: boolean, b: number[], c: boolean, d: boolean): number {
  if (a) {
    for (const x of b) {
      while (c) {
        if (d) {
          return x;
        }
      }
    }
  }
  return 0;
}

export function flatSeven(a: number): number {
  if (a === 1) { return 1; }
  if (a === 2) { return 2; }
  if (a === 3) { return 3; }
  if (a === 4) { return 4; }
  if (a === 5) { return 5; }
  if (a === 6) { return 6; }
  if (a === 7) { return 7; }
  return 0;
}

export function threeLoops(xs: number[][][]): number {
  let total = 0;
  for (const a of xs) {
    for (const b of a) {
      for (const c of b) {
        total += c;
      }
    }
  }
  return total;
}

export function findPair(grid: number[][], target: number): boolean {
  outer: for (const row of grid) {
    for (const cell of row) {
      if (cell === target) {
        break outer;
      }
    }
  }
  return false;
}

export function pick(a: boolean, b: number, c: number): number {
  return a ? b : c;
}

export function countDown(n: number): number {
  do {
    n -= 1;
  } while (n > 0);
  return n;
}

export function keysOf(o: object): string[] {
  const out: string[] = [];
  for (const k in o) {
    out.push(k);
  }
  return out;
}

export function kind(n: number): string {
  switch (n) {
    case 1:
    case 2:
      return "small";
    case 3:
      return "three";
    default:
      return "other";
  }
}

export function guarded(text: string): number {
  try {
    return JSON.parse(text);
  } catch (e) {
    return 0;
  } finally {
    text = "";
  }
}
