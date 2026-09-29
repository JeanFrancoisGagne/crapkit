export function emit(p) {
  if (p) {
    if (p.a) this.a = p.a;
    if (p.b) this.b = p.b;
    if (p.c) this.c = p.c;
  }
}
