// A test file inside the scope: the universe drops __tests__ directories, and
// the scope's {files} template has a test to point at. The recording suite is tests/web.
import { expect, test } from "vitest";
import { up } from "../arrows.js";

test("up", () => {
  expect(up(20)).toBe(2);
});
