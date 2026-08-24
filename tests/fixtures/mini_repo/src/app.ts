export function dispatch(kind: string): number {
  switch (kind) {
    case "a": return 1;
    case "b": return 2;
    case "c": return 3;
    case "d": return 4;
    case "e": return 5;
    case "f": return 6;
    default: return 0;
  }
}

export function plain(x: number): number {
  const inner = (y: number) => (y > 0 ? y : -y);
  if (x > 10) {
    return inner(x);
  }
  return x;
}
