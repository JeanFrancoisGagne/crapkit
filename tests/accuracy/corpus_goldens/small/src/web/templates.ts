// Template literals nested inside template literals, an arrow inside a
// substitution, and a regular expression holding a backtick.
export function banner(user: { name: string; admin: boolean }, count: number): string {
  const suffix = `${count > 1 ? `${count} items` : `one item`}`;
  if (user.admin) {
    return `[admin] ${user.name}: ${suffix}`;
  }
  return `${user.name}: ${suffix}`;
}

export function stripTicks(text: string): string {
  const tick = /`+/g;
  if (!text) {
    return "";
  }
  return text.replace(tick, "");
}

export function listing(items: string[]): string {
  return `${items.map((item) => `<li>${item.length > 3 ? item : `${item}!`}</li>`).join("")}`;
}

export function afterTemplates(value: number): number {
  if (value > 10) {
    return 10;
  }
  return value;
}
