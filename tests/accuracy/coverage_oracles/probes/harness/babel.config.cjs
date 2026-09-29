// babel-jest's transform for the probes: modern syntax kept for node, types stripped.
module.exports = {
  presets: [["@babel/preset-env", { targets: { node: "current" } }], "@babel/preset-typescript"],
};
