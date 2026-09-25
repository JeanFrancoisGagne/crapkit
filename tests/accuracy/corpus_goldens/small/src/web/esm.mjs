// An ES module file.
export function parseFlags(argv) {
  const flags = {};
  for (const arg of argv) {
    if (!arg.startsWith("--")) {
      continue;
    }
    const [key, value] = arg.slice(2).split("=");
    flags[key] = value === undefined ? true : value;
  }
  return flags;
}

export async function retry(fn, times) {
  let last;
  for (let i = 0; i < times; i += 1) {
    try {
      return await fn();
    } catch (error) {
      last = error;
    }
  }
  throw last;
}
