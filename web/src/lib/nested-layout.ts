import ELK from "elkjs/lib/elk.bundled.js";
import type { ElkExtendedEdge, ElkNode } from "elkjs/lib/elk-api";
import type { Edge, Node } from "@xyflow/react";

// Folder → (file →) leaf layout with ELK, shared by the evidence map (leaves are functions inside
// file boxes) and the repo map (leaves are the files themselves). Dagre is not used because it
// mis-lays sub-flows whose nodes connect outside their group, which is the cross-folder story here.

/**
 * An edge whose text (call site, import line) is shown in a tooltip; `unknown` draws it dashed,
 * and `badge` stays visible on it (e.g. "7 imports" merged into a collapsed folder).
 */
export type TooltipFlowEdge = Edge<{ tooltip: string; unknown?: boolean; badge?: string }, "call">;
export type FolderFlowNode = Node<{ label: string; path: string }, "folder">;

export interface LayoutLeaf {
  id: string;
  width: number;
  height: number;
  /** Put the leaf inside this file's box… */
  file?: string;
  /** …or directly inside this folder (e.g. a group spanning several files). */
  folder?: string;
}

export interface LayoutEdge {
  id: string;
  source: string;
  target: string;
}

export interface PortPlacement {
  id: string;
  side: "left" | "right";
  /** Offset from the leaf's top edge, in canvas units. */
  offset: number;
}

export interface PlacedGroup {
  id: string;
  kind: "folder" | "file";
  path: string;
  label: string;
  parentId?: string;
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface PlacedLeaf {
  id: string;
  parentId?: string;
  x: number;
  y: number;
  ports: PortPlacement[];
}

export interface NestedLayout {
  /** Parents always come before their children, as React Flow sub-flows require. */
  groups: PlacedGroup[];
  leaves: Map<string, PlacedLeaf>;
  handles: Map<string, { sourceHandle: string; targetHandle: string }>;
}

interface FolderTree {
  path: string;
  label: string;
  folders: Map<string, FolderTree>;
  files: Map<string, LayoutLeaf[]>;
  leaves: LayoutLeaf[];
}

const GROUP_PADDING = "[top=40,left=16,bottom=16,right=16]";
const elk = new ELK();

const newFolder = (path: string, label: string): FolderTree => ({ path, label, folders: new Map(), files: new Map(), leaves: [] });

function folderFor(root: FolderTree, dir: string): FolderTree {
  let node = root;
  for (const part of dir.split("/").filter(Boolean)) {
    const path = node.path ? `${node.path}/${part}` : part;
    if (!node.folders.has(part)) node.folders.set(part, newFolder(path, part));
    node = node.folders.get(part)!;
  }
  return node;
}

const dirname = (path: string) => path.split("/").slice(0, -1).join("/");
const basename = (path: string) => path.split("/").pop() ?? path;

function buildTree(leaves: LayoutLeaf[]): FolderTree {
  const root = newFolder("", "");
  for (const leaf of leaves) {
    if (leaf.file) {
      const folder = folderFor(root, dirname(leaf.file));
      folder.files.set(leaf.file, [...(folder.files.get(leaf.file) ?? []), leaf]);
    } else {
      folderFor(root, leaf.folder ?? "").leaves.push(leaf);
    }
  }
  return root;
}

/** A folder holding only one sub-folder reads better as one block: "sample_project/pricing". */
function compress(folder: FolderTree): FolderTree {
  folder.folders.forEach((child, key) => folder.folders.set(key, compress(child)));
  if (folder.path && folder.folders.size === 1 && !folder.files.size && !folder.leaves.length) {
    const [child] = folder.folders.values();
    return { ...child, label: `${folder.label}/${child.label}` };
  }
  return folder;
}

export interface NestedLayoutOptions {
  /**
   * Split each folder's layers over this many columns. Layered layout puts every unconnected
   * sibling in one layer, so a folder of unrelated files would otherwise be one very tall column.
   */
  layerSplit?: number;
  /** Gap between columns; room for a badge on the edges that cross it. */
  layerSpacing?: number;
}

// ELK's layer unzipping: the strategy is read from the folder, the split from each leaf.
const unzipFolder = (options: NestedLayoutOptions): Record<string, string> =>
  options.layerSplit ? { "elk.layered.layerUnzipping.strategy": "ALTERNATING" } : {};
const unzipLeaf = (options: NestedLayoutOptions): Record<string, string> =>
  options.layerSplit ? { "elk.layered.layerUnzipping.layerSplit": String(options.layerSplit) } : {};

function leafNode(leaf: LayoutLeaf, edges: LayoutEdge[], options: NestedLayoutOptions): ElkNode {
  const ports = [
    ...edges.filter((e) => e.target === leaf.id).map((e) => ({ id: `${e.id}:in`, width: 1, height: 1, layoutOptions: { "elk.port.side": "WEST" } })),
    ...edges.filter((e) => e.source === leaf.id).map((e) => ({ id: `${e.id}:out`, width: 1, height: 1, layoutOptions: { "elk.port.side": "EAST" } })),
  ];
  return { id: leaf.id, width: leaf.width, height: leaf.height, ports, layoutOptions: { "elk.portConstraints": "FIXED_SIDE", ...unzipLeaf(options) } };
}

function folderNode(folder: FolderTree, edges: LayoutEdge[], options: NestedLayoutOptions): ElkNode[] {
  const group = { "elk.padding": GROUP_PADDING, ...unzipFolder(options) };
  return [
    ...[...folder.folders.values()].map((child) => ({
      id: `folder:${child.path}`, layoutOptions: group, children: folderNode(child, edges, options),
    })),
    ...[...folder.files].map(([file, leaves]) => ({
      id: `file:${file}`, layoutOptions: group, children: leaves.map((leaf) => leafNode(leaf, edges, options)),
    })),
    ...folder.leaves.map((leaf) => leafNode(leaf, edges, options)),
  ];
}

function collect(node: ElkNode, labels: Map<string, string>, parentId: string | undefined, out: NestedLayout) {
  for (const child of node.children ?? []) {
    const kind = child.id.startsWith("folder:") ? "folder" : child.id.startsWith("file:") ? "file" : undefined;
    const box = { x: child.x ?? 0, y: child.y ?? 0 };
    if (kind) {
      const path = child.id.slice(kind.length + 1);
      out.groups.push({ id: child.id, kind, path, label: labels.get(child.id) ?? basename(path), parentId, ...box, width: child.width ?? 0, height: child.height ?? 0 });
      collect(child, labels, child.id, out);
    } else {
      const ports = (child.ports ?? []).map((port) => ({
        id: port.id, side: port.id.endsWith(":in") ? "left" as const : "right" as const, offset: (port.y ?? 0) + (port.height ?? 0) / 2,
      }));
      out.leaves.set(child.id, { id: child.id, parentId, ...box, ports });
    }
  }
}

function folderLabels(folder: FolderTree, labels: Map<string, string>) {
  folder.folders.forEach((child) => {
    labels.set(`folder:${child.path}`, child.label);
    folderLabels(child, labels);
  });
  return labels;
}

export async function layoutNested(leaves: LayoutLeaf[], edges: LayoutEdge[], options: NestedLayoutOptions = {}): Promise<NestedLayout> {
  const tree = compress(buildTree(leaves));
  const graph: ElkNode = {
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.hierarchyHandling": "INCLUDE_CHILDREN",
      "elk.layered.spacing.nodeNodeBetweenLayers": String(options.layerSpacing ?? 96),
      "elk.spacing.nodeNode": "32",
      ...unzipFolder(options),
    },
    children: folderNode(tree, edges, options),
    edges: edges.map((e): ElkExtendedEdge => ({ id: e.id, sources: [`${e.id}:out`], targets: [`${e.id}:in`] })),
  };
  const result = await elk.layout(graph);
  const layout: NestedLayout = { groups: [], leaves: new Map(), handles: new Map() };
  collect(result, folderLabels(tree, new Map()), undefined, layout);
  edges.forEach((e) => layout.handles.set(e.id, { sourceHandle: `${e.id}:out`, targetHandle: `${e.id}:in` }));
  return layout;
}
