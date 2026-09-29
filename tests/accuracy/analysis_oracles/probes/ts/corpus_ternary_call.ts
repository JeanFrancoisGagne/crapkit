export function styles(p: boolean) {
  const k = p ? 1 : 0;
  const o = {
    a: p
      ? darken(c, k)
      : h,
  };
  return o;
}
