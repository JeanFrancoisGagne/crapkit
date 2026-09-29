export function templated(name: string): string {
  return `a ${`b ${name}`} c`;
}

export function afterTemplate(): number {
  return 4;
}
