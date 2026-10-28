import { createHash } from "node:crypto";
import { lstatSync, readdirSync, readFileSync } from "node:fs";
import { join, extname } from "node:path";

// Receipt v1 algorithm (also the offline packager's verification contract):
// - SHA-256 over each file's raw bytes, encoded as lowercase hexadecimal.
// - `inputs` keys are POSIX paths relative to frontend/: these five fixed files
//   plus EVERY regular file recursively under src/, public/ and scripts/.
// - `dist` keys are POSIX paths relative to frontend/dist/: EVERY regular file.
// - Sort both maps lexicographically by relative path. Reject symlinks rather
//   than hashing a target outside the source/build tree. No timestamps/absolute
//   paths, aggregate hash, environment values, or dependencies enter the receipt.
// - Verification must compare the exact key sets AND all digests in both maps.
//   package-lock.json locks dependencies; node_modules/ is deliberately excluded.
export const fixedInputs = [
  "package.json",
  "package-lock.json",
  "tsconfig.json",
  "vite.config.ts",
  "vite.appearance.config.ts",
];
export const inputTrees = ["src", "public", "scripts"];
const allowedExtensions = new Set([
  ".html",
  ".js",
  ".css",
  ".svg",
  ".png",
  ".webp",
  ".woff",
  ".woff2",
]);
export const requiredAssets = [
  "index.html",
  "app.js",
  "appearance.js",
  "styles.css",
  "palettes.css",
];
function files(root, relative = "") {
  if (!lstatSync(join(root, relative)).isDirectory())
    throw new Error(`Expected real build directory: ${relative || root}`);
  const output = [];
  for (const name of readdirSync(join(root, relative)).sort()) {
    const path = relative ? `${relative}/${name}` : name;
    const stat = lstatSync(join(root, path));
    if (stat.isDirectory()) output.push(...files(root, path));
    else if (stat.isFile()) output.push(path);
    else
      throw new Error(`Build tree contains a symlink or special file: ${path}`);
  }
  return output;
}
function digestMap(root, paths) {
  return Object.fromEntries(
    paths.sort().map((path) => {
      if (!lstatSync(join(root, path)).isFile())
        throw new Error(`Expected regular build input: ${path}`);
      return [
        path,
        createHash("sha256")
          .update(readFileSync(join(root, path)))
          .digest("hex"),
      ];
    }),
  );
}
export function snapshotInputs(root) {
  return digestMap(root, [
    ...fixedInputs,
    ...inputTrees.flatMap((tree) => files(root, tree)),
  ]);
}
export function snapshotDist(root) {
  const directory = join(root, "dist"),
    paths = files(directory);
  for (const path of paths) {
    if (
      path.split("/").some((part) => part.startsWith(".")) ||
      !allowedExtensions.has(extname(path))
    )
      throw new Error(`Unsafe generated asset: ${path}`);
  }
  for (const name of requiredAssets)
    if (!paths.includes(name))
      throw new Error(`Missing generated asset: ${name}`);
  const html = readFileSync(join(directory, "index.html"), "utf8");
  for (const key of ["title", "theme", "logo"])
    if (!html.includes(`{{ui.${key}}}`))
      throw new Error(`Lost branding placeholder: ${key}`);
  const blocking = '<script src="/appearance.js"></script>';
  if (
    !html.includes(blocking) ||
    html.indexOf(blocking) > html.indexOf('href="/styles.css"')
  )
    throw new Error(
      "Appearance must be blocking classic JavaScript before CSS",
    );
  return digestMap(directory, paths);
}
