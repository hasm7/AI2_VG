import React, { useEffect, useMemo, useRef, useState } from "react";
import ReactDOM from "react-dom/client";
import cytoscape, { type Core, type EventObject } from "cytoscape";
import "./styles.css";

type GraphNode = {
  id: string;
  label: string;
  type: string;
  summary?: string;
  properties: Record<string, string | number>;
};

type GraphRelationship = {
  id: string;
  source: string;
  target: string;
  label: string;
  sourceType: DataSource;
  properties: Record<string, string>;
};

type DataSource = "Mail" | "Slack" | "Teams" | "Issues" | "Docs" | "PRs" | "References" | "Knowledge";

type ReferenceEdge = {
  source_label: string;
  source_display_name: string;
  relationship: string;
  matched_text: string;
  source_property: string;
  target_display_name: string;
};

type ReferenceState = {
  edges: ReferenceEdge[];
  total: number;
  counts: Record<string, number>;
  last_import_at: string | null;
  last_extraction_at: string | null;
  needs_rerun: boolean;
  error?: string;
};

type GraphResponse = {
  nodes: GraphNode[];
  relationships: GraphRelationship[];
  error?: string;
};

type KnowledgeTopic = {
  slug: string;
  name: string;
  topic_type: string;
  summary: string;
  event_count: number;
};

type KnowledgeEvent = {
  topic_slug: string;
  slug: string;
  name: string;
  event_type: string;
  occurred_at: string;
  summary: string;
  evidence: string[];
  actors: string[];
};

type KnowledgeCausalLink = {
  topic_slug: string;
  cause_slug: string;
  cause_name: string;
  effect_slug: string;
  effect_name: string;
  explanation: string;
  evidence: string[];
};

type KnowledgeTokenUsage = {
  input_tokens: number;
  output_tokens: number;
};

type KnowledgeState = {
  topics: KnowledgeTopic[];
  events: KnowledgeEvent[];
  causal_links: KnowledgeCausalLink[];
  last_extraction_at: string | null;
  last_layer_build_at: string | null;
  needs_layer_rerun: boolean;
  calls?: number;
  model?: string;
  discarded_evidence?: number;
  overflow_events?: number;
  overflow_links?: number;
  token_usage?: KnowledgeTokenUsage | null;
  error?: string;
};

type Neo4jStatus = "checking" | "connected" | "disconnected";

type Selection = {
  title: string;
  rows: Array<[string, string]>;
} | null;

type ViewerStartResponse = {
  url?: string;
  error?: string;
};

const dataSources: Array<DataSource | "All"> = ["All", "Mail", "Slack", "Teams", "Issues", "Docs", "PRs", "References", "Knowledge"];
const graphApiUrl = "/api/graph";
const nodeTypeOrder = [
  "Person",
  "MailMessage",
  "SlackMessage",
  "TeamsMeeting",
  "TeamsTranscriptSegment",
  "Issue",
  "IssueVersion",
  "IssueComment",
  "Document",
  "DocumentVersion",
  "PullRequest",
  "PullRequestReview",
  "CodeChange",
  "Topic",
  "Event",
];

const nodeColors: Record<string, string> = {
  Person: "#f59e0b",
  MailMessage: "#22c55e",
  SlackMessage: "#a855f7",
  TeamsMeeting: "#06b6d4",
  TeamsTranscriptSegment: "#38bdf8",
  Issue: "#ef4444",
  IssueVersion: "#fb7185",
  IssueComment: "#f97316",
  Document: "#14b8a6",
  DocumentVersion: "#2dd4bf",
  PullRequest: "#6366f1",
  PullRequestReview: "#818cf8",
  CodeChange: "#8b5cf6",
};

const fallbackNodeColors = [
  "#64748b",
  "#0f766e",
  "#7c3aed",
  "#be123c",
  "#0369a1",
  "#65a30d",
  "#c2410c",
  "#4338ca",
  "#0d9488",
  "#b45309",
];

const nodeColorFamilies: Array<{ test: (type: string) => boolean; colors: string[] }> = [
  {
    test: (type) => type.startsWith("Issue"),
    colors: ["#ef4444", "#fb7185", "#f97316", "#dc2626", "#f43f5e"],
  },
  {
    test: (type) => type.startsWith("Document"),
    colors: ["#14b8a6", "#2dd4bf", "#0d9488", "#5eead4"],
  },
  {
    test: (type) => type.startsWith("PullRequest") || type === "CodeChange",
    colors: ["#6366f1", "#818cf8", "#8b5cf6", "#4f46e5", "#7c3aed"],
  },
  {
    test: (type) => type.startsWith("Teams"),
    colors: ["#06b6d4", "#38bdf8", "#0891b2", "#7dd3fc"],
  },
  {
    test: (type) => type.includes("Mail"),
    colors: ["#22c55e", "#86efac", "#16a34a", "#4ade80"],
  },
  {
    test: (type) => type.includes("Slack"),
    colors: ["#a855f7", "#c084fc", "#9333ea", "#d8b4fe"],
  },
];

function stableColorIndex(value: string, paletteSize: number) {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 31 + value.charCodeAt(index)) >>> 0;
  }
  return hash % paletteSize;
}

function nodeColor(type: string) {
  if (nodeColors[type]) {
    return nodeColors[type];
  }

  const family = nodeColorFamilies.find((entry) => entry.test(type));
  if (family) {
    return family.colors[stableColorIndex(type, family.colors.length)];
  }

  return fallbackNodeColors[stableColorIndex(type, fallbackNodeColors.length)];
}

async function readGraphResponse(response: Response): Promise<GraphResponse> {
  const body = await response.text();

  if (!body.trim()) {
    return {
      nodes: [],
      relationships: [],
      error: `Backend returned an empty response (${response.status} ${response.statusText || "Unknown status"}).`,
    };
  }

  try {
    return JSON.parse(body) as GraphResponse;
  } catch {
    return {
      nodes: [],
      relationships: [],
      error: `Backend returned non-JSON response (${response.status} ${response.statusText || "Unknown status"}).`,
    };
  }
}

const nodes: GraphNode[] = [
  {
    id: "issue-auth-17",
    label: "AUTH-17",
    type: "Issue",
    summary: "Increase admin session timeout",
    properties: {
      acceptance_criteria: "Admin sessions should remain active for 60 minutes of inactivity.",
      assignee_name: "Erik",
      created_at: "2026-09-16T10:45:00+02:00",
      creator_name: "Anna",
      description: "Admin users need a longer session timeout during review work.",
      issue_key: "AUTH-17",
      priority: "High",
      source_instance: "jira-main",
      status: "In Progress",
      title: "Increase admin session timeout",
    },
  },
  {
    id: "person-anna",
    label: "Anna",
    type: "Person",
    summary: "anna@example.com",
    properties: { email: "anna@example.com", name: "Anna", person_key: "email:anna@example.com", source_id: "user-anna" },
  },
  {
    id: "person-customer",
    label: "Customer Contact",
    type: "Person",
    summary: "customer@example.com",
    properties: { email: "customer@example.com", name: "Customer Contact", person_key: "email:customer@example.com" },
  },
  {
    id: "person-erik",
    label: "Erik",
    type: "Person",
    summary: "source:user-erik",
    properties: { name: "Erik", person_key: "source:user-erik", source_id: "user-erik" },
  },
  {
    id: "person-product-owner",
    label: "Product Owner",
    type: "Person",
    summary: "product@example.com",
    properties: { email: "product@example.com", name: "Product Owner", person_key: "email:product@example.com" },
  },
  {
    id: "pr-42",
    label: "PR #42",
    type: "PullRequest",
    summary: "acme/auth-service#42",
    properties: {
      author_name: "Erik",
      created_at: "2026-09-16T12:00:00+02:00",
      description: "Changes the admin inactivity timeout from 15 to 60 minutes.",
      display_name: "acme/auth-service#42",
      pr_number: 42,
      repository: "acme/auth-service",
      source_instance: "github-main",
      state: "open",
      title: "Increase admin session timeout",
    },
  },
  {
    id: "doc-001",
    label: "doc-001",
    type: "Document",
    summary: "Admin session timeout requirement",
    properties: {
      author_name: "Anna",
      body: "Admin users should have a 60 minute inactivity timeout to support longer review tasks.",
      change_summary: "Initial requirement after customer mail and team meeting.",
      document_type: "requirement",
      source_instance: "docs-main",
      title: "Admin session timeout requirement",
    },
  },
  {
    id: "mail-001",
    label: "mail-001",
    type: "MailMessage",
    summary: "Session timeout for admin users",
    properties: {
      body: "Admin users report that the session timeout is too short during review work.",
      sender_address: "customer@example.com",
      sender_name: "Customer Contact",
      sent_at: "2026-09-16T09:00:00+02:00",
      source_instance: "gmail-main",
      subject: "Session timeout for admin users",
    },
  },
  {
    id: "meeting-001",
    label: "meeting-001",
    type: "TeamsMeeting",
    summary: "Auth timeout decision meeting",
    properties: {
      ended_at: "2026-09-16T10:30:00+02:00",
      source_instance: "teams-main",
      started_at: "2026-09-16T10:00:00+02:00",
      title: "Auth timeout decision meeting",
    },
  },
  {
    id: "segment-001",
    label: "segment-001",
    type: "TeamsTranscriptSegment",
    summary: "Transcript segment 1",
    properties: {
      body: "We should increase admin session timeout to sixty minutes because review work takes longer than normal user flows.",
      end_offset_ms: 12000,
      sequence_number: 1,
      speaker_name: "Anna",
      start_offset_ms: 0,
    },
  },
  {
    id: "slack-001",
    label: "slack-001",
    type: "SlackMessage",
    summary: "auth-team",
    properties: {
      author_name: "Anna",
      body: "Customer mail says admin sessions expire too quickly. I think we need to adjust the timeout requirement.",
      channel_name: "auth-team",
      sent_at: "2026-09-16T09:20:00+02:00",
      source_instance: "slack-main",
    },
  },
];

const relationships: GraphRelationship[] = [
  { id: "rel-authored-document", source: "person-anna", target: "doc-001", label: "AUTHORED_DOCUMENT", sourceType: "Docs", properties: {} },
  { id: "rel-authored-pr", source: "person-erik", target: "pr-42", label: "AUTHORED_PR", sourceType: "PRs", properties: {} },
  { id: "rel-commented-issue", source: "person-erik", target: "issue-auth-17", label: "COMMENTED_ON_ISSUE", sourceType: "Issues", properties: {} },
  { id: "rel-has-transcript", source: "meeting-001", target: "segment-001", label: "HAS_TEAMS_TRANSCRIPT_SEGMENT", sourceType: "Teams", properties: {} },
  { id: "rel-mail-recipient", source: "mail-001", target: "person-product-owner", label: "MAIL_RECIPIENT", sourceType: "Mail", properties: { recipient_type: "to" } },
  { id: "rel-owns-issue", source: "person-erik", target: "issue-auth-17", label: "OWNS_ISSUE", sourceType: "Issues", properties: {} },
  { id: "rel-participated-meeting", source: "person-anna", target: "meeting-001", label: "PARTICIPATED_IN_MEETING", sourceType: "Teams", properties: {} },
  { id: "rel-reviewed-pr", source: "person-anna", target: "pr-42", label: "REVIEWED_PR", sourceType: "PRs", properties: {} },
  { id: "rel-sent-mail", source: "person-customer", target: "mail-001", label: "SENT_MAIL", sourceType: "Mail", properties: {} },
  { id: "rel-sent-slack", source: "person-anna", target: "slack-001", label: "SENT_SLACK_MESSAGE", sourceType: "Slack", properties: {} },
  { id: "rel-spoke-segment", source: "person-anna", target: "segment-001", label: "SPOKE_TEAMS_TRANSCRIPT_SEGMENT", sourceType: "Teams", properties: {} },
];

function propertyRows(properties: Record<string, string | number>) {
  return Object.entries(properties).map(([key, value]) => [key, String(value)] as [string, string]);
}

function GraphView() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const graphRef = useRef<Core | null>(null);
  const viewerWindowRef = useRef<Window | null>(null);
  const viewerCloseTimerRef = useRef<number | null>(null);
  const isMotionPausedRef = useRef(false);
  const motionLevelRef = useRef(1);
  const [selection, setSelection] = useState<Selection>(null);
  const [isExpanded, setIsExpanded] = useState(false);
  const [isMotionPaused, setIsMotionPaused] = useState(false);
  const [isMotionMenuOpen, setIsMotionMenuOpen] = useState(false);
  const [isLegendVisible, setIsLegendVisible] = useState(true);
  const [motionLevel, setMotionLevel] = useState(1);
  const [spacingLevel, setSpacingLevel] = useState(1);
  const [areLabelsVisible, setAreLabelsVisible] = useState(true);
  const [isNeighborMode, setIsNeighborMode] = useState(false);
  const [activeSource, setActiveSource] = useState<DataSource | "All">("All");
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [graphNodes, setGraphNodes] = useState<GraphNode[]>(nodes);
  const [graphRelationships, setGraphRelationships] = useState<GraphRelationship[]>(relationships);
  const [graphError, setGraphError] = useState("");
  const [isGraphLoading, setIsGraphLoading] = useState(false);
  const [isViewerStarting, setIsViewerStarting] = useState(false);
  const [neo4jStatus, setNeo4jStatus] = useState<Neo4jStatus>("checking");

  const stopViewer = async () => {
    await fetch("/api/viewer/stop", { method: "POST" }).catch(() => undefined);
  };

  const clearViewerCloseTimer = () => {
    if (viewerCloseTimerRef.current !== null) {
      window.clearInterval(viewerCloseTimerRef.current);
      viewerCloseTimerRef.current = null;
    }
  };

  const watchViewerWindow = (viewerWindow: Window) => {
    clearViewerCloseTimer();
    viewerWindowRef.current = viewerWindow;

    viewerCloseTimerRef.current = window.setInterval(() => {
      if (!viewerWindow.closed) {
        return;
      }

      clearViewerCloseTimer();
      viewerWindowRef.current = null;
      void stopViewer();
    }, 700);
  };

  const openSqlViewer = async () => {
    if (isViewerStarting) {
      return;
    }

    setGraphError("");
    setIsViewerStarting(true);

    try {
      const response = await fetch("/api/viewer/start", { method: "POST" });
      const data = (await response.json()) as ViewerStartResponse;

      if (!response.ok || data.error || !data.url) {
        throw new Error(data.error || "Could not start SQL viewer.");
      }

      const viewerWindow = window.open(data.url, "_blank");
      if (!viewerWindow) {
        throw new Error("The browser blocked the SQL viewer popup.");
      }

      watchViewerWindow(viewerWindow);
    } catch (error) {
      setGraphError(error instanceof Error ? error.message : "Could not start SQL viewer.");
    } finally {
      setIsViewerStarting(false);
    }
  };

  // In the Knowledge filter, Topic is pinned to the left and Event to the
  // right. Everything else (the source nodes cited as evidence) is seeded
  // into a tight cluster at the center instead of being locked, so cose's
  // own repulsion/attraction still applies within that clump rather than
  // scattering it across the full canvas between the two pinned columns.
  const knowledgeAnchors = useMemo(() => {
    if (activeSource !== "Knowledge") {
      return null;
    }

    const left: GraphNode[] = [];
    const right: GraphNode[] = [];
    const middle: GraphNode[] = [];
    graphNodes.forEach((node) => {
      if (node.type === "Topic") {
        left.push(node);
      } else if (node.type === "Event") {
        right.push(node);
      } else {
        middle.push(node);
      }
    });

    const spacingY = 90;
    const anchors: Record<string, { x: number; y: number; locked: boolean }> = {};

    const placeColumn = (items: GraphNode[], x: number) => {
      const offset = ((items.length - 1) * spacingY) / 2;
      items.forEach((node, index) => {
        anchors[node.id] = { x, y: index * spacingY - offset, locked: true };
      });
    };

    placeColumn(left, -420);
    placeColumn(right, 420);

    middle.forEach((node) => {
      let hash = 0;
      for (let index = 0; index < node.id.length; index += 1) {
        hash = (hash * 31 + node.id.charCodeAt(index)) >>> 0;
      }
      const angle = (hash % 360) * (Math.PI / 180);
      const radius = 20 + (hash % 140);
      anchors[node.id] = { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius, locked: false };
    });

    return anchors;
  }, [activeSource, graphNodes]);

  const elements = useMemo(
    () => [
      ...graphNodes.map((node) => {
        const anchor = knowledgeAnchors?.[node.id];
        return {
          data: {
            id: node.id,
            label: node.label,
            nodeType: node.type,
            properties: node.properties,
            summary: node.summary ?? "",
          },
          ...(anchor ? { position: { x: anchor.x, y: anchor.y }, locked: anchor.locked } : {}),
        };
      }),
      ...graphRelationships.map((relationship) => ({
        data: {
          id: relationship.id,
          source: relationship.source,
          target: relationship.target,
          label: relationship.label,
          sourceType: relationship.sourceType,
          properties: relationship.properties,
        },
      })),
    ],
    [graphNodes, graphRelationships, knowledgeAnchors],
  );

  const legendItems = useMemo(() => {
    const types = Array.from(new Set(graphNodes.map((node) => node.type)));
    return types.sort((left, right) => {
      const leftIndex = nodeTypeOrder.indexOf(left);
      const rightIndex = nodeTypeOrder.indexOf(right);

      if (leftIndex === -1 && rightIndex === -1) {
        return left.localeCompare(right);
      }

      if (leftIndex === -1) {
        return 1;
      }

      if (rightIndex === -1) {
        return -1;
      }

      return leftIndex - rightIndex;
    });
  }, [graphNodes]);

  const layoutSettings = useMemo(
    () => ({
      nodeRepulsion: 5200 + spacingLevel * 3800,
      idealEdgeLength: 62 + spacingLevel * 34,
    }),
    [spacingLevel],
  );

  useEffect(() => {
    if (!containerRef.current) {
      return;
    }

    const graph = cytoscape({
      container: containerRef.current,
      elements,
      minZoom: 0.25,
      maxZoom: 2.5,
      wheelSensitivity: 0.55,
      style: [
        {
          selector: "node",
          style: {
            "background-color": "#64748b",
            "border-color": "#ffffff",
            "border-width": 2,
            color: "#111827",
            content: "data(label)",
            "font-size": 11,
            "font-weight": 700,
            height: 46,
            label: "data(label)",
            "text-background-color": "#ffffff",
            "text-background-opacity": 0.9,
            "text-background-padding": "3px",
            "text-margin-y": -8,
            "text-valign": "top",
            width: 46,
          },
        },
        {
          selector: 'node[nodeType = "Person"]',
          style: {
            "background-color": "#f59e0b",
          },
        },
        {
          selector: 'node[nodeType = "MailMessage"]',
          style: {
            "background-color": "#22c55e",
          },
        },
        {
          selector: 'node[nodeType = "SlackMessage"]',
          style: {
            "background-color": "#a855f7",
          },
        },
        {
          selector: 'node[nodeType = "TeamsMeeting"]',
          style: {
            "background-color": "#06b6d4",
          },
        },
        {
          selector: 'node[nodeType = "TeamsTranscriptSegment"]',
          style: {
            "background-color": "#38bdf8",
          },
        },
        {
          selector: 'node[nodeType = "Issue"]',
          style: {
            "background-color": "#ef4444",
          },
        },
        {
          selector: 'node[nodeType = "IssueVersion"]',
          style: {
            "background-color": "#fb7185",
          },
        },
        {
          selector: 'node[nodeType = "IssueComment"]',
          style: {
            "background-color": "#f97316",
          },
        },
        {
          selector: 'node[nodeType = "Document"]',
          style: {
            "background-color": "#14b8a6",
          },
        },
        {
          selector: 'node[nodeType = "DocumentVersion"]',
          style: {
            "background-color": "#2dd4bf",
          },
        },
        {
          selector: 'node[nodeType = "PullRequest"]',
          style: {
            "background-color": "#6366f1",
          },
        },
        {
          selector: 'node[nodeType = "PullRequestReview"]',
          style: {
            "background-color": "#818cf8",
          },
        },
        {
          selector: 'node[nodeType = "CodeChange"]',
          style: {
            "background-color": "#8b5cf6",
          },
        },
        {
          selector: "node",
          style: {
            "background-color": (element) => nodeColor(String(element.data("nodeType") ?? "")),
          },
        },
        {
          selector: "edge",
          style: {
            "curve-style": "bezier",
            "font-size": 8,
            color: "#2563eb",
            "line-color": "#2563eb",
            "target-arrow-color": "#2563eb",
            "target-arrow-shape": "triangle",
            label: "data(label)",
            "text-background-color": "#ffffff",
            "text-background-opacity": 1,
            "text-background-padding": "4px",
            "text-rotation": "autorotate",
            width: 2,
          },
        },
        {
          selector: "node:selected",
          style: {
            "border-color": "#1d4ed8",
            "border-width": 4,
          },
        },
        {
          selector: "edge:selected",
          style: {
            "line-color": "#1d4ed8",
            "target-arrow-color": "#1d4ed8",
            width: 4,
          },
        },
        {
          selector: ".hidden",
          style: {
            display: "none",
          },
        },
        {
          selector: ".labels-hidden",
          style: {
            label: "",
          },
        },
      ],
      layout: knowledgeAnchors
        ? {
            name: "cose",
            animate: false,
            fit: true,
            padding: 42,
            randomize: false,
            nodeRepulsion: 2600,
            idealEdgeLength: 34,
            componentSpacing: 20,
          }
        : {
            name: "cose",
            animate: false,
            fit: true,
            padding: 42,
            nodeRepulsion: layoutSettings.nodeRepulsion,
            idealEdgeLength: layoutSettings.idealEdgeLength,
          },
    });

    graph.on("tap", "node", (event: EventObject) => {
      const data = event.target.data();
      setSelectedNodeId(data.id);
      setSelection({
        title: data.label,
        rows: [
          ["label", data.nodeType],
          ["id", data.id],
          ["summary", data.summary],
          ...propertyRows(data.properties),
        ],
      });
    });

    graph.on("tap", "edge", (event: EventObject) => {
      const data = event.target.data();
      setSelectedNodeId(null);
      setSelection({
        title: data.label,
        rows: [
          ["type", data.label],
          ["source", data.source],
          ["target", data.target],
          ...propertyRows(data.properties),
        ],
      });
    });

    graph.on("tap", (event: EventObject) => {
      if (event.target === graph) {
        setSelectedNodeId(null);
        setSelection(null);
      }
    });

    graphRef.current = graph;

    let animationFrame = 0;
    let basePositions: Record<string, { x: number; y: number }> = {};

    const startFloating = () => {
      basePositions = {};
      graph.nodes().forEach((node) => {
        basePositions[node.id()] = { ...node.position() };
      });

      const startedAt = performance.now();

      const floatGraph = (now: number) => {
        const elapsed = (now - startedAt) / 1000;

        if (!isMotionPausedRef.current) {
          graph.batch(() => {
            graph.nodes().forEach((node, index) => {
              if (node.grabbed()) {
                basePositions[node.id()] = { ...node.position() };
                return;
              }

              const base = basePositions[node.id()];
              if (!base) {
                return;
              }

              const phase = index * 0.73;
              const motion = motionLevelRef.current;
              node.position({
                x: base.x + Math.sin(elapsed * (0.14 + motion * 0.14) + phase) * (8 + motion * 15),
                y: base.y + Math.cos(elapsed * (0.11 + motion * 0.11) + phase) * (7 + motion * 12),
              });
            });
          });
        }

        animationFrame = window.requestAnimationFrame(floatGraph);
      };

      animationFrame = window.requestAnimationFrame(floatGraph);
    };

    graph.ready(() => {
      window.setTimeout(startFloating, 120);
    });

    graph.on("free", "node", (event: EventObject) => {
      basePositions[event.target.id()] = { ...event.target.position() };
    });

    return () => {
      window.cancelAnimationFrame(animationFrame);
      graph.destroy();
      graphRef.current = null;
    };
  }, [elements, layoutSettings, knowledgeAnchors]);

  useEffect(() => {
    const resizeTimer = window.setTimeout(() => {
      graphRef.current?.resize();
      graphRef.current?.fit(undefined, 42);
    }, 120);

    return () => window.clearTimeout(resizeTimer);
  }, [isExpanded]);

  useEffect(() => {
    isMotionPausedRef.current = isMotionPaused;
  }, [isMotionPaused]);

  useEffect(() => {
    motionLevelRef.current = motionLevel;
  }, [motionLevel]);

  useEffect(() => {
    const graph = graphRef.current;
    if (!graph) {
      return;
    }

    graph.elements().toggleClass("labels-hidden", !areLabelsVisible);
  }, [areLabelsVisible, elements]);

  useEffect(() => {
    const controller = new AbortController();

    async function loadGraph() {
      setIsGraphLoading(true);
      setGraphError("");
      setNeo4jStatus("checking");
      setIsNeighborMode(false);
      setSelectedNodeId(null);
      setSelection(null);

      try {
        const response = await fetch(`${graphApiUrl}?source=${encodeURIComponent(activeSource)}`, {
          signal: controller.signal,
        });
        const data = await readGraphResponse(response);

        if (!response.ok || data.error) {
          throw new Error(data.error || "Could not load graph data.");
        }

        setGraphNodes(data.nodes);
        setGraphRelationships(data.relationships);
        setNeo4jStatus("connected");
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }

        setNeo4jStatus("disconnected");
        setGraphError(error instanceof Error ? error.message : "Could not load graph data.");
      } finally {
        setIsGraphLoading(false);
      }
    }

    loadGraph();

    return () => controller.abort();
  }, [activeSource]);

  useEffect(() => {
    return () => {
      clearViewerCloseTimer();
      void stopViewer();
    };
  }, []);

  const fitGraph = () => {
    graphRef.current?.fit(undefined, 42);
  };

  const centerSelected = () => {
    const graph = graphRef.current;
    if (!graph || !selectedNodeId) {
      return;
    }

    const selectedNode = graph.getElementById(selectedNodeId);
    if (selectedNode.empty()) {
      return;
    }

    graph.animate({
      center: { eles: selectedNode },
      zoom: Math.max(graph.zoom(), 1.15),
      duration: 260,
    });
  };

  const showNeighbors = () => {
    const graph = graphRef.current;
    if (!graph) {
      return;
    }

    if (isNeighborMode) {
      graph.elements().removeClass("hidden");
      graph.fit(undefined, 42);
      setIsNeighborMode(false);
      return;
    }

    if (!selectedNodeId) {
      return;
    }

    const selectedNode = graph.getElementById(selectedNodeId);
    if (selectedNode.empty()) {
      return;
    }

    const visibleElements = selectedNode.closedNeighborhood();
    graph.elements().addClass("hidden");
    visibleElements.removeClass("hidden");
    graph.fit(visibleElements, 42);
    setIsNeighborMode(true);
  };

  const filterBySource = (source: DataSource | "All") => {
    setActiveSource(source);
  };

  return (
    <section
      className={`context-box${isExpanded ? " context-box-expanded" : ""}`}
      aria-label="Neo4j graph visualization"
    >
      <div className="source-filters" aria-label="Data source filters">
        {dataSources.map((source) => (
          <button
            className={`source-filter-button${activeSource === source ? " source-filter-button-active" : ""}`}
            type="button"
            key={source}
            onClick={() => filterBySource(source)}
          >
            {source === "All" ? "Full graph" : source}
          </button>
        ))}
      </div>
      <div className="graph-actions">
        <button
          className="graph-action-button"
          type="button"
          aria-label="Fit graph to screen"
          onClick={fitGraph}
        >
          Fit
        </button>
        <button
          className="graph-action-button"
          type="button"
          disabled={!selectedNodeId}
          onClick={centerSelected}
        >
          Center
        </button>
        <button
          className="graph-action-button"
          type="button"
          disabled={!selectedNodeId && !isNeighborMode}
          onClick={showNeighbors}
        >
          {isNeighborMode ? "All" : "Neighbors"}
        </button>
        <button className="graph-action-button" type="button" onClick={() => setAreLabelsVisible((current) => !current)}>
          {areLabelsVisible ? "Hide labels" : "Show labels"}
        </button>
        <button className="graph-action-button" type="button" onClick={() => setIsLegendVisible((current) => !current)}>
          {isLegendVisible ? "Hide legend" : "Show legend"}
        </button>
        <div className="motion-control">
          <button
            className={`graph-action-button${isMotionMenuOpen ? " graph-action-button-active" : ""}`}
            type="button"
            aria-expanded={isMotionMenuOpen}
            aria-controls="motion-control-panel"
            onClick={() => setIsMotionMenuOpen((current) => !current)}
          >
            Motion
          </button>
          {isMotionMenuOpen ? (
            <div className="motion-panel" id="motion-control-panel">
              <div className="motion-panel-header">
                <span>Motion</span>
                <button className="motion-toggle-button" type="button" onClick={() => setIsMotionPaused((current) => !current)}>
                  {isMotionPaused ? "On" : "Pause"}
                </button>
              </div>
              <label className="motion-slider">
                <span>Distance</span>
                <input
                  type="range"
                  min="0"
                  max="2"
                  step="0.1"
                  value={spacingLevel}
                  onChange={(event) => setSpacingLevel(Number(event.target.value))}
                />
              </label>
              <label className="motion-slider">
                <span>Float</span>
                <input
                  type="range"
                  min="0"
                  max="2"
                  step="0.1"
                  value={motionLevel}
                  onChange={(event) => setMotionLevel(Number(event.target.value))}
                />
              </label>
            </div>
          ) : null}
        </div>
        <button
          className="graph-action-button"
          type="button"
          aria-label={isExpanded ? "Minimize graph" : "Expand graph"}
          onClick={() => setIsExpanded((current) => !current)}
        >
          {isExpanded ? "Close" : "Expand"}
        </button>
      </div>
      <button
        className="viewer-button"
        type="button"
        disabled={isViewerStarting}
        onClick={openSqlViewer}
      >
        {isViewerStarting ? "Starting SQL Viewer..." : "Open SQL Viewer"}
      </button>
      <div className={`neo4j-status neo4j-status-${neo4jStatus}`} aria-live="polite">
        <span className="neo4j-status-dot" />
        <span>
          {neo4jStatus === "connected"
            ? "Connected to Neo4j"
            : neo4jStatus === "checking"
              ? "Checking Neo4j"
              : "Neo4j disconnected"}
        </span>
      </div>
      {isLegendVisible ? (
        <div className="graph-legend" aria-label="Node labels">
          {legendItems.map((type) => {
            const color = nodeColor(type);
            return (
              <div className="graph-legend-item" key={type}>
                <span
                  className="graph-legend-marker"
                  style={{ borderColor: color, backgroundColor: color }}
                />
                <span>{type}</span>
              </div>
            );
          })}
        </div>
      ) : null}
      <div className="graph-canvas" ref={containerRef} />
      {(isGraphLoading || graphError) ? (
        <div className={`graph-status${graphError ? " graph-status-error" : ""}`}>
          {graphError || "Loading graph..."}
        </div>
      ) : null}
      {selection ? (
        <aside className="selection-panel">
          <strong>{selection.title}</strong>
          <dl>
            {selection.rows.map(([key, value]) => (
              <div key={key}>
                <dt>{key}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
        </aside>
      ) : null}
    </section>
  );
}

function formatTimestamp(value: string | null) {
  if (!value) {
    return "never";
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

const relationshipLabels: Record<string, string> = {
  MENTIONS_ISSUE: "Issue",
  MENTIONS_PULL_REQUEST: "Pull request",
  MENTIONS_DOCUMENT: "Document",
};

function ReferenceExtractionPanel() {
  const [state, setState] = useState<ReferenceState | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState("");
  const [justRan, setJustRan] = useState(false);

  const loadState = async () => {
    try {
      const response = await fetch("/api/references");
      const data = (await response.json()) as ReferenceState;
      if (!response.ok || data.error) {
        throw new Error(data.error || "Could not read the reference layer.");
      }
      setState(data);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Could not read the reference layer.");
    }
  };

  useEffect(() => {
    void loadState();
  }, []);

  const runExtraction = async () => {
    setError("");
    setIsRunning(true);
    setJustRan(false);

    try {
      const response = await fetch("/api/references/extract", { method: "POST" });
      const data = (await response.json()) as ReferenceState;
      if (!response.ok || data.error) {
        throw new Error(data.error || "Extraction failed.");
      }
      setState(data);
      setJustRan(true);
    } catch (runError) {
      setError(runError instanceof Error ? runError.message : "Extraction failed.");
    } finally {
      setIsRunning(false);
    }
  };

  const edges = state?.edges ?? [];

  return (
    <div className="reference-panel">
      <div className="reference-actions">
        <button
          className={`reference-run-button${state?.needs_rerun ? " reference-run-button-stale" : ""}`}
          type="button"
          disabled={isRunning}
          onClick={runExtraction}
        >
          {isRunning ? "Extracting..." : "Extract references"}
        </button>
        <div className="reference-status">
          <span>Last extraction: {formatTimestamp(state?.last_extraction_at ?? null)}</span>
          <span>Last import: {formatTimestamp(state?.last_import_at ?? null)}</span>
          {state?.needs_rerun ? (
            <span className="reference-stale">An import has happened since the last extraction. Run it again.</span>
          ) : null}
        </div>
      </div>

      {error ? <p className="reference-error">{error}</p> : null}
      {justRan && !error ? <p className="reference-success">Done. {state?.total ?? 0} edges created.</p> : null}

      <div className="reference-counts">
        {Object.keys(relationshipLabels).map((key) => (
          <span key={key} className="reference-count">
            {relationshipLabels[key]}: <strong>{state?.counts?.[key] ?? 0}</strong>
          </span>
        ))}
        <span className="reference-count">
          Total: <strong>{state?.total ?? 0}</strong>
        </span>
      </div>

      <div className="reference-table-wrapper">
        {edges.length === 0 ? (
          <p className="reference-empty">No references yet. Press the button to run the pass.</p>
        ) : (
          <table className="reference-table">
            <thead>
              <tr>
                <th>Source type</th>
                <th>Source</th>
                <th>Matched</th>
                <th>Field</th>
                <th>Target</th>
              </tr>
            </thead>
            <tbody>
              {edges.map((edge, index) => (
                <tr key={`${edge.source_display_name}-${edge.relationship}-${edge.target_display_name}-${index}`}>
                  <td>{edge.source_label}</td>
                  <td>{edge.source_display_name}</td>
                  <td><code>{edge.matched_text}</code></td>
                  <td>{edge.source_property}</td>
                  <td>{edge.target_display_name}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function KnowledgeLayerPanel() {
  const [state, setState] = useState<KnowledgeState | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState("");
  const [justRan, setJustRan] = useState(false);

  const loadState = async () => {
    try {
      const response = await fetch("/api/knowledge");
      const data = (await response.json()) as KnowledgeState;
      if (!response.ok || data.error) {
        throw new Error(data.error || "Could not read the knowledge layer.");
      }
      setState(data);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Could not read the knowledge layer.");
    }
  };

  useEffect(() => {
    void loadState();
  }, []);

  const runBuild = async () => {
    setError("");
    setIsRunning(true);
    setJustRan(false);

    try {
      const response = await fetch("/api/knowledge/build", { method: "POST" });
      const data = (await response.json()) as KnowledgeState;
      if (!response.ok || data.error) {
        throw new Error(data.error || "Build failed.");
      }
      setState(data);
      setJustRan(true);
    } catch (runError) {
      setError(runError instanceof Error ? runError.message : "Build failed.");
    } finally {
      setIsRunning(false);
    }
  };

  const topics = state?.topics ?? [];
  const events = state?.events ?? [];
  const causalLinks = state?.causal_links ?? [];

  const eventsByTopic = (topicSlug: string) => events.filter((event) => event.topic_slug === topicSlug);
  const linksByTopic = (topicSlug: string) => causalLinks.filter((link) => link.topic_slug === topicSlug);

  return (
    <div className="knowledge-panel">
      <div className="reference-actions">
        <button
          className={`knowledge-build-button${state?.needs_layer_rerun ? " knowledge-build-button-stale" : ""}`}
          type="button"
          disabled={isRunning}
          onClick={runBuild}
        >
          {isRunning ? "Building knowledge layer..." : "Build knowledge layer"}
        </button>
        <div className="reference-status">
          <span>Last reference extraction: {formatTimestamp(state?.last_extraction_at ?? null)}</span>
          <span>Last knowledge build: {formatTimestamp(state?.last_layer_build_at ?? null)}</span>
          {state?.needs_layer_rerun ? (
            <span className="reference-stale">References have changed since the last build. Run it again.</span>
          ) : null}
        </div>
      </div>

      {error ? <p className="reference-error">{error}</p> : null}
      {justRan && !error ? (
        <p className="reference-success">
          Done. {topics.length} topics, {events.length} events, {causalLinks.length} causal links.
        </p>
      ) : null}

      {state ? (
        <div className="reference-counts">
          <span className="reference-count">
            Model: <strong>{state.model ?? "-"}</strong>
          </span>
          <span className="reference-count">
            Calls: <strong>{state.calls ?? 0}</strong>
          </span>
          <span className="reference-count">
            Tokens:{" "}
            <strong>
              {state.token_usage ? `${state.token_usage.input_tokens} in / ${state.token_usage.output_tokens} out` : "n/a"}
            </strong>
          </span>
          <span className="reference-count">
            Discarded evidence: <strong>{state.discarded_evidence ?? 0}</strong>
          </span>
        </div>
      ) : null}

      <div className="knowledge-topics">
        {topics.length === 0 ? (
          <p className="reference-empty">No knowledge layer yet. Press the button to build it.</p>
        ) : (
          topics.map((topic) => (
            <div className="knowledge-topic" key={topic.slug}>
              <h3>
                {topic.name} <span className="knowledge-topic-type">{topic.topic_type}</span>
              </h3>
              <p>{topic.summary}</p>

              <table className="reference-table">
                <thead>
                  <tr>
                    <th>Event</th>
                    <th>Type</th>
                    <th>Occurred at</th>
                    <th>Summary</th>
                    <th>Actors</th>
                    <th>Evidence</th>
                  </tr>
                </thead>
                <tbody>
                  {eventsByTopic(topic.slug).map((event) => (
                    <tr key={event.slug}>
                      <td>{event.name}</td>
                      <td>{event.event_type}</td>
                      <td>{formatTimestamp(event.occurred_at)}</td>
                      <td>{event.summary}</td>
                      <td>{event.actors.join(", ")}</td>
                      <td>{event.evidence.join(", ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {linksByTopic(topic.slug).length > 0 ? (
                <table className="reference-table">
                  <thead>
                    <tr>
                      <th>Cause</th>
                      <th>Effect</th>
                      <th>Explanation</th>
                      <th>Evidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {linksByTopic(topic.slug).map((link) => (
                      <tr key={`${link.cause_slug}-${link.effect_slug}`}>
                        <td>{link.cause_name}</td>
                        <td>{link.effect_name}</td>
                        <td>{link.explanation}</td>
                        <td>{link.evidence.join(", ")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : null}
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function BuildGraphLayersPanel() {
  const [activeInnerTab, setActiveInnerTab] = useState<"step1" | "step2">("step1");

  return (
    <div className="build-graph-layers">
      <div className="center-tabs center-tabs-inner" role="tablist" aria-label="Build graph layers tabs">
        <button
          className={`center-tab${activeInnerTab === "step1" ? " center-tab-active" : ""}`}
          type="button"
          role="tab"
          aria-selected={activeInnerTab === "step1"}
          onClick={() => setActiveInnerTab("step1")}
        >
          Reference extraction
        </button>
        <button
          className={`center-tab${activeInnerTab === "step2" ? " center-tab-active" : ""}`}
          type="button"
          role="tab"
          aria-selected={activeInnerTab === "step2"}
          onClick={() => setActiveInnerTab("step2")}
        >
          Knowledge layer
        </button>
      </div>
      <div className="center-tab-panel" role="tabpanel">
        {activeInnerTab === "step1" ? <ReferenceExtractionPanel /> : <KnowledgeLayerPanel />}
      </div>
    </div>
  );
}

function App() {
  const [activeCenterTab, setActiveCenterTab] = useState<"message" | "notes">("message");
  const [aiMessage, setAiMessage] = useState("");
  const [aiAnswer, setAiAnswer] = useState("");
  const [isAiLoading, setIsAiLoading] = useState(false);
  const [aiError, setAiError] = useState("");

  const sendAiMessage = async () => {
    const message = aiMessage.trim();
    if (!message || isAiLoading) {
      return;
    }

    setAiError("");
    setIsAiLoading(true);

    try {
      const response = await fetch("/api/ai/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      });
      const data = (await response.json()) as { answer?: string; error?: string };

      if (!response.ok || data.error) {
        throw new Error(data.error || "AI request failed.");
      }

      setAiAnswer(data.answer || "");
    } catch (error) {
      setAiError(error instanceof Error ? error.message : "AI request failed.");
    } finally {
      setIsAiLoading(false);
    }
  };

  return (
    <main>
      <GraphView />
      <section className="center-panel" aria-label="Center workspace">
        <div className="center-tabs" role="tablist" aria-label="Center panel tabs">
          <button
            className={`center-tab${activeCenterTab === "message" ? " center-tab-active" : ""}`}
            type="button"
            role="tab"
            aria-selected={activeCenterTab === "message"}
            onClick={() => setActiveCenterTab("message")}
          >
            Communicate with AI
          </button>
          <button
            className={`center-tab${activeCenterTab === "notes" ? " center-tab-active" : ""}`}
            type="button"
            role="tab"
            aria-selected={activeCenterTab === "notes"}
            onClick={() => setActiveCenterTab("notes")}
          >
            Build graph layers
          </button>
        </div>
        <div className="center-tab-panel" role="tabpanel">
          {activeCenterTab === "message" ? (
            <div className="ai-chat">
              <label className="ai-chat-field">
                <span>Your message</span>
                <textarea
                  className="ai-chat-input"
                  aria-label="Message to AI"
                  value={aiMessage}
                  onChange={(event) => setAiMessage(event.target.value)}
                />
              </label>
              <button
                className="ai-send-button"
                type="button"
                disabled={!aiMessage.trim() || isAiLoading}
                onClick={sendAiMessage}
              >
                {isAiLoading ? "Thinking..." : "Send"}
              </button>
              <label className="ai-chat-field ai-chat-answer-field">
                <span>AI answer</span>
                <textarea
                  className="ai-chat-output"
                  aria-label="AI answer"
                  readOnly
                  value={aiError || aiAnswer}
                />
              </label>
            </div>
          ) : (
            <BuildGraphLayersPanel />
          )}
        </div>
      </section>
      <textarea className="right-text-box" aria-label="Right text box" />
    </main>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
