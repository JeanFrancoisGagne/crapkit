// Generic function declarations, and a parameter whose type is a function.
export function firstOf<T>(items: T[], fallback: T): T {
  for (const item of items) {
    if (item !== undefined && item !== null) {
      return item;
    }
  }
  return fallback;
}

export function mapKeep<T, U>(items: T[], fn: (item: T) => U | undefined): U[] {
  const out: U[] = [];
  for (const item of items) {
    const mapped = fn(item);
    if (mapped !== undefined) {
      out.push(mapped);
    }
  }
  return out;
}

export function label(user?: { name?: string }): string {
  return user?.name ?? "anonymous";
}

export function naïveSum(values: number[]): number {
  let total = 0;
  for (const value of values) {
    if (value > 0) {
      total += value;
    }
  }
  return total;
}
