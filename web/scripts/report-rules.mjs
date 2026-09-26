// Rules for the reports the viewer ships. Every shipped report is published evidence, so it must
// be a real CI run that anyone can trace back to its public log.

const SHA = /^[0-9a-f]{40}$/;
const ACTION_RUN = /^https:\/\/github\.com\/[^/]+\/[^/]+\/actions\/runs\/\d+$/;
// The lookbehind keeps the "s:/" inside "https://" from reading as a drive letter.
const LOCAL_PATH = /(?<![A-Za-z])[A-Za-z]:[\\/]|\/(?:Users|home)\//;

/** Problems that make any report unfit to publish; an empty list means it passes. */
export function reportProblems(report) {
  const problems = [];
  if (report?.fixture !== false) problems.push("it is not real engine output (fixture must be false)");
  const revisions = report?.revisions ?? {};
  if (!SHA.test(revisions.base_sha ?? "") || !SHA.test(revisions.head_sha ?? "")) {
    problems.push("it does not name its real base and head commits");
  }
  if (!ACTION_RUN.test(report?.links?.action_run ?? "")) {
    problems.push("it is not a CI artifact stamped with links.action_run");
  }
  const comparisons = Array.isArray(report?.comparisons) ? report.comparisons : [];
  if (comparisons.some((c) => !c?.probe?.id || !c.base || !c.head)) {
    problems.push("a comparison lacks its probe or its base and head observations");
  }
  if (LOCAL_PATH.test(JSON.stringify(report))) problems.push("it contains a local absolute path");
  return problems;
}

/** The single-report demo must also tell the story: a real caller outside the diff, from a --run. */
export function demoProblems(report) {
  const problems = reportProblems(report);
  const paths = report?.impact?.paths ?? [];
  if (!paths.some((path) => Array.isArray(path?.hops) && path.outside_diff === true && path.is_test === false)) {
    problems.push("it shows no non-test caller outside the diff");
  }
  if (!report?.comparisons?.length) problems.push("it has no probe comparisons (not from a --run)");
  return problems;
}
