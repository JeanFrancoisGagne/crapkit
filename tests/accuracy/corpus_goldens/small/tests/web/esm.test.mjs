import { expect, test } from "vitest";
import { parseFlags } from "../../src/web/esm.mjs";

test("esm", () => {
  expect(parseFlags(["--a=1", "b", "--c"])).toEqual({ a: "1", c: true });
});
