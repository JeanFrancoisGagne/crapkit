import { expect, test } from "vitest";
import { caption, tone } from "../../src/web/Counter.vue";

test("counter", () => {
  expect(tone(11)).toBe("hot");
  expect(caption(1, 99)).toBe("1");
});
