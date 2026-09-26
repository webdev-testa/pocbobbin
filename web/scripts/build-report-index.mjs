// Writes public/data/reports/index.json from the pr-<N>/ folders beside it, newest first.
// Usage: npm run reports:index (after adding or replacing a pr-<N>/report.json).
import { readdir, readFile, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { reportProblems } from "./report-rules.mjs";

const root = resolve("public/data/reports");
const folders = (await readdir(root, { withFileTypes: true }))
  .filter((entry) => entry.isDirectory() && /^pr-\d+$/.test(entry.name))
  .map((entry) => entry.name);

async function entryFor(folder) {
  const report = JSON.parse(await readFile(join(root, folder, "report.json"), "utf8"));
  const problems = reportProblems(report);
  if (problems.length) {
    console.warn(`Skipping ${folder}: ${problems.join("; ")}.`);
    return null;
  }
  const pr = Number(folder.slice(3)) || Number(report.links?.pr);
  return {
    pr,
    title: report.links?.pr_title ?? `PR #${pr}`,
    head_sha: report.revisions.head_sha,
    base_sha: report.revisions.base_sha,
    generated_at: report.generated_at,
    action_run: report.links.action_run,
    path: folder,
  };
}

const reports = (await Promise.all(folders.map(entryFor)))
  .filter(Boolean)
  .sort((a, b) => b.generated_at.localeCompare(a.generated_at));
await writeFile(join(root, "index.json"), `${JSON.stringify({ reports }, null, 2)}\n`);
console.log(`Wrote index.json: ${reports.map((r) => `PR #${r.pr}`).join(", ") || "no reports"}.`);
