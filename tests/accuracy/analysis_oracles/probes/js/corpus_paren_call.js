export function parenCall(n) {
  const y = (g(n));
  if (y) {
    return 1;
  }
  return 0;
}
