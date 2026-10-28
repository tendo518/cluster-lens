import assert from "node:assert/strict";
import {
  readFileSync,
  readdirSync,
  mkdtempSync,
  cpSync,
  symlinkSync,
  writeFileSync,
  rmSync,
} from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import test from "node:test";
const read = (name) =>
  readFileSync(new URL(`../../dist/${name}`, import.meta.url), "utf8");

test("builds ignore ambient .env/VITE credentials and are byte-for-byte deterministic", () => {
  const root = fileURLToPath(new URL("../../", import.meta.url));
  const scratch = mkdtempSync(join(tmpdir(), "monitor-build-"));
  try {
    for (const name of [
      "src",
      "public",
      "package.json",
      "vite.config.ts",
      "vite.appearance.config.ts",
    ])
      cpSync(join(root, name), join(scratch, name), { recursive: true });
    symlinkSync(
      join(root, "node_modules"),
      join(scratch, "node_modules"),
      "dir",
    );
    for (const name of [
      ".env",
      ".env.local",
      ".env.production",
      ".env.production.local",
    ])
      writeFileSync(
        join(scratch, name),
        "VITE_SECRET=never-embed-test-secret\nSECRET=also-never-embed\n",
      );
    for (const config of ["vite.config.ts", "vite.appearance.config.ts"]) {
      const result = spawnSync(
        process.execPath,
        [
          join(root, "node_modules/vite/bin/vite.js"),
          "build",
          "--config",
          join(scratch, config),
        ],
        {
          cwd: scratch,
          encoding: "utf8",
          env: {
            ...process.env,
            VITE_RUNTIME_SECRET: "never-embed-runtime-secret",
          },
        },
      );
      assert.equal(result.status, 0, result.stdout + result.stderr);
    }
    for (const name of readdirSync(join(root, "dist")))
      assert.equal(
        readFileSync(join(scratch, "dist", name), "utf8"),
        read(name),
        `deterministic, credential-free ${name}`,
      );
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }
});
test("build preserves stable public files and one-pass branding template placeholders", () => {
  assert.deepEqual(
    readdirSync(new URL("../../dist/", import.meta.url)).sort(),
    ["app.js", "appearance.js", "index.html", "palettes.css", "styles.css"],
  );
  const source = readFileSync(
      new URL("../../public/index.html", import.meta.url),
      "utf8",
    ),
    html = read("index.html");
  assert.equal(
    html,
    source,
    "Vite must not interpolate or normalize the server-owned template",
  );
  assert.ok(
    html.indexOf('<script src="/appearance.js"></script>') <
      html.indexOf('href="/styles.css"'),
  );
  assert.ok(html.includes('data-default-theme="{{ui.theme}}"'));
  assert.ok(html.includes('data-brand-mark="{{ui.mark}}"'));
  for (const key of [
    "title",
    "icon_url",
    "icon_type",
    "name",
    "logo",
    "subtitle",
    "workspace",
    "workspace_subtitle",
    "avatar",
    "heading",
    "footer",
    "palette",
  ])
    assert.ok(html.includes(`{{ui.${key}}}`), key);
  assert.ok(
    !/\b(import|export)\s/.test(read("appearance.js")),
    "blocking script must be standalone classic JavaScript",
  );
  for (const name of ["app.js", "appearance.js"]) {
    assert.ok(!read(name).includes("sourceMappingURL"));
    assert.ok(!read(name).includes("process.env"));
    assert.ok(!read(name).includes("window.state"));
    assert.ok(!read(name).includes("testHook"));
  }
});
