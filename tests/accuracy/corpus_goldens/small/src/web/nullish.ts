// Nullish coalescing and its assignment form.
export function withDefaults(options: { port?: number; host?: string | null }): string {
  const port = options.port ?? 8080;
  options.host ??= "localhost";
  return `${options.host}:${port}`;
}

export function firstSet(a?: string, b?: string, c?: string): string {
  return a ?? b ?? c ?? "";
}
