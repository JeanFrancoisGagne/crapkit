import { expect, test } from "vitest";
import common from "../../src/web/common.cjs";

test("common", () => {
  expect(common.pad("ab", 4, ".")).toBe("..ab");
});
