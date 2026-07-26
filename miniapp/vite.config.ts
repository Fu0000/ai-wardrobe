import { defineConfig } from "vite";
import uniModule from "@dcloudio/vite-plugin-uni";

// DCloud currently publishes this package as CommonJS. Node ESM exposes the
// factory through a second `default` layer, while the type declaration models
// the direct default export.
const uni =
  (uniModule as unknown as { default?: typeof uniModule }).default ?? uniModule;

export default defineConfig({
  plugins: [uni()],
});
