// Records recorded/ts.json; see crapkit.toml.
import { expect, test } from "vitest";
import { band, clamp, label } from "../src/ts/grade";

test("band", () => {
  expect(band(95)).toBe("A");
  expect(band(10)).toBe("C");
});

test("clamp", () => {
  expect(clamp(5, 0, 3)).toBe(3);
});

test("label", () => {
  expect(label({ name: "x" })).toBe("x");
});
