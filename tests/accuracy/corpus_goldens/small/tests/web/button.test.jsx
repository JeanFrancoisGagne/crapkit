import { expect, test } from "vitest";
import { Button } from "../../src/web/button.jsx";

test("button", () => {
  expect(Button({ kind: "primary", disabled: false, label: "go" }).props.className).toBe("btn btn-primary");
});
