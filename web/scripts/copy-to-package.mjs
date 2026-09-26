// Copies the built page into the Python package (app/web_dist/), where `behavior-review ui` serves it.
// It is committed: `uv tool install git+…` builds the package from source, so the page must already
// be built there, and users need no Node. CI rebuilds and fails if this copy is stale.
// The published demo data (dist/data/) is left out: local mode reads the repository's own runs.
import { cp, rm } from "node:fs/promises";
import { resolve } from "node:path";

const source = resolve("dist");
const target = resolve("../app/web_dist");

await rm(target, { recursive: true, force: true });
await cp(source, target, { recursive: true, filter: (path) => !path.startsWith(resolve(source, "data")) });
console.log(`Copied the built page to ${target}.`);
