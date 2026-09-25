import { expect, test } from "vitest";
import { banner, listing, stripTicks } from "../../src/web/templates";

test("templates", () => {
  expect(banner({ name: "a", admin: true }, 2)).toBe("[admin] a: 2 items");
  expect(stripTicks("a`b")).toBe("ab");
  expect(listing(["abcd", "x"])).toBe("<li>abcd</li><li>x!</li>");
});
