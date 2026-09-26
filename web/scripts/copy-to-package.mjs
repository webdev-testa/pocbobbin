// Copies the built page into the Python package (app/web_dist/), where `behavior-review ui` serves it.
// It is committed: `uv tool install git+…` builds the package from source, so the page must already
// be built there, and users need no Node. CI rebuilds and fails if this copy is stale.
// The published demo data (dist/data/) is left out: local mode reads the repository's own runs.
import { cp, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { extname, join, resolve } from "node:path";

const source = resolve("dist");
const target = resolve("../app/web_dist");
const TEXT = new Set([".html", ".css", ".js", ".svg"]);

await rm(target, { recursive: true, force: true });
await cp(source, target, { recursive: true, filter: (path) => !path.startsWith(resolve(source, "data")) });

// A Windows checkout gives Vite CRLF sources (index.html is copied as-is), so normalize to LF:
// the committed copy must match a Linux CI build byte for byte.
for (const entry of await readdir(target, { recursive: true, withFileTypes: true })) {
  const path = join(entry.parentPath, entry.name);
  if (entry.isFile() && TEXT.has(extname(entry.name))) {
    // `\r+\n`: Vite can emit "\r\r\n" where a CRLF source line meets its own line break.
    await writeFile(path, (await readFile(path, "utf8")).replace(/\r+\n/g, "\n"));
  }
}
console.log(`Copied the built page to ${target}.`);
