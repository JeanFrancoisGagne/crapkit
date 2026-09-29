export function outer(xs: boolean[]) {
  return xs.map((x) =>
    x ? 1 : 2);
}
