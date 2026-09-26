export function narrow(v: unknown) {
  const c = v as string extends keyof T ? X : Y;
  return c;
}
