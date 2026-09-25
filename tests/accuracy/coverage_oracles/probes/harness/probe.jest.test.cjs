// One jest test over the probes jest can load (CommonJS js, and ts through babel).
const js = require("../js/shapes.js");
const ts = require("../ts/shapes.ts");

test("probe scenario", async () => {
  if (process.env.PROBE_SCENARIO === "call") {
    const { call } = await import("./drive.mjs");
    await call(js);
    await call(ts);
  }
});
