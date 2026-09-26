export function visitAll(xs) {
  for (const x of xs) if (x) visit(x);
}
