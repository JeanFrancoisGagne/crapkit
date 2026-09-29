// Worked examples from G. Ann Campbell, "Cognitive Complexity", SonarSource,
// version 1.7 (29 August 2023), written in TypeScript.

export function sumOfPrimes(max: number): number {
  let total = 0;
  OUT: for (let i = 1; i <= max; ++i) {
    for (let j = 2; j < i; ++j) {
      if (i % j === 0) {
        continue OUT;
      }
    }
    total += i;
  }
  return total;
}

export function getWords(n: number): string {
  switch (n) {
    case 1:
      return "one";
    case 2:
      return "a couple";
    case 3:
      return "a few";
    default:
      return "lots";
  }
}

export function myMethod(condition1: boolean, condition2: boolean): void {
  try {
    if (condition1) {
      for (let i = 0; i < 10; i++) {
        while (condition2) {
          condition2 = false;
        }
      }
    }
  } catch (e) {
    if (condition2) {
      condition2 = false;
    }
  }
}

export function logicalMix(a: boolean, b: boolean, c: boolean, d: boolean, e: boolean, f: boolean): number {
  if (a && b && c || d || e && f) {
    return 1;
  }
  return 0;
}

export function logicalNot(a: boolean, b: boolean, c: boolean): number {
  if (a && !(b && c)) {
    return 1;
  }
  return 0;
}

export function factorial(n: number): number {
  if (n <= 1) {
    return 1;
  }
  return n * factorial(n - 1);
}

export function nullishPick(a?: number): number {
  return a ?? 0;
}

export function nullishAssign(a?: number): number {
  a ??= 1;
  return a;
}

export function optionalRead(a?: { b: number }): number | undefined {
  return a?.b;
}
