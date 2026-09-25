// One vitest test that imports every JS-family probe and, in the call scenario, drives it.
import { test } from "vitest";
import { call } from "./drive.mjs";
import * as js from "../js/shapes.js";
import * as mjs from "../mjs/shapes.mjs";
import * as ts from "../ts/shapes.ts";
import * as tsx from "../tsx/shapes.tsx";
import * as jsx from "../jsx/shapes.jsx";
import * as vue from "../vue/Shapes.vue";

const modules = [js, mjs, ts, tsx, jsx, vue];

test("probe scenario", async () => {
  if (process.env.PROBE_SCENARIO === "call") {
    for (const m of modules) {
      await call(m.ifElse ? m : m.default);
    }
  }
});
