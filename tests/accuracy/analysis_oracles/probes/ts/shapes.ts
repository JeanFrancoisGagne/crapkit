export function withDefaults(a: number, b = 1, ...rest: number[]): number {
  return a + b + rest.length;
}

export function destructured({ a, b }: { a: number; b: number }, [c]: number[]): number {
  return a + b + c;
}

export function noParams(): number {
  return 0;
}

export class Counter {
  private n = 0;

  increment(by: number): number {
    if (by > 0) {
      this.n += by;
    }
    return this.n;
  }

  reset(): void {
    this.n = 0;
  }
}
