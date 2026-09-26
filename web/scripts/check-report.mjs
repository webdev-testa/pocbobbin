import { access, readFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { demoProblems, reportProblems } from "./report-rules.mjs";

const readJson = async (file) => JSON.parse(await readFile(file, "utf8"));
const exists = (file) => access(file).then(() => true, () => false);

// The single-report fallback (shown when there is no reports index) is the demo story itself.
const report = await readJson(resolve("public/data/report.json"));
const problems = demoProblems(report);
if (problems.length) throw new Error(`public/data/report.json can't be shipped: ${problems.join("; ")}.`);
const { base_sha, head_sha } = report.revisions;
console.log(
  `Report check passed: ${base_sha.slice(0, 7)}..${head_sha.slice(0, 7)}, ${report.impact.paths.length} impact paths, ` +
    `${report.comparisons.length} probe comparisons, from ${report.links.action_run}.`,
);

// Every PR in the index must be publishable evidence, and the index must match its folders.
const root = resolve("public/data/reports");
if (await exists(join(root, "index.json"))) {
  const { reports } = await readJson(join(root, "index.json"));
  for (const entry of reports) {
    const prReport = await readJson(join(root, entry.path, "report.json"));
    const prProblems = reportProblems(prReport);
    if (prProblems.length) throw new Error(`${entry.path}/report.json can't be shipped: ${prProblems.join("; ")}.`);
    if (prReport.revisions.head_sha !== entry.head_sha) {
      throw new Error(`index.json is stale for ${entry.path}; run npm run reports:index.`);
    }
  }
  console.log(`Reports index passed: ${reports.map((entry) => `PR #${entry.pr}`).join(", ")}.`);
}
