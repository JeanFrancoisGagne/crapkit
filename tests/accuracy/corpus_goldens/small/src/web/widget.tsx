/** @jsx h */
// TSX without React: a local h() builds the element objects.
export function h(tag: string, props: Record<string, unknown> | null, ...children: unknown[]) {
  return { tag, props: props ?? {}, children };
}

export function Badge(props: { count: number; max: number }) {
  if (props.count <= 0) {
    return null;
  }
  const shown = props.count > props.max ? `${props.max}+` : String(props.count);
  return <span className="badge">{shown}</span>;
}

export function Toolbar(props: { items: string[]; compact: boolean }) {
  return (
    <div>
      {props.items.map((item) => (props.compact ? <i>{item[0]}</i> : <b>{item}</b>))}
    </div>
  );
}
