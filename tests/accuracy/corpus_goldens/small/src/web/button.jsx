/** @jsx h */
// JSX without React: a local h() builds the element objects.
export function h(tag, props, ...children) {
  return { tag, props: props || {}, children };
}

export function Button({ kind, disabled, label }) {
  const cls = kind === "primary" ? "btn btn-primary" : "btn";
  if (disabled) {
    return <button className={cls} disabled>{label}</button>;
  }
  return <button className={cls}>{label}</button>;
}
