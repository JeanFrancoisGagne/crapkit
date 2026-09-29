import { expect, test } from "vitest";
import { firstSet, withDefaults } from "../../src/web/nullish";

test("nullish", () => {
  expect(withDefaults({ port: 1 })).toBe("localhost:1");
  expect(firstSet(undefined, "b")).toBe("b");
});
