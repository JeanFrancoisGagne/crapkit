// nyc and c8 run this under plain node: it requires the CommonJS probe and drives it.
const shapes = require("../js/shapes.js");

async function main() {
  if (process.argv[2] === "call") {
    const { call } = await import("./drive.mjs");
    await call(shapes);
  }
}

main();
