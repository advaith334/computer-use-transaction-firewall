// Bundle the extension into dist/ (load that folder as an unpacked extension).
import { build } from "esbuild";
import { cpSync, mkdirSync } from "node:fs";

mkdirSync("dist", { recursive: true });
await build({
  entryPoints: ["src/content.ts", "src/main_world.ts", "src/sw.ts"],
  bundle: true,
  format: "iife",
  target: "chrome120",
  outdir: "dist",
  logLevel: "info",
});
cpSync("static", "dist", { recursive: true });
// One source of truth for the DOM snapshot builder, shared with the Python harness sensor.
cpSync("../observ/assets/snapshot.js", "dist/snapshot.js");
