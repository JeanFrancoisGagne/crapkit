import { expect, test } from "vitest";
import { dispatch, handlers, up } from "../../src/web/arrows.js";

test("arrows", () => {
  expect(handlers[0]({ ctrl: true })).toBe("copy");
  expect(up(5)).toBe(1);
  expect(dispatch({ ctrl: true, shift: true })).toBe("both");
});
