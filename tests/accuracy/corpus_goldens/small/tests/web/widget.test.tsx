import { expect, test } from "vitest";
import { Badge } from "../../src/web/widget";

test("widget", () => {
  expect(Badge({ count: 5, max: 3 })).toMatchObject({ tag: "span", children: ["3+"] });
});
