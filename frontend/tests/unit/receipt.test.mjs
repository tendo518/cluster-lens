import assert from "node:assert/strict";
import test from "node:test";
import {
  cpSync,
  mkdtempSync,
  readFileSync,
  writeFileSync,
  existsSync,
  rmSync,
  symlinkSync,
} from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import {
  snapshotInputs,
  snapshotDist,
  fixedInputs,
  inputTrees,
} from "../../scripts/receipt.mjs";
const root = fileURLToPath(new URL("../../", import.meta.url));
test("successful build receipt matches exact current input and asset hashes", () => {
  const receipt = JSON.parse(
    readFileSync(join(root, "build-receipt.json"), "utf8"),
  );
  assert.deepEqual(receipt, {
    schema: 1,
    algorithm: "sha256",
    inputs: snapshotInputs(root),
    dist: snapshotDist(root),
  });
  assert.ok(Object.keys(receipt.inputs).includes("scripts/build.mjs"));
  assert.ok(
    !Object.keys(receipt.inputs).some(
      (path) => path.startsWith("node_modules/") || path.startsWith("tests/"),
    ),
  );
});
test("failed typecheck or bundle invalidates older receipt, never publishing partial success", () => {
  const scratch = mkdtempSync(join(tmpdir(), "monitor-receipt-"));
  try {
    for (const name of [...fixedInputs, ...inputTrees])
      cpSync(join(root, name), join(scratch, name), { recursive: true });
    symlinkSync(
      join(root, "node_modules"),
      join(scratch, "node_modules"),
      "dir",
    );
    const build = () =>
      spawnSync(process.execPath, [join(scratch, "scripts/build.mjs")], {
        cwd: scratch,
        encoding: "utf8",
      });
    const receipt = join(scratch, "build-receipt.json"),
      app = join(scratch, "src/app.ts");
    writeFileSync(receipt, '{"old":"receipt"}');
    writeFileSync(app, 'const invalid: number = "string";');
    const typeFailure = build();
    assert.notEqual(typeFailure.status, 0);
    assert.match(typeFailure.stdout, /not assignable/);
    assert.equal(existsSync(receipt), false);
    cpSync(join(root, "src/app.ts"), app);
    // Type-correct config with a failing final bundle, after app.js has built.
    writeFileSync(
      join(scratch, "vite.appearance.config.ts"),
      'throw new Error("fixture build failure"); export default {};',
    );
    writeFileSync(receipt, '{"old":"receipt"}');
    const bundleFailure = build();
    assert.notEqual(bundleFailure.status, 0);
    assert.match(bundleFailure.stderr, /fixture build failure/);
    assert.equal(existsSync(join(scratch, "dist/app.js")), true);
    assert.equal(existsSync(receipt), false);
    assert.equal(existsSync(`${receipt}.tmp`), false);
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }
});
