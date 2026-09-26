import { useCallback, useEffect, useState, type CSSProperties, type DependencyList } from "react";
import {
  BaseEdge, EdgeLabelRenderer, getBezierPath, Handle, Position, useEdgesState, useNodesState, useReactFlow,
  type Edge, type EdgeProps, type Node, type NodeProps,
} from "@xyflow/react";
import { Folder } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { FolderFlowNode, PortPlacement, TooltipFlowEdge } from "@/lib/nested-layout";

// Building blocks shared by the evidence map and the repo map.

/** SVG dash pattern that marks an unknown (unresolved) edge; legends use the same value. */
export const UNKNOWN_EDGE_DASH = "6 4";
export const MINIMAP_FROM_NODES = 8;

export const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** One handle per ELK port, so each edge enters or leaves at its own point instead of one trunk. */
export function PortHandles({ ports }: { ports: PortPlacement[] }) {
  return ports.map((port) => (
    <Handle
      key={port.id}
      id={port.id}
      type={port.side === "left" ? "target" : "source"}
      position={port.side === "left" ? Position.Left : Position.Right}
      className="opacity-0"
      style={{ top: port.offset }}
    />
  ));
}

export function FolderGroup({ data }: NodeProps<FolderFlowNode>) {
  return (
    <div className="size-full rounded-lg bg-muted/60" aria-label={`Folder ${data.path}`}>
      <span className="flex items-center gap-1 px-3 pt-2 text-xs font-medium text-muted-foreground">
        <Folder aria-hidden="true" className="size-3.5" />
        {data.label}
      </span>
    </div>
  );
}

/** Bezier edge whose text (e.g. the call site) shows in a tooltip on hover, or when the edge is focused and selected. */
export function TooltipEdge({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, markerEnd, selected, data, style }: EdgeProps<TooltipFlowEdge>) {
  const [path, labelX, labelY] = getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition });
  const [hovered, setHovered] = useState(false);
  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={data?.unknown ? { ...style, strokeDasharray: UNKNOWN_EDGE_DASH } : style} />
      <path d={path} fill="none" stroke="transparent" strokeWidth={16} onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)} />
      <EdgeLabelRenderer>
        {data?.badge ? (
          <Badge
            variant="secondary"
            className="pointer-events-none absolute -translate-1/2"
            style={{ left: labelX, top: labelY, opacity: style?.opacity }}
          >
            {data.badge}
          </Badge>
        ) : null}
        <Tooltip open={hovered || selected}>
          <TooltipTrigger asChild>
            <span aria-hidden="true" className="pointer-events-none absolute size-px" style={{ transform: `translate(${labelX}px, ${labelY}px)` }} />
          </TooltipTrigger>
          <TooltipContent>{data?.tooltip}</TooltipContent>
        </Tooltip>
      </EdgeLabelRenderer>
    </>
  );
}

/** A legend sample of an edge; `lineStyle` takes the same style object as the edge it explains. */
export function LineSwatch({ label, dash, lineStyle }: { label: string; dash?: string; lineStyle?: CSSProperties }) {
  return (
    <li className="flex items-center gap-2 text-xs text-muted-foreground">
      <svg aria-hidden="true" className="h-2 w-8" viewBox="0 0 32 8">
        <line x1="0" y1="4" x2="32" y2="4" stroke="currentColor" strokeWidth="2" strokeDasharray={dash} style={lineStyle} />
      </svg>
      {label}
    </li>
  );
}

/** React Flow's own Controls and MiniMap follow our `.dark` class toggle, not just the OS setting. */
export function useFlowColorMode(): "dark" | "light" {
  const read = (): "dark" | "light" => (document.documentElement.classList.contains("dark") ? "dark" : "light");
  const [mode, setMode] = useState(read);
  useEffect(() => {
    const observer = new MutationObserver(() => setMode(read()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return mode;
}

/**
 * Runs an async (ELK) layout into React Flow state, fits the view, and can re-run it ("Reset layout").
 * `focus` picks the node ids to fit first (e.g. a PR's files in a large repo); empty means fit everything.
 */
export function useAsyncLayout<N extends Node, E extends Edge>(
  build: () => Promise<{ nodes: N[]; edges: E[] }>,
  deps: DependencyList,
  focus?: (nodes: N[], edges: E[]) => string[],
) {
  const [run, setRun] = useState(0);
  const [error, setError] = useState<string>();
  const [nodes, setNodes, onNodesChange] = useNodesState<N>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<E>([]);
  const { fitView } = useReactFlow();

  useEffect(() => {
    let current = true;
    build()
      .then((layout) => {
        if (!current) return;
        setNodes(layout.nodes);
        setEdges(layout.edges);
        const ids = focus?.(layout.nodes, layout.edges) ?? [];
        requestAnimationFrame(() =>
          fitView({
            padding: 0.15, maxZoom: 1, duration: reducedMotion() ? 0 : 200,
            nodes: ids.length ? ids.map((id) => ({ id })) : undefined,
          }),
        );
      })
      .catch((err: unknown) => current && setError(err instanceof Error ? err.message : String(err)));
    return () => {
      current = false;
    };
    // `build` is recreated each render; `deps` lists what it actually reads.
  }, [...deps, run, setNodes, setEdges, fitView]);

  const reset = useCallback(() => setRun((value) => value + 1), []);
  return { nodes, edges, onNodesChange, onEdgesChange, error, reset };
}
