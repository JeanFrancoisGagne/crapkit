export function apply(cb: (x: number) => number, v: number): number {
  return cb(v);
}

export function afterApply(): number {
  return 2;
}
