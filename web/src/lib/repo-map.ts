import type { CSSProperties } from "react";
import { MarkerType, type Node } from "@xyflow/react";
import { layoutNested, type LayoutLeaf, type PortPlacement, type TooltipFlowEdge } from "@/lib/nested-layout";
import { ReportError, type ReviewReport, type Tier } from "@/lib/review-report";

// Mirrors `RepoMap` in app/repo_map.py (schema "map-0.1").

export interface LastCommit {
  sha: string;
  date: string;
  subject: string;
  pr: number | null;
}

export interface MapModule {
  path: string;
  language: string;
  tier: Tier;
  last_commit: LastCommit | null;
}

export interface RepoMap {
  schema_version: string;
  repo: string;
  sha: string;
  generated_at: string;
  modules: MapModule[];
  edges: { from: string; to: string; kind: string; line: number }[];
  unknowns: { path: string; line: number; expression: string; reason: string }[];
  limits: string[];
}

export function parseRepoMap(value: unknown): RepoMap {
  const map = value as Partial<RepoMap> | null;
  if (!map || typeof map !== "object" || !String(map.schema_version ?? "").startsWith("map-")) {
    throw new ReportError("repo_map.json is not a behavior-review repo map.");
  }
  if (!Array.isArray(map.modules) || !Array.isArray(map.edges)) {
    throw new ReportError("repo_map.json has no modules or edges.");
  }
  return { ...(map as RepoMap), unknowns: map.unknowns ?? [], limits: map.limits ?? [] };
}

// --- What each file is to this PR -------------------------------------------------------------

/** Strongest first; a folder takes the strongest category among its files. */
export const CATEGORY_ORDER = ["changed", "impacted", "imports", "unrelated"] as const;
export type FileCategory = (typeof CATEGORY_ORDER)[number];

const isHot = (category: FileCategory | undefined) => category === "changed" || category === "impacted";

// Test code by its conventional location or name, in the languages the adapters cover.
const TEST_PATH = /(^|\/)(tests?|__tests__|spec)\/|(^|\/)test_[^/]+$|_test\.[^/.]+$|\.(test|spec)\.[^/]+$/;
export const isTestPath = (path: string) => TEST_PATH.test(path);

/**
 * Changed (in the diff), impacted (a non-test caller of changed code in `impact.paths`, the
 * evidence map's source), imports a changed file (an import edge in the repo map), or unrelated.
 */
export function fileCategories(map: RepoMap, report: Pick<ReviewReport, "revisions" | "impact">): Map<string, FileCategory> {
  const changed = new Set(report.revisions.changed_files);
  const callers = report.impact.paths.filter((path) => !path.is_test).flatMap((path) => path.hops.slice(0, -1));
  const impacted = new Set(callers.map((hop) => hop.path));
  const importers = new Set(map.edges.filter((edge) => changed.has(edge.to)).map((edge) => edge.from));
  const categoryOf = (path: string): FileCategory =>
    changed.has(path) ? "changed" : impacted.has(path) ? "impacted" : importers.has(path) ? "imports" : "unrelated";
  return new Map(map.modules.map(({ path }) => [path, categoryOf(path)]));
}

// --- Folders ----------------------------------------------------------------------------------

const dirname = (path: string) => path.split("/").slice(0, -1).join("/");

/** The folders leading to `path`, outermost first: "a/b/c.py" → ["a", "a/b"]. */
export function ancestors(path: string): string[] {
  const parts = dirname(path).split("/").filter(Boolean);
  return parts.map((_, index) => parts.slice(0, index + 1).join("/"));
}

export const allFolders = (map: RepoMap) => new Set(map.modules.flatMap((module) => ancestors(module.path)));

/** The default view: only the folders on the way to a changed or impacted file are open. */
export const defaultExpanded = (categories: Map<string, FileCategory>) =>
  new Set([...categories].filter(([, category]) => isHot(category)).flatMap(([path]) => ancestors(path)));

export interface FolderSummary {
  path: string;
  fileCount: number;
  counts: Record<FileCategory, number>;
  strongest: FileCategory;
  /** The most recent `last_commit` among the folder's files. */
  latest: LastCommit | null;
}

const newer = (a: LastCommit | null, b: LastCommit | null) => (!a || (b && b.date > a.date) ? b : a);

function summarize(path: string, modules: MapModule[], categories: Map<string, FileCategory>): FolderSummary {
  const inside = modules.filter((module) => module.path.startsWith(`${path}/`));
  const counts: Record<FileCategory, number> = { changed: 0, impacted: 0, imports: 0, unrelated: 0 };
  inside.forEach((module) => counts[categories.get(module.path) ?? "unrelated"]++);
  return {
    path,
    fileCount: inside.length,
    counts,
    strongest: CATEGORY_ORDER.find((category) => counts[category] > 0) ?? "unrelated",
    latest: inside.reduce<LastCommit | null>((latest, module) => newer(latest, module.last_commit), null),
  };
}

// --- The visible graph ------------------------------------------------------------------------

export interface RepoMapView {
  expanded: ReadonlySet<string>;
  /** Hide files (and so folders) that are unrelated to the PR. */
  focusOnly: boolean;
}

export type EdgeWeight = "strong" | "medium" | "weak";

/** Between changed/impacted files: strong; touching a file that imports a changed one: medium. */
function weightOf(from: string, to: string, categories: Map<string, FileCategory>): EdgeWeight {
  const [a, b] = [categories.get(from), categories.get(to)];
  if (isHot(a) && isHot(b)) return "strong";
  return a === "imports" || b === "imports" ? "medium" : "weak";
}

const WEIGHT_RANK: Record<EdgeWeight, number> = { strong: 0, medium: 1, weak: 2 };
export const EDGE_STYLE: Record<EdgeWeight, CSSProperties> = {
  strong: { stroke: "var(--color-info)", strokeWidth: 3 },
  medium: { stroke: "var(--color-warning)", strokeWidth: 2 },
  weak: { stroke: "var(--color-muted-foreground)", strokeWidth: 1, opacity: 0.35 },
};

// Box sizes, in React Flow's canvas units.
export const MODULE_WIDTH = 280;
export const MODULE_HEIGHT = 96;
export const COLLAPSED_HEIGHT = 96;

export const moduleId = (path: string) => `module:${path}`;
export const collapsedId = (folder: string) => `collapsed:${folder}`;

/** The box a file is drawn in: itself, or its outermost collapsed folder. */
const unitOf = (path: string, expanded: ReadonlySet<string>) => {
  const folder = ancestors(path).find((candidate) => !expanded.has(candidate));
  return folder === undefined ? moduleId(path) : collapsedId(folder);
};

interface MergedEdge {
  id: string;
  source: string;
  target: string;
  imports: RepoMap["edges"];
  weight: EdgeWeight;
}

/** One edge per pair of boxes; imports inside one collapsed folder disappear with it. */
function mergeEdges(map: RepoMap, units: Map<string, string>, categories: Map<string, FileCategory>): MergedEdge[] {
  const merged = new Map<string, MergedEdge>();
  for (const edge of map.edges) {
    const [source, target] = [units.get(edge.from), units.get(edge.to)];
    if (!source || !target || source === target) continue;
    const id = `import:${source}->${target}`;
    const entry = merged.get(id) ?? { id, source, target, imports: [], weight: "weak" };
    const weight = weightOf(edge.from, edge.to, categories);
    merged.set(id, { ...entry, imports: [...entry.imports, edge], weight: WEIGHT_RANK[weight] < WEIGHT_RANK[entry.weight] ? weight : entry.weight });
  }
  return [...merged.values()];
}

function flowEdge(edge: MergedEdge, reducedMotion: boolean): TooltipFlowEdge {
  const count = edge.imports.length;
  const [first] = edge.imports;
  const tooltip = count === 1 ? `${first.from} imports ${first.to} · line ${first.line}` : `${count} imports between these boxes`;
  const style = EDGE_STYLE[edge.weight];
  return {
    id: edge.id,
    source: edge.source,
    target: edge.target,
    type: "call",
    animated: edge.weight === "strong" && !reducedMotion,
    style,
    markerEnd: { type: MarkerType.ArrowClosed, color: String(style.stroke) },
    ariaLabel: tooltip,
    data: { tooltip, badge: count > 1 ? `${count} imports` : undefined },
  };
}

export type ModuleFlowNode = Node<
  { module: MapModule; category: FileCategory; isTest: boolean; ports: PortPlacement[] }, "module"
>;
export type CollapsedFlowNode = Node<{ label: string; summary: FolderSummary; ports: PortPlacement[] }, "collapsed">;
export type RepoFolderFlowNode = Node<{ label: string; summary: FolderSummary }, "repoFolder">;
export type RepoMapNode = ModuleFlowNode | CollapsedFlowNode | RepoFolderFlowNode;

interface VisibleUnits {
  modules: MapModule[];
  collapsed: string[];
  units: Map<string, string>;
}

function visibleUnits(map: RepoMap, categories: Map<string, FileCategory>, view: RepoMapView): VisibleUnits {
  const shown = view.focusOnly ? map.modules.filter((m) => categories.get(m.path) !== "unrelated") : map.modules;
  const units = new Map(shown.map((module) => [module.path, unitOf(module.path, view.expanded)]));
  return {
    modules: shown.filter((module) => units.get(module.path) === moduleId(module.path)),
    collapsed: [...new Set([...units.values()].filter((unit) => unit.startsWith("collapsed:")))].map((unit) => unit.slice(10)),
    units,
  };
}

function layoutLeaves(visible: VisibleUnits): LayoutLeaf[] {
  return [
    ...visible.modules.map((m) => ({ id: moduleId(m.path), width: MODULE_WIDTH, height: MODULE_HEIGHT, folder: dirname(m.path) })),
    ...visible.collapsed.map((f) => ({ id: collapsedId(f), width: MODULE_WIDTH, height: COLLAPSED_HEIGHT, folder: dirname(f) })),
  ];
}

const inParent = (parentId: string | undefined) => ({ parentId, extent: parentId ? ("parent" as const) : undefined });

/** Folders as nested blocks (collapsible), files inside them, merged import edges between boxes. */
export async function buildRepoMap(
  map: RepoMap,
  categories: Map<string, FileCategory>,
  view: RepoMapView,
  reducedMotion: boolean,
): Promise<{ nodes: RepoMapNode[]; edges: TooltipFlowEdge[] }> {
  const visible = visibleUnits(map, categories, view);
  const shown = map.modules.filter((m) => visible.units.has(m.path));
  const edges = mergeEdges(map, visible.units, categories).map((edge) => flowEdge(edge, reducedMotion));
  const layout = await layoutNested(layoutLeaves(visible), edges, { layerSplit: 3, layerSpacing: 160 });
  const folders: RepoFolderFlowNode[] = layout.groups.map((group) => ({
    id: group.id, type: "repoFolder", position: { x: group.x, y: group.y }, ...inParent(group.parentId),
    width: group.width, height: group.height, selectable: false, focusable: false,
    data: { label: group.label, summary: summarize(group.path, shown, categories) },
  }));
  const modules: ModuleFlowNode[] = visible.modules.map((module) => {
    const spot = layout.leaves.get(moduleId(module.path))!;
    const data = { module, category: categories.get(module.path) ?? "unrelated", isTest: isTestPath(module.path), ports: spot.ports };
    return { id: spot.id, type: "module", position: { x: spot.x, y: spot.y }, ...inParent(spot.parentId), data };
  });
  const collapsed: CollapsedFlowNode[] = visible.collapsed.map((folder) => {
    const spot = layout.leaves.get(collapsedId(folder))!;
    const data = { label: folder.split("/").pop() ?? folder, summary: summarize(folder, shown, categories), ports: spot.ports };
    return { id: spot.id, type: "collapsed", position: { x: spot.x, y: spot.y }, ...inParent(spot.parentId), data };
  });
  return { nodes: [...folders, ...modules, ...collapsed], edges: edges.map((e) => ({ ...e, ...layout.handles.get(e.id) })) };
}
