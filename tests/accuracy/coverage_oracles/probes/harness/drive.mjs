// The call scenario: every call ground_truth.tsv's arms_taken column is worked from.
export async function call(m) {
  m.ifElse(true);
  m.ternary(1);
  m.logical(0, 1);
  m.nullish("x");
  m.defaultParam(5);
  m.switchCase("a");
  m.loop([]);
  m.withCallback([1]);
  m.optionalChain({ name: "n" });
  m.oneLine(1);
  m.arrow(2);
  m.ignoredIstanbul(1);
  m.ignoredV8(1);
  await m.asyncIf(true);
  new m.Box().method(5);
  if (m.badge) {
    m.badge(true);
  }
}
