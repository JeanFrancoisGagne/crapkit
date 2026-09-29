// jest for the probes, copied to the probe root before a run (jest on Windows reads
// --config only for a project at the cwd). Provider and output come from the environment.
module.exports = {
  rootDir: ".",
  testMatch: ["<rootDir>/harness/probe.jest.test.cjs"],
  transform: { "\.[jt]s$": ["babel-jest", { configFile: "./harness/babel.config.cjs" }] },
  collectCoverage: true,
  coverageProvider: process.env.PROBE_PROVIDER,
  collectCoverageFrom: ["js/shapes.js", "ts/shapes.ts"],
  coverageReporters: ["json"],
  coverageDirectory: process.env.PROBE_OUT,
};
