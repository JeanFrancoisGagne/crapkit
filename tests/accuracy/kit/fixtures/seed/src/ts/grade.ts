// The seed corpus's TypeScript half: a switch, an arrow and an optional chain.
export function band(score: number): string {
  switch (true) {
    case score >= 90:
      return "A";
    case score >= 75:
      return "B";
    default:
      return "C";
  }
}

export const clamp = (value: number, low: number, high: number): number =>
  value < low ? low : value > high ? high : value;

export function label(user?: { name?: string }): string {
  return user?.name ?? "anonymous";
}

export function never(flag: boolean): number {
  if (flag) {
    return 1;
  }
  return 0;
}
