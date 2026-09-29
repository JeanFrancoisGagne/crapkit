// The small corpus's recording config: tools/accuracy/regenerate.py runs vitest
// with it, with node_modules linked to tools/accuracy/node/nightly.
import vue from "@vitejs/plugin-vue";

export default {
  plugins: [vue()],
  // Vite 8 transforms with Oxc; the classic runtime calls the corpus's own h().
  oxc: { jsx: { runtime: "classic", pragma: "h" } },
  test: {
    include: ["tests/web/**/*.test.*"],
    coverage: {
      provider: "istanbul",
      reporter: ["json"],
      include: ["src/web/**"],
    },
  },
};
