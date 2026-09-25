// vitest for the probes: provider, reporters, scenario and output directory come from the environment.
import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  // The tsx and jsx probes build their elements with the h() they define.
  oxc: { jsx: { runtime: "classic", pragma: "h" } },
  test: {
    include: ["harness/probe.test.mjs"],
    coverage: {
      enabled: true,
      provider: process.env.PROBE_PROVIDER,
      reporter: (process.env.PROBE_REPORTERS || "json").split(","),
      reportsDirectory: process.env.PROBE_OUT,
      include: ["js/*.js", "mjs/*.mjs", "ts/*.ts", "tsx/*.tsx", "jsx/*.jsx", "vue/*.vue"],
    },
  },
});
