import React, { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
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

type EmbeddingLabelState = {
  label: string;
  total: number;
  embedded: number;
  missing: number;
};

type EmbeddingRunLabelReport = {
  label: string;
  embedded: number;
  skipped_unchanged: number;
  skipped_empty: number;
  failed: number;
};

type EmbeddingFailure = {
  label: string;
  key: Record<string, string | number | null>;
  error: string;
};

type EmbeddingTokenUsage = {
  total_tokens: number;
};

type EmbeddingState = {
  per_label: EmbeddingLabelState[];
  models_in_use: string[];
  configured_model: string;
  configured_dimensions: number;
  last_embedding_at: string | null;
  last_import_at: string | null;
  last_layer_build_at: string | null;
  needs_rerun: boolean;
  run_at?: string;
  forced?: boolean;
  model?: string;
  dimensions?: number;
  per_label_run?: EmbeddingRunLabelReport[];
  embedded?: number;
  skipped?: number;
  skipped_unchanged?: number;
  skipped_empty?: number;
  failed?: number;
  failures?: EmbeddingFailure[];
  token_usage?: EmbeddingTokenUsage | null;
  error?: string;
};

type GraphViewHandle = {
  selectNodeByDisplayName: (type: string, displayName: string) => void;
};

type ChatCitation = {
  label: string;
  key: string;
  display_name: string;
  source_url?: string | null;
};

type ChatTokenUsage = Record<string, { input_tokens: number; output_tokens: number }>;

type ChatToolError = { node: string; tool: string; error: string };

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations?: ChatCitation[];
  droppedCitations?: string[];
  toolErrors?: ChatToolError[];
  error?: string;
};

type ChatSseEvent =
  | { type: "status"; text: string }
  | { type: "token"; text: string }
  | { type: "sources"; citations: ChatCitation[]; dropped_citations: string[] }
  | { type: "tool_error"; node: string; tool: string; args: Record<string, unknown>; error: string }
  | {
      type: "done";
      answer: string;
      citations?: ChatCitation[];
      dropped_citations?: string[];
      token_usage?: ChatTokenUsage;
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

const GraphView = forwardRef<GraphViewHandle>(function GraphView(_props, ref) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const graphRef = useRef<Core | null>(null);
  const viewerWindowRef = useRef<Window | null>(null);
  const viewerCloseTimerRef = useRef<number | null>(null);
  const isMotionPausedRef = useRef(false);
  const motionLevelRef = useRef(1);
  const areLabelsVisibleRef = useRef(true);
  const pendingSelectionRef = useRef<{ type: string; displayName: string } | null>(null);
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

  // In the Knowledge filter every position is fixed, so no force layout runs.
  // Topic sits in a column on the left, Event in a column on the right, and
  // the source nodes cited as evidence fill a centered grid between them.
  // The columns are pushed outward by the grid's own width, so the two
  // derived types stay clear of the evidence block at any node count.
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

    const gridSpacingX = 190;
    const gridSpacingY = 70;
    const columnSpacingY = 90;
    const columnSpacingX = 190;
    const maxPerColumn = 12;
    const gutter = 280;

    const anchors: Record<string, { x: number; y: number; locked: boolean }> = {};

    const gridColumns = Math.max(1, Math.ceil(Math.sqrt(middle.length)));
    const gridWidth = (gridColumns - 1) * gridSpacingX;
    const gridRows = Math.max(1, Math.ceil(middle.length / gridColumns));
    const gridOffsetY = ((gridRows - 1) * gridSpacingY) / 2;

    middle.forEach((node, index) => {
      const column = index % gridColumns;
      const row = Math.floor(index / gridColumns);
      anchors[node.id] = {
        x: column * gridSpacingX - gridWidth / 2,
        y: row * gridSpacingY - gridOffsetY,
        locked: false,
      };
    });

    // Long columns wrap into further columns, stepping away from the center.
    const placeColumn = (items: GraphNode[], baseX: number, direction: number) => {
      const perColumn = Math.min(items.length, maxPerColumn) || 1;
      const offsetY = ((perColumn - 1) * columnSpacingY) / 2;
      items.forEach((node, index) => {
        const column = Math.floor(index / perColumn);
        anchors[node.id] = {
          x: baseX + direction * column * columnSpacingX,
          y: (index % perColumn) * columnSpacingY - offsetY,
          locked: true,
        };
      });
    };

    const columnX = gridWidth / 2 + gutter;
    placeColumn(left, -columnX, -1);
    placeColumn(right, columnX, 1);

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
      nodeRepulsion: 2800 + spacingLevel * 8200,
      idealEdgeLength: 44 + spacingLevel * 78,
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
            name: "preset",
            animate: false,
            fit: true,
            padding: 42,
          }
        : {
            name: "cose",
            animate: false,
            fit: true,
            padding: 56,
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
    graph.elements().toggleClass("labels-hidden", !areLabelsVisibleRef.current);

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
      window.setTimeout(() => {
        graph.fit(undefined, 56);
        startFloating();
      }, 120);
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
      const graph = graphRef.current;
      graph?.resize();
      graph?.fit(undefined, 56);
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
    areLabelsVisibleRef.current = areLabelsVisible;
  }, [areLabelsVisible]);

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

  const trySelectPending = () => {
    const pending = pendingSelectionRef.current;
    if (!pending) {
      return;
    }
    const match = graphNodes.find((node) => node.type === pending.type && node.label === pending.displayName);
    if (!match) {
      return;
    }
    pendingSelectionRef.current = null;
    setSelectedNodeId(match.id);
    setSelection({
      title: match.label,
      rows: [
        ["label", match.type],
        ["id", match.id],
        ["summary", match.summary ?? ""],
        ...propertyRows(match.properties),
      ],
    });
    const node = graphRef.current?.getElementById(match.id);
    if (node && !node.empty()) {
      graphRef.current!.animate({
        center: { eles: node },
        zoom: Math.max(graphRef.current!.zoom(), 1.15),
        duration: 260,
      });
    }
  };

  // Runs after both the graph API response and the cytoscape re-init effect
  // (declared earlier, so it commits first) have settled for this render.
  useEffect(() => {
    trySelectPending();
  }, [elements, graphNodes]);

  useImperativeHandle(ref, () => ({
    selectNodeByDisplayName(type: string, displayName: string) {
      pendingSelectionRef.current = { type, displayName };
      if (activeSource === "All") {
        trySelectPending();
      } else {
        setActiveSource("All");
      }
    },
  }));

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
        <button className="graph-action-button" type="button" onClick={() => setIsLegendVisible((current) => !current)}>
          {isLegendVisible ? "Hide legend" : "Show legend"}
        </button>
        <button className="graph-action-button" type="button" onClick={() => setAreLabelsVisible((current) => !current)}>
          {areLabelsVisible ? "Hide labels" : "Show labels"}
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
                  max="3"
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
      <div className="graph-footer">
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
});

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
        <span className="reference-counts-label">Relationships created</span>
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
                <th>Source type (Label)</th>
                <th>Source (node name)</th>
                <th>Field (property in source)</th>
                <th>Matched (text in source)</th>
                <th>Target (node name)</th>
                <th>Relationship (type)</th>
              </tr>
            </thead>
            <tbody>
              {edges.map((edge, index) => (
                <tr key={`${edge.source_display_name}-${edge.relationship}-${edge.target_display_name}-${index}`}>
                  <td>{edge.source_label}</td>
                  <td>{edge.source_display_name}</td>
                  <td>{edge.source_property}</td>
                  <td><code>{edge.matched_text}</code></td>
                  <td>{edge.target_display_name}</td>
                  <td>{edge.relationship}</td>
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
              <div className="knowledge-topic-card">
                <h4 className="knowledge-card-title">Topic node created</h4>
                <p className="knowledge-table-caption">
                  (Type is one of: requirement, defect, incident, decision, other)
                </p>
                <p><strong>Topic name:</strong> {topic.name}</p>
                <p>
                  <strong>Type:</strong> <span className="knowledge-topic-type">{topic.topic_type}</span>
                </p>
                <p><strong>Topic summary:</strong> {topic.summary}</p>
              </div>

              <h4 className="knowledge-card-title knowledge-section-title">Event nodes created for this topic</h4>
              <div className="knowledge-table-scroll">
              <table className="reference-table">
                <thead>
                  <tr>
                    <th>Event (what happened)</th>
                    <th>Type (what kind of event)</th>
                    <th>Occurred at</th>
                    <th>Summary</th>
                    <th>Actors (linked persons)</th>
                    <th>Evidence (source nodes)</th>
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
              </div>

              {linksByTopic(topic.slug).length > 0 ? (
                <>
                <h4 className="knowledge-card-title knowledge-section-title">
                  Relationships created between these events (CAUSED)
                </h4>
                <p className="knowledge-table-caption">(cause:Event)-[:CAUSED]-&gt;(effect:Event)</p>
                <div className="knowledge-table-scroll">
                <table className="reference-table">
                  <thead>
                    <tr>
                      <th>Cause</th>
                      <th>Effect</th>
                      <th>Explanation (why cause led to effect)</th>
                      <th>Evidence (source identifiers)</th>
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
                </div>
                </>
              ) : null}
            </div>
          ))
        )}
      </div>
    </div>
  );
}

const EMBEDDING_MODEL_FALLBACK = "text-embedding-3-large";

function EmbeddingPanel() {
  const [state, setState] = useState<EmbeddingState | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState("");
  const [justRan, setJustRan] = useState(false);
  const [confirmingReembed, setConfirmingReembed] = useState(false);

  const loadState = async () => {
    try {
      const response = await fetch("/api/embeddings");
      const data = (await response.json()) as EmbeddingState;
      if (!response.ok || data.error) {
        throw new Error(data.error || "Could not read embedding coverage.");
      }
      setState(data);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Could not read embedding coverage.");
    }
  };

  useEffect(() => {
    void loadState();
  }, []);

  const runBuild = async (force: boolean) => {
    setError("");
    setIsRunning(true);
    setJustRan(false);
    setConfirmingReembed(false);

    try {
      const response = await fetch("/api/embeddings/build", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ force }),
      });
      const data = (await response.json()) as EmbeddingState;
      if (!response.ok || data.error) {
        throw new Error(data.error || "Embedding build failed.");
      }
      setState(data);
      setJustRan(true);
    } catch (runError) {
      setError(runError instanceof Error ? runError.message : "Embedding build failed.");
    } finally {
      setIsRunning(false);
    }
  };

  const perLabel = state?.per_label ?? [];
  const failures = state?.failures ?? [];
  const mixedModels = (state?.models_in_use?.length ?? 0) > 1
    || (state?.models_in_use?.length === 1 && state.models_in_use[0] !== state.configured_model);

  return (
    <div className="knowledge-panel embedding-panel">
      <div className="reference-actions">
        <button
          className={`knowledge-build-button${state?.needs_rerun ? " knowledge-build-button-stale" : ""}`}
          type="button"
          disabled={isRunning}
          onClick={() => void runBuild(false)}
        >
          {isRunning && !confirmingReembed ? "Building embeddings..." : "Build embeddings"}
        </button>

        {confirmingReembed ? (
          <div className="embedding-confirm">
            <span>Re-embed every node? This spends money proportional to corpus size.</span>
            <button
              className="embedding-secondary-button embedding-confirm-button"
              type="button"
              disabled={isRunning}
              onClick={() => void runBuild(true)}
            >
              Yes, re-embed all
            </button>
            <button
              className="embedding-secondary-button"
              type="button"
              onClick={() => setConfirmingReembed(false)}
            >
              Cancel
            </button>
          </div>
        ) : (
          <button
            className="embedding-secondary-button"
            type="button"
            disabled={isRunning}
            onClick={() => setConfirmingReembed(true)}
          >
            Re-embed all
          </button>
        )}

        <div className="reference-status">
          <span>Model: <strong>{state?.configured_model ?? EMBEDDING_MODEL_FALLBACK}</strong> ({state?.configured_dimensions ?? "-"} dims)</span>
          <span>Last embedding run: {formatTimestamp(state?.last_embedding_at ?? null)}</span>
          <span>Last import: {formatTimestamp(state?.last_import_at ?? null)}</span>
          <span>Last knowledge build: {formatTimestamp(state?.last_layer_build_at ?? null)}</span>
          {state?.needs_rerun ? (
            <span className="reference-stale">Import or knowledge build happened since the last embedding run. Run it again.</span>
          ) : null}
          {mixedModels ? (
            <span className="reference-stale">Mixed embedding models in the graph: {state?.models_in_use.join(", ")}.</span>
          ) : null}
        </div>
      </div>

      {error ? <p className="reference-error">{error}</p> : null}
      {justRan && !error ? (
        <p className="reference-success">
          {(state?.embedded ?? 0) === 0
            ? "0 nodes embedded — everything is up to date."
            : `Done. ${state?.embedded ?? 0} nodes embedded, ${state?.skipped ?? 0} skipped, ${state?.failed ?? 0} failed.`}
          {state?.token_usage ? ` (${state.token_usage.total_tokens} tokens)` : ""}
        </p>
      ) : null}

      <div className="reference-table-wrapper">
        <table className="reference-table">
          <thead>
            <tr>
              <th>Label</th>
              <th>Total nodes</th>
              <th>Embedded</th>
              <th>Missing</th>
            </tr>
          </thead>
          <tbody>
            {perLabel.map((row) => (
              <tr key={row.label}>
                <td>{row.label}</td>
                <td>{row.total}</td>
                <td>{row.embedded}</td>
                <td>{row.missing}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {failures.length > 0 ? (
        <div className="embedding-failures">
          <h4 className="knowledge-card-title">Failures from the last run</h4>
          <table className="reference-table">
            <thead>
              <tr>
                <th>Label</th>
                <th>Node key</th>
                <th>Error</th>
              </tr>
            </thead>
            <tbody>
              {failures.map((failure, index) => (
                <tr key={`${failure.label}-${index}`}>
                  <td>{failure.label}</td>
                  <td><code>{JSON.stringify(failure.key)}</code></td>
                  <td>{failure.error}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}

function BuildGraphLayersPanel() {
  const [activeInnerTab, setActiveInnerTab] = useState<"step1" | "step2" | "step3">("step1");

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
        <button
          className={`center-tab${activeInnerTab === "step3" ? " center-tab-active" : ""}`}
          type="button"
          role="tab"
          aria-selected={activeInnerTab === "step3"}
          onClick={() => setActiveInnerTab("step3")}
        >
          Embeddings
        </button>
      </div>
      <div className="center-tab-panel" role="tabpanel">
        {activeInnerTab === "step1" ? <ReferenceExtractionPanel /> : null}
        {activeInnerTab === "step2" ? <KnowledgeLayerPanel /> : null}
        {activeInnerTab === "step3" ? <EmbeddingPanel /> : null}
      </div>
    </div>
  );
}

function getOrCreateThreadId(): string {
  const storageKey = "ai-chat-thread-id";
  try {
    const existing = window.sessionStorage.getItem(storageKey);
    if (existing) {
      return existing;
    }
    const created = crypto.randomUUID();
    window.sessionStorage.setItem(storageKey, created);
    return created;
  } catch {
    // Private browsing / blocked storage: fall back to an in-memory id for this page load.
    return crypto.randomUUID();
  }
}

async function* readSseEvents(response: Response): AsyncGenerator<ChatSseEvent> {
  const body = response.body;
  if (!body) {
    return;
  }
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });

      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const rawEvent = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        for (const line of rawEvent.split("\n")) {
          if (line.startsWith("data:")) {
            const jsonText = line.slice(5).trim();
            if (jsonText) {
              try {
                yield JSON.parse(jsonText) as ChatSseEvent;
              } catch {
                // Ignore a malformed event rather than breaking the stream.
              }
            }
          }
        }
        boundary = buffer.indexOf("\n\n");
      }
    }
  } finally {
    reader.releaseLock();
  }
}

function ChatPanel({ graphViewRef }: { graphViewRef: React.RefObject<GraphViewHandle | null> }) {
  const [threadId] = useState(getOrCreateThreadId);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [status, setStatus] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState("");
  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, status]);

  const sendMessage = async () => {
    const message = input.trim();
    if (!message || isStreaming) {
      return;
    }

    setError("");
    setStatus("");
    setIsStreaming(true);
    setInput("");

    const userMessageId = `u-${Date.now()}`;
    const assistantMessageId = `a-${Date.now()}`;
    setMessages((previous) => [
      ...previous,
      { id: userMessageId, role: "user", content: message },
      { id: assistantMessageId, role: "assistant", content: "" },
    ]);

    const updateAssistant = (update: Partial<ChatMessage>) => {
      setMessages((previous) =>
        previous.map((entry) => (entry.id === assistantMessageId ? { ...entry, ...update } : entry)),
      );
    };

    try {
      const response = await fetch("/api/ai/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, thread_id: threadId }),
      });

      if (!response.ok) {
        const data = (await response.json().catch(() => ({}))) as { error?: string };
        throw new Error(data.error || `Chat request failed (${response.status}).`);
      }

      let streamedContent = "";
      for await (const event of readSseEvents(response)) {
        if (event.type === "status") {
          setStatus(event.text);
        } else if (event.type === "token") {
          streamedContent += event.text;
          updateAssistant({ content: streamedContent });
        } else if (event.type === "sources") {
          updateAssistant({ citations: event.citations, droppedCitations: event.dropped_citations });
        } else if (event.type === "tool_error") {
          setMessages((previous) =>
            previous.map((entry) =>
              entry.id === assistantMessageId
                ? { ...entry, toolErrors: [...(entry.toolErrors ?? []), { node: event.node, tool: event.tool, error: event.error }] }
                : entry,
            ),
          );
        } else if (event.type === "done") {
          if (event.error) {
            updateAssistant({ error: event.error });
            setError(event.error);
          } else {
            updateAssistant({
              content: event.answer,
              citations: event.citations ?? [],
              droppedCitations: event.dropped_citations ?? [],
            });
          }
          setStatus("");
        }
      }
    } catch (streamError) {
      const messageText = streamError instanceof Error ? streamError.message : "Chat request failed.";
      setError(messageText);
      updateAssistant({ error: messageText });
      setStatus("");
    } finally {
      setIsStreaming(false);
    }
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void sendMessage();
    }
  };

  const selectCitation = (citation: ChatCitation) => {
    graphViewRef.current?.selectNodeByDisplayName(citation.label, citation.display_name);
  };

  return (
    <div className="chat-panel">
      <div className="chat-messages">
        {messages.length === 0 ? (
          <p className="reference-empty">Ask a question about the graph, or just say hello.</p>
        ) : (
          messages.map((entry) => (
            <div key={entry.id} className={`chat-message chat-message-${entry.role}`}>
              <div className="chat-message-role">{entry.role === "user" ? "You" : "AI"}</div>
              {entry.toolErrors && entry.toolErrors.length > 0 ? (
                <div className="chat-tool-errors">
                  <strong>Verktygsfel — grafen kunde inte frågas helt ut:</strong>
                  <ul>
                    {entry.toolErrors.map((toolError, index) => (
                      <li key={`${toolError.tool}-${index}`}>
                        {toolError.tool} ({toolError.node}): {toolError.error}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              <div className="chat-message-content">
                {entry.error ? <span className="reference-error">{entry.error}</span> : entry.content}
              </div>
              {entry.citations && entry.citations.length > 0 ? (
                <div className="chat-citations">
                  {entry.citations.map((citation) => (
                    <button
                      key={`${citation.label}-${citation.key}`}
                      type="button"
                      className="chat-citation-chip"
                      onClick={() => selectCitation(citation)}
                      title={`${citation.label}: ${citation.display_name}`}
                    >
                      {citation.display_name}
                    </button>
                  ))}
                </div>
              ) : null}
              {entry.droppedCitations && entry.droppedCitations.length > 0 ? (
                <p className="chat-dropped-citations">
                  Unresolved references (not in the retrieved context): {entry.droppedCitations.join(", ")}
                </p>
              ) : null}
            </div>
          ))
        )}
        {isStreaming && status ? <p className="chat-status">{status}</p> : null}
        <div ref={messagesEndRef} />
      </div>

      {error ? <p className="reference-error">{error}</p> : null}

      <div className="chat-input-row">
        <textarea
          className="ai-chat-input"
          aria-label="Message to AI"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Write a message... (Enter to send, Shift+Enter for a new line)"
        />
        <button
          className="ai-send-button"
          type="button"
          disabled={!input.trim() || isStreaming}
          onClick={() => void sendMessage()}
        >
          {isStreaming ? "Thinking..." : "Send"}
        </button>
      </div>
    </div>
  );
}

function App() {
  const [activeCenterTab, setActiveCenterTab] = useState<"message" | "notes">("message");
  const graphViewRef = useRef<GraphViewHandle>(null);

  return (
    <main>
      <GraphView ref={graphViewRef} />
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
          {activeCenterTab === "message" ? <ChatPanel graphViewRef={graphViewRef} /> : <BuildGraphLayersPanel />}
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
