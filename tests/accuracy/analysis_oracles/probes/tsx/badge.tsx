export function Badge({ count }: { count: number }) {
  if (count > 99) {
    return <span>99+</span>;
  }
  return <span>{count > 0 ? count : "none"}</span>;
}
