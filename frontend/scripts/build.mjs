import { rmSync, writeFileSync, renameSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import { snapshotInputs, snapshotDist } from "./receipt.mjs";

const root = fileURLToPath(new URL("../", import.meta.url));
const receipt = join(root, "build-receipt.json"),
  temporary = `${receipt}.tmp`;
// No partial success: even a failed typecheck must invalidate an older receipt.
rmSync(receipt, { force: true });
rmSync(temporary, { force: true });
const before = snapshotInputs(root);
function run(script, ...args) {
  // Only installed local dependencies. Building never invokes npm/npx, installs
  // dependencies, contacts a registry, or assembles backend package resources.
  const result = spawnSync(
    process.execPath,
    [join(root, "node_modules", script), ...args],
    { cwd: root, stdio: "inherit" },
  );
  if (result.error) throw result.error;
  if (result.status !== 0)
    throw new Error(`${script} failed (${result.status ?? result.signal})`);
}
run("typescript/bin/tsc", "--noEmit");
run("vite/bin/vite.js", "build");
run("vite/bin/vite.js", "build", "--config", "vite.appearance.config.ts");
const inputs = snapshotInputs(root),
  dist = snapshotDist(root);
if (JSON.stringify(before) !== JSON.stringify(inputs))
  throw new Error(
    "Frontend inputs changed during the build; rebuild before packaging",
  );
writeFileSync(
  temporary,
  JSON.stringify({ schema: 1, algorithm: "sha256", inputs, dist }, null, 2) +
    "\n",
);
renameSync(temporary, receipt);
console.log("Verified frontend/build-receipt.json (SHA-256 inputs and assets)");
