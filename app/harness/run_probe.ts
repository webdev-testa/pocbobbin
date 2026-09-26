/**
 * Minimal JavaScript/TypeScript probe runner.
 *
 * Run with a configured runtime, for example:
 *   node --experimental-strip-types tools/run_probe.ts probes/example.json
 *   npx tsx tools/run_probe.ts probes/example.json
 *
 * Module-loading failures use `error`, so the paired runner classifies them as
 * inconclusive rather than as a behavior exception.
 */

import { readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

function targetParts(target) {
  if (typeof target === "object" && target !== null) return [target.path, target.symbol];
  const [path, ...symbolParts] = String(target).split(":");
  return [path, symbolParts.join(":")];
}

function moduleCandidates(targetPath) {
  const absolute = resolve(process.cwd(), targetPath);
  if (existsSync(absolute)) return [absolute];
  return [".ts", ".tsx", ".js", ".jsx", ".mjs"].map((extension) => `${absolute}${extension}`);
}

async function loadTarget(target) {
  const [targetPath, symbol] = targetParts(target);
  let lastError;
  for (const candidate of moduleCandidates(targetPath)) {
    try {
      const module = await import(pathToFileURL(candidate).href);
      const fn = module[symbol];
      if (typeof fn !== "function") throw new Error(`export '${symbol}' is not callable`);
      return fn;
    } catch (error) {
      lastError = error;
      if (existsSync(candidate)) throw error;
    }
  }
  throw lastError ?? new Error(`cannot resolve module '${targetPath}'`);
}

async function main() {
  const probePath = process.argv[2];
  if (!probePath) throw new Error("probe path is required");
  const probe = JSON.parse(await readFile(probePath, "utf8"));
  let fn;
  try {
    fn = await loadTarget(probe.target);
  } catch (error) {
    console.log(JSON.stringify({
      probe: probe.id,
      outcome: "error",
      error_type: error?.name ?? "Error",
      error: String(error?.message ?? error),
    }, null, 2));
    return;
  }
  try {
    const args = Array.isArray(probe.args) ? probe.args : probe.input?.args ?? [];
    const value = await fn(...args);
    console.log(JSON.stringify({ probe: probe.id, outcome: "value", value }, null, 2));
  } catch (error) {
    console.log(JSON.stringify({
      probe: probe.id,
      outcome: "exception",
      error_type: error?.name ?? "Error",
      error: String(error?.message ?? error),
    }, null, 2));
  }
}

main().catch((error) => {
  console.log(JSON.stringify({
    probe: process.argv[2] ?? "unknown",
    outcome: "error",
    error_type: error?.name ?? "Error",
    error: String(error?.message ?? error),
  }, null, 2));
});
