// c8 runs this under plain node: it imports the ES module probe and drives it.
import * as shapes from "../mjs/shapes.mjs";
import { call } from "./drive.mjs";

if (process.argv[2] === "call") {
  await call(shapes);
}
