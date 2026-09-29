import { expect, test } from "vitest";
import { firstOf, label, naïveSum } from "../../src/web/generic";

test("generic", () => {
  expect(firstOf([null, 2], 0)).toBe(2);
  expect(label()).toBe("anonymous");
  expect(naïveSum([1, -1, 2])).toBe(3);
});
