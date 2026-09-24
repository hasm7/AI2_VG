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

type DataSource =
  | "Mail"
  | "Slack"
  | "Teams"
  | "Issues"
  | "Docs"
  | "PRs"
  | "References"
  | "Knowledge"
  | "Architecture"
  | "Causal"
  | "Collaboration"
  | "Algorithms";

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
  stale_reasons: string[];
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
  stale_reasons: string[];
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
  last_architecture_build_at?: string | null;
  last_causal_build_at?: string | null;
  needs_rerun: boolean;
  stale_reasons: string[];
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

type ArchitectureTokenUsage = {
  input_tokens: number;
  output_tokens: number;
};

type ArchitectureCounts = {
  repositories: number;
  modules: number;
  files: number;
  components: number;
  dependencies: number;
};

type ArchitectureRepository = {
  source_instance: string;
  name: string;
  module_count: number;
  component_count: number;
};

type ArchitectureModule = {
  repository: string;
  path: string;
  file_count: number;
};

type ArchitectureComponent = {
  repository: string;
  slug: string;
  name: string;
  component_type: string;
  summary: string;
  files: string[];
  evidence: string[];
};

type ArchitectureDependency = {
  from_name: string;
  to_name: string;
  dependency_type: string;
  explanation: string;
  evidence: string[];
};

type ArchitectureFile = {
  repository: string;
  module: string;
  path: string;
  change_count: number;
  modified_by: string[];
  components: string[];
};

type LayerRelationship = {
  relationship_type: string;
  from_labels: string[];
  to_labels: string[];
  count: number;
};

type ArchitectureState = {
  last_import_at: string | null;
  last_extraction_at: string | null;
  last_architecture_build_at: string | null;
  needs_rerun: boolean;
  stale_reasons: string[];
  counts: ArchitectureCounts;
  repositories: ArchitectureRepository[];
  modules: ArchitectureModule[];
  components: ArchitectureComponent[];
  dependencies: ArchitectureDependency[];
  files: ArchitectureFile[];
  relationships: LayerRelationship[];
  built_at?: string;
  deleted_relationships?: number;
  deleted_nodes?: number;
  calls?: number;
  model?: string;
  discarded_evidence?: number;
  discarded_file_paths?: number;
  token_usage?: ArchitectureTokenUsage | null;
  error?: string;
};

type CausalTokenUsage = {
  input_tokens: number;
  output_tokens: number;
};

type CausalCounts = {
  root_causes: number;
  code_contributions: number;
  affected_components: number;
  cross_topic_links: number;
  within_topic_links: number;
};

type CausalRootCause = {
  slug: string;
  name: string;
  cause_type: string;
  summary: string;
  events: string[];
  components: string[];
  evidence: string[];
};

type CausalRootCauseLink = {
  event_name: string;
  topic_name: string | null;
  root_cause_name: string;
  explanation: string;
  evidence: string[];
};

type CausalCodeContribution = {
  code_change: string;
  file_path: string;
  event_name: string;
  topic_name: string;
  contribution_type: string;
  explanation: string;
  evidence: string[];
};

type CausalAffectedComponent = {
  event_name: string;
  topic_name: string;
  component_name: string;
  explanation: string;
  evidence: string[];
};

type CausalCrossTopicLink = {
  cause_name: string;
  cause_topic: string;
  effect_name: string;
  effect_topic: string;
  explanation: string;
  evidence: string[];
};

type CausalWithinTopicLink = {
  topic_name: string;
  cause_name: string;
  effect_name: string;
  explanation: string;
};

type CausalState = {
  last_layer_build_at: string | null;
  last_architecture_build_at: string | null;
  last_causal_build_at: string | null;
  needs_rerun: boolean;
  stale_reasons: string[];
  counts: CausalCounts;
  root_causes: CausalRootCause[];
  root_cause_links: CausalRootCauseLink[];
  code_contributions: CausalCodeContribution[];
  affected_components: CausalAffectedComponent[];
  cross_topic_links: CausalCrossTopicLink[];
  within_topic_links: CausalWithinTopicLink[];
  relationships: LayerRelationship[];
  built_at?: string;
  deleted_relationships?: number;
  deleted_nodes?: number;
  calls?: number;
  model?: string;
  discarded_evidence?: number;
  token_usage?: CausalTokenUsage | null;
  error?: string;
};

type CollaborationCounts = {
  expertise: number;
  persons_with_expertise: number;
  works_with_pairs: number;
  excluded_persons: number;
};

type CollaborationExpertise = {
  person_name: string;
  subject_label: string;
  subject_name: string;
  score: number;
  share: number;
  rank: number;
  activity_count: number;
  first_activity_at: string | null;
  last_activity_at: string | null;
  evidence: string[];
};

type CollaborationWorksWith = {
  person_a: string;
  person_b: string;
  weight: number;
  work_item_types: string[];
  shared_work_items: string[];
};

type CollaborationExcludedPerson = {
  person_name: string;
  person_key: string;
  reason: string;
};

type CollaborationState = {
  last_import_at: string | null;
  last_layer_build_at: string | null;
  last_architecture_build_at: string | null;
  last_causal_build_at: string | null;
  last_collaboration_build_at: string | null;
  needs_rerun: boolean;
  stale_reasons: string[];
  counts: CollaborationCounts;
  expertise: CollaborationExpertise[];
  works_with: CollaborationWorksWith[];
  excluded_persons: CollaborationExcludedPerson[];
  relationships: LayerRelationship[];
  built_at?: string;
  deleted_relationships?: number;
  deleted_nodes?: number;
  error?: string;
};

type AlgorithmsCounts = {
  communities: number;
  community_memberships: number;
  persons: number;
  topics: number;
  components: number;
};

type AlgorithmsPerson = {
  name: string;
  collab_weighted_degree: number | null;
  collab_betweenness: number | null;
  community_id: string | null;
};

type AlgorithmsCommunity = {
  community_id: string;
  size: number;
  members: string[];
};

type AlgorithmsBusFactor = {
  subject_label: string;
  subject_name: string;
  bus_factor: number;
  expert_count: number;
  top_expert: string | null;
};

type AlgorithmsComponent = {
  name: string;
  depends_on_count: number | null;
  depended_on_by_count: number | null;
  affected_event_count: number | null;
  bus_factor: number | null;
};

type AlgorithmsState = {
  last_layer_build_at: string | null;
  last_architecture_build_at: string | null;
  last_causal_build_at: string | null;
  last_collaboration_build_at: string | null;
  last_algorithms_run_at: string | null;
  needs_rerun: boolean;
  stale_reasons: string[];
  counts: AlgorithmsCounts;
  persons: AlgorithmsPerson[];
  communities: AlgorithmsCommunity[];
  bus_factor: AlgorithmsBusFactor[];
  components: AlgorithmsComponent[];
  run_at?: string;
  deleted_relationships?: number;
  deleted_nodes?: number;
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

const dataSources: Array<DataSource | "All"> = [
  "All",
  "Mail",
  "Slack",
  "Teams",
  "Issues",
  "Docs",
  "PRs",
  "References",
  "Knowledge",
  "Architecture",
  "Causal",
  "Collaboration",
  "Algorithms",
];
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
  Repository: "#eab308",
  Module: "#fb923c",
  File: "#fbbf24",
  Component: "#34d399",
  RootCause: "#f43f5e",
  Expertise: "#22d3ee",
  Community: "#a3e635",
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
  const areLabelsVisibleRef = useRef(false);
  const pendingSelectionRef = useRef<{ type: string; displayName: string } | null>(null);
  const [selection, setSelection] = useState<Selection>(null);
  const [isExpanded, setIsExpanded] = useState(false);
  const [isMotionPaused, setIsMotionPaused] = useState(false);
  const [isMotionMenuOpen, setIsMotionMenuOpen] = useState(false);
  const [isLegendVisible, setIsLegendVisible] = useState(true);
  const [motionLevel, setMotionLevel] = useState(1);
  const [spacingLevel, setSpacingLevel] = useState(1);
  const [areLabelsVisible, setAreLabelsVisible] = useState(false);
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
            <span className="reference-stale">
              An import has happened since the last extraction. Run it again.
              <ul>
                {state.stale_reasons.map((reason, index) => (
                  <li key={index}>{reason}</li>
                ))}
              </ul>
            </span>
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
            <span className="reference-stale">
              References have changed since the last build. Run it again.
              <ul>
                {state.stale_reasons.map((reason, index) => (
                  <li key={index}>{reason}</li>
                ))}
              </ul>
            </span>
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

function ArchitectureLayerPanel() {
  const [state, setState] = useState<ArchitectureState | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState("");
  const [justRan, setJustRan] = useState(false);

  const loadState = async () => {
    try {
      const response = await fetch("/api/architecture");
      const data = (await response.json()) as ArchitectureState;
      if (!response.ok || data.error) {
        throw new Error(data.error || "Could not read the architecture layer.");
      }
      setState(data);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Could not read the architecture layer.");
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
      const response = await fetch("/api/architecture/build", { method: "POST" });
      const data = (await response.json()) as ArchitectureState;
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

  const repositories = state?.repositories ?? [];
  const modules = state?.modules ?? [];
  const components = state?.components ?? [];
  const dependencies = state?.dependencies ?? [];
  const files = state?.files ?? [];
  const relationships = state?.relationships ?? [];
  const counts = state?.counts;

  return (
    <div className="knowledge-panel architecture-panel">
      <div className="reference-actions">
        <button
          className={`knowledge-build-button${state?.needs_rerun ? " knowledge-build-button-stale" : ""}`}
          type="button"
          disabled={isRunning}
          onClick={runBuild}
        >
          {isRunning ? "Building architecture layer..." : "Build architecture layer"}
        </button>
        <div className="reference-status">
          <span>Last import: {formatTimestamp(state?.last_import_at ?? null)}</span>
          <span>Last reference extraction: {formatTimestamp(state?.last_extraction_at ?? null)}</span>
          <span>Last architecture build: {formatTimestamp(state?.last_architecture_build_at ?? null)}</span>
          {state?.needs_rerun ? (
            <span className="reference-stale">
              Upstream data has changed since the last build. Run it again.
              <ul>
                {state.stale_reasons.map((reason, index) => (
                  <li key={index}>{reason}</li>
                ))}
              </ul>
            </span>
          ) : null}
        </div>
      </div>

      <p className="reference-description">Models how the code is structured and how its components depend on each other.</p>

      {error ? <p className="reference-error">{error}</p> : null}
      {justRan && !error ? (
        <p className="reference-success">
          Done. {components.length} components, {dependencies.length} dependencies, {files.length} files.
        </p>
      ) : null}

      {counts ? <p className="reference-description reference-counts-heading">Nodes and relationships:</p> : null}
      {counts ? (
        <div className="reference-counts">
          <span className="reference-count">Repositories: <strong>{counts.repositories}</strong></span>
          <span className="reference-count">Modules: <strong>{counts.modules}</strong></span>
          <span className="reference-count">Files: <strong>{counts.files}</strong></span>
          <span className="reference-count">Components: <strong>{counts.components}</strong></span>
          <span className="reference-count">Dependencies: <strong>{counts.dependencies}</strong></span>
        </div>
      ) : null}

      {justRan && !error ? (
        <div className="reference-counts">
          <span className="reference-count">Model: <strong>{state?.model ?? "-"}</strong></span>
          <span className="reference-count">Calls: <strong>{state?.calls ?? 0}</strong></span>
          <span className="reference-count">
            Tokens:{" "}
            <strong>
              {state?.token_usage ? `${state.token_usage.input_tokens} in / ${state.token_usage.output_tokens} out` : "n/a"}
            </strong>
          </span>
          <span className="reference-count">Discarded evidence: <strong>{state?.discarded_evidence ?? 0}</strong></span>
          <span className="reference-count">Discarded file paths: <strong>{state?.discarded_file_paths ?? 0}</strong></span>
        </div>
      ) : null}

      {components.length === 0 ? (
        <p className="reference-empty">No architecture layer yet. Press the button to build it.</p>
      ) : (
        <>
          <h4 className="knowledge-card-title knowledge-section-title">
            Repositories <span className="knowledge-section-kind">(node)</span>
          </h4>
          <div className="reference-table-wrapper">
            <table className="reference-table">
              <thead>
                <tr>
                  <th>
                    Source instance <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th>
                    Repository <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th>
                    Modules <span className="knowledge-section-kind">(via CONTAINS_MODULE)</span>
                  </th>
                  <th>
                    Components <span className="knowledge-section-kind">(via PART_OF_REPOSITORY)</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {repositories.map((repository) => (
                  <tr key={`${repository.source_instance}-${repository.name}`}>
                    <td>{repository.source_instance}</td>
                    <td>{repository.name}</td>
                    <td>{repository.module_count}</td>
                    <td>{repository.component_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h4 className="knowledge-card-title knowledge-section-title">
            Modules <span className="knowledge-section-kind">(node)</span>
          </h4>
          <div className="reference-table-wrapper">
            <table className="reference-table">
              <thead>
                <tr>
                  <th>
                    Repository <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th>
                    Module <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th>
                    Files <span className="knowledge-section-kind">(via CONTAINS_FILE)</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {modules.map((module) => (
                  <tr key={`${module.repository}-${module.path}`}>
                    <td>{module.repository}</td>
                    <td>{module.path}</td>
                    <td>{module.file_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h4 className="knowledge-card-title knowledge-section-title">
            Files <span className="knowledge-section-kind">(node)</span>
          </h4>
          <div className="reference-table-wrapper">
            <table className="reference-table">
              <thead>
                <tr>
                  <th>
                    Repository <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th>
                    Module <span className="knowledge-section-kind">(via CONTAINS_FILE)</span>
                  </th>
                  <th>
                    File <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th className="reference-cell-center">
                    Changes <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Modified by <span className="knowledge-section-kind">(via MODIFIES_FILE)</span>
                  </th>
                  <th>
                    Components <span className="knowledge-section-kind">(via IMPLEMENTED_IN)</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {files.map((file) => (
                  <tr key={`${file.repository}-${file.path}`}>
                    <td>{file.repository}</td>
                    <td>{file.module}</td>
                    <td>{file.path}</td>
                    <td className="reference-cell-center">{file.change_count}</td>
                    <td className="reference-cell-wrap">{file.modified_by.join(", ")}</td>
                    <td>{file.components.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h4 className="knowledge-card-title knowledge-section-title">
            Components <span className="knowledge-section-kind">(node)</span>
          </h4>
          <div className="reference-table-wrapper">
            <table className="reference-table">
              <thead>
                <tr>
                  <th>
                    Repository <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th>
                    Component <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th>
                    Type <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Summary <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th>
                    Files <span className="knowledge-section-kind">(via IMPLEMENTED_IN)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Evidence <span className="knowledge-section-kind">(via COMPONENT_EVIDENCED_BY)</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {components.map((component) => (
                  <tr key={`${component.repository}-${component.slug}`}>
                    <td>{component.repository}</td>
                    <td>{component.name}</td>
                    <td>{component.component_type}</td>
                    <td className="reference-cell-wrap">{component.summary}</td>
                    <td>{component.files.join(", ")}</td>
                    <td className="reference-cell-wrap">{component.evidence.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h4 className="knowledge-card-title knowledge-section-title">
            Relationships <span className="knowledge-section-kind">(all relationship types)</span>
          </h4>
          <div className="reference-table-wrapper">
            <table className="reference-table">
              <thead>
                <tr>
                  <th>Relationship</th>
                  <th>From → To</th>
                  <th>Count</th>
                </tr>
              </thead>
              <tbody>
                {relationships.map((relationship) => (
                  <tr key={relationship.relationship_type}>
                    <td>{relationship.relationship_type}</td>
                    <td>
                      {relationship.from_labels.join(", ")} → {relationship.to_labels.join(", ")}
                    </td>
                    <td>{relationship.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h4 className="knowledge-card-title knowledge-section-title">
            Dependencies <span className="knowledge-section-kind">(relationship: DEPENDS_ON)</span>
          </h4>
          <div className="reference-table-wrapper layer-last-table">
            <table className="reference-table">
              <thead>
                <tr>
                  <th>
                    From <span className="knowledge-section-kind">(start node)</span>
                  </th>
                  <th>
                    To <span className="knowledge-section-kind">(end node)</span>
                  </th>
                  <th>
                    Type <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Explanation <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Evidence <span className="knowledge-section-kind">(property)</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {dependencies.map((dependency, index) => (
                  <tr key={`${dependency.from_name}-${dependency.to_name}-${index}`}>
                    <td>{dependency.from_name}</td>
                    <td>{dependency.to_name}</td>
                    <td>{dependency.dependency_type}</td>
                    <td className="reference-cell-wrap">{dependency.explanation}</td>
                    <td className="reference-cell-wrap">{dependency.evidence.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

function CausalLayerPanel() {
  const [state, setState] = useState<CausalState | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState("");
  const [justRan, setJustRan] = useState(false);

  const loadState = async () => {
    try {
      const response = await fetch("/api/causal");
      const data = (await response.json()) as CausalState;
      if (!response.ok || data.error) {
        throw new Error(data.error || "Could not read the root cause & impact layer.");
      }
      setState(data);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Could not read the root cause & impact layer.");
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
      const response = await fetch("/api/causal/build", { method: "POST" });
      const data = (await response.json()) as CausalState;
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

  const rootCauses = state?.root_causes ?? [];
  const rootCauseLinks = state?.root_cause_links ?? [];
  const codeContributions = state?.code_contributions ?? [];
  const affectedComponents = state?.affected_components ?? [];
  const crossTopicLinks = state?.cross_topic_links ?? [];
  const withinTopicLinks = state?.within_topic_links ?? [];
  const relationships = state?.relationships ?? [];
  const counts = state?.counts;

  return (
    <div className="knowledge-panel causal-panel">
      <div className="reference-actions">
        <button
          className={`knowledge-build-button${state?.needs_rerun ? " knowledge-build-button-stale" : ""}`}
          type="button"
          disabled={isRunning}
          onClick={runBuild}
        >
          {isRunning ? "Building root cause & impact layer..." : "Build root cause & impact layer"}
        </button>
        <div className="reference-status">
          <span>Last knowledge build: {formatTimestamp(state?.last_layer_build_at ?? null)}</span>
          <span>Last architecture build: {formatTimestamp(state?.last_architecture_build_at ?? null)}</span>
          <span>Last root cause &amp; impact build: {formatTimestamp(state?.last_causal_build_at ?? null)}</span>
          {state?.needs_rerun ? (
            <span className="reference-stale">
              Upstream data has changed since the last build. Run it again.
              <ul>
                {state.stale_reasons.map((reason, index) => (
                  <li key={index}>{reason}</li>
                ))}
              </ul>
            </span>
          ) : null}
        </div>
      </div>

      <p className="reference-description">
        Finds the root causes behind events, the code changes that contributed to them, and the components they affected.
      </p>

      {error ? <p className="reference-error">{error}</p> : null}
      {justRan && !error ? (
        <p className="reference-success">
          Done. {rootCauses.length} root causes, {codeContributions.length} code contributions,{" "}
          {affectedComponents.length} affected components, {crossTopicLinks.length} cross-topic links.
        </p>
      ) : null}

      {counts ? <p className="reference-description reference-counts-heading">Nodes and relationships:</p> : null}
      {counts ? (
        <div className="reference-counts">
          <span className="reference-count">Root causes: <strong>{counts.root_causes}</strong></span>
          <span className="reference-count">Code contributions: <strong>{counts.code_contributions}</strong></span>
          <span className="reference-count">Affected components: <strong>{counts.affected_components}</strong></span>
          <span className="reference-count">Cross-topic links: <strong>{counts.cross_topic_links}</strong></span>
        </div>
      ) : null}

      {counts ? <p className="reference-description reference-counts-heading">Read from Knowledge layer:</p> : null}
      {counts ? (
        <div className="reference-counts">
          <span className="reference-count">Within-topic links: <strong>{counts.within_topic_links}</strong></span>
        </div>
      ) : null}

      {justRan && !error ? (
        <div className="reference-counts">
          <span className="reference-count">Model: <strong>{state?.model ?? "-"}</strong></span>
          <span className="reference-count">Calls: <strong>{state?.calls ?? 0}</strong></span>
          <span className="reference-count">
            Tokens:{" "}
            <strong>
              {state?.token_usage ? `${state.token_usage.input_tokens} in / ${state.token_usage.output_tokens} out` : "n/a"}
            </strong>
          </span>
          <span className="reference-count">Discarded evidence: <strong>{state?.discarded_evidence ?? 0}</strong></span>
        </div>
      ) : null}

      {rootCauses.length === 0 && codeContributions.length === 0 && affectedComponents.length === 0
      && crossTopicLinks.length === 0 && withinTopicLinks.length === 0 ? (
        <p className="reference-empty">No root cause & impact layer yet. Press the button to build it.</p>
      ) : (
        <>
          <h4 className="knowledge-card-title knowledge-section-title">
            Root causes <span className="knowledge-section-kind">(node)</span>
          </h4>
          <div className="reference-table-wrapper">
            <table className="reference-table">
              <thead>
                <tr>
                  <th className="reference-cell-wrap">
                    Name <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th>
                    Type <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Summary <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Explains events <span className="knowledge-section-kind">(via HAS_ROOT_CAUSE)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Components <span className="knowledge-section-kind">(via ROOT_CAUSE_IN_COMPONENT)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Evidence <span className="knowledge-section-kind">(via ROOT_CAUSE_EVIDENCED_BY)</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {rootCauses.map((rc) => (
                  <tr key={rc.slug}>
                    <td className="reference-cell-wrap">{rc.name}</td>
                    <td>{rc.cause_type}</td>
                    <td className="reference-cell-wrap">{rc.summary}</td>
                    <td className="reference-cell-wrap">{rc.events.join(", ")}</td>
                    <td className="reference-cell-wrap">{rc.components.join(", ")}</td>
                    <td className="reference-cell-wrap">{rc.evidence.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h4 className="knowledge-card-title knowledge-section-title">
            Relationships <span className="knowledge-section-kind">(all relationship types)</span>
          </h4>
          <div className="reference-table-wrapper">
            <table className="reference-table">
              <thead>
                <tr>
                  <th>Relationship</th>
                  <th className="reference-cell-wrap">From → To</th>
                  <th className="reference-cell-center">Count</th>
                </tr>
              </thead>
              <tbody>
                {relationships.map((relationship) => (
                  <tr key={relationship.relationship_type}>
                    <td>{relationship.relationship_type}</td>
                    <td className="reference-cell-wrap">
                      {relationship.from_labels.join(", ")} → {relationship.to_labels.join(", ")}
                    </td>
                    <td className="reference-cell-center">{relationship.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h4 className="knowledge-card-title knowledge-section-title">
            Root cause links <span className="knowledge-section-kind">(relationship: HAS_ROOT_CAUSE)</span>
          </h4>
          <div className="reference-table-wrapper">
            <table className="reference-table">
              <thead>
                <tr>
                  <th className="reference-cell-wrap">
                    Event <span className="knowledge-section-kind">(start node)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Topic <span className="knowledge-section-kind">(via EVENT_OF_TOPIC)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Root cause <span className="knowledge-section-kind">(end node)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Explanation <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Evidence <span className="knowledge-section-kind">(property)</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {rootCauseLinks.map((row, index) => (
                  <tr key={`${row.event_name}-${row.root_cause_name}-${index}`}>
                    <td className="reference-cell-wrap">{row.event_name}</td>
                    <td className="reference-cell-wrap">{row.topic_name}</td>
                    <td className="reference-cell-wrap">{row.root_cause_name}</td>
                    <td className="reference-cell-wrap">{row.explanation}</td>
                    <td className="reference-cell-wrap">{row.evidence.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h4 className="knowledge-card-title knowledge-section-title">
            Code contributions <span className="knowledge-section-kind">(relationship: CONTRIBUTED_TO)</span>
          </h4>
          <div className="reference-table-wrapper">
            <table className="reference-table">
              <thead>
                <tr>
                  <th className="reference-cell-wrap">
                    Code change <span className="knowledge-section-kind">(start node)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    File <span className="knowledge-section-kind">(start node property)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Event <span className="knowledge-section-kind">(end node)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Topic <span className="knowledge-section-kind">(via EVENT_OF_TOPIC)</span>
                  </th>
                  <th>
                    Contribution <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Explanation <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Evidence <span className="knowledge-section-kind">(property)</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {codeContributions.map((row, index) => (
                  <tr key={`${row.code_change}-${row.event_name}-${index}`}>
                    <td className="reference-cell-wrap">{row.code_change}</td>
                    <td className="reference-cell-wrap">{row.file_path}</td>
                    <td className="reference-cell-wrap">{row.event_name}</td>
                    <td className="reference-cell-wrap">{row.topic_name}</td>
                    <td>{row.contribution_type}</td>
                    <td className="reference-cell-wrap">{row.explanation}</td>
                    <td className="reference-cell-wrap">{row.evidence.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h4 className="knowledge-card-title knowledge-section-title">
            Affected components <span className="knowledge-section-kind">(relationship: AFFECTED_COMPONENT)</span>
          </h4>
          <div className="reference-table-wrapper">
            <table className="reference-table">
              <thead>
                <tr>
                  <th className="reference-cell-wrap">
                    Event <span className="knowledge-section-kind">(start node)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Topic <span className="knowledge-section-kind">(via EVENT_OF_TOPIC)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Component <span className="knowledge-section-kind">(end node)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Explanation <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Evidence <span className="knowledge-section-kind">(property)</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {affectedComponents.map((row, index) => (
                  <tr key={`${row.event_name}-${row.component_name}-${index}`}>
                    <td className="reference-cell-wrap">{row.event_name}</td>
                    <td className="reference-cell-wrap">{row.topic_name}</td>
                    <td className="reference-cell-wrap">{row.component_name}</td>
                    <td className="reference-cell-wrap">{row.explanation}</td>
                    <td className="reference-cell-wrap">{row.evidence.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h4 className="knowledge-card-title knowledge-section-title">
            Cross-topic links <span className="knowledge-section-kind">(relationship: CROSS_TOPIC_CAUSED)</span>
          </h4>
          <div className="reference-table-wrapper">
            <table className="reference-table">
              <thead>
                <tr>
                  <th className="reference-cell-wrap">
                    Cause <span className="knowledge-section-kind">(start node)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Cause topic <span className="knowledge-section-kind">(via EVENT_OF_TOPIC)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Effect <span className="knowledge-section-kind">(end node)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Effect topic <span className="knowledge-section-kind">(via EVENT_OF_TOPIC)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Explanation <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Evidence <span className="knowledge-section-kind">(property)</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {crossTopicLinks.length === 0 ? (
                  <tr>
                    <td className="reference-empty" colSpan={6}>
                      No cross-topic links. These need events in at least two different topics.
                    </td>
                  </tr>
                ) : null}
                {crossTopicLinks.map((row, index) => (
                  <tr key={`${row.cause_name}-${row.effect_name}-${index}`}>
                    <td className="reference-cell-wrap">{row.cause_name}</td>
                    <td className="reference-cell-wrap">{row.cause_topic}</td>
                    <td className="reference-cell-wrap">{row.effect_name}</td>
                    <td className="reference-cell-wrap">{row.effect_topic}</td>
                    <td className="reference-cell-wrap">{row.explanation}</td>
                    <td className="reference-cell-wrap">{row.evidence.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h4 className="knowledge-card-title knowledge-section-title">
            Within-topic links{" "}
            <span className="knowledge-section-kind">(relationship: CAUSED, from Knowledge layer)</span>
          </h4>
          <p className="knowledge-table-caption">
            Created by the Knowledge layer. Rebuilding this layer does not change these links.
            <br />
            Sent to the model as context when finding the root causes above.
          </p>
          <div className="reference-table-wrapper layer-last-table">
            <table className="reference-table">
              <thead>
                <tr>
                  <th className="reference-cell-wrap">
                    Topic <span className="knowledge-section-kind">(via EVENT_OF_TOPIC)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Cause <span className="knowledge-section-kind">(start node)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Effect <span className="knowledge-section-kind">(end node)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Explanation <span className="knowledge-section-kind">(property)</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {withinTopicLinks.map((row, index) => (
                  <tr key={`${row.topic_name}-${row.cause_name}-${row.effect_name}-${index}`}>
                    <td className="reference-cell-wrap">{row.topic_name}</td>
                    <td className="reference-cell-wrap">{row.cause_name}</td>
                    <td className="reference-cell-wrap">{row.effect_name}</td>
                    <td className="reference-cell-wrap">{row.explanation}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

function CollaborationLayerPanel() {
  const [state, setState] = useState<CollaborationState | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState("");
  const [justRan, setJustRan] = useState(false);

  const loadState = async () => {
    try {
      const response = await fetch("/api/collaboration");
      const data = (await response.json()) as CollaborationState;
      if (!response.ok || data.error) {
        throw new Error(data.error || "Could not read the expertise & collaboration layer.");
      }
      setState(data);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Could not read the expertise & collaboration layer.");
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
      const response = await fetch("/api/collaboration/build", { method: "POST" });
      const data = (await response.json()) as CollaborationState;
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

  const expertise = state?.expertise ?? [];
  const worksWith = state?.works_with ?? [];
  const excludedPersons = state?.excluded_persons ?? [];
  const relationships = state?.relationships ?? [];
  const counts = state?.counts;

  return (
    <div className="knowledge-panel collaboration-panel">
      <div className="reference-actions">
        <button
          className={`knowledge-build-button${state?.needs_rerun ? " knowledge-build-button-stale" : ""}`}
          type="button"
          disabled={isRunning}
          onClick={runBuild}
        >
          {isRunning ? "Building expertise & collaboration layer..." : "Build expertise & collaboration layer"}
        </button>
        <div className="reference-status">
          <span>Last import: {formatTimestamp(state?.last_import_at ?? null)}</span>
          <span>Last knowledge build: {formatTimestamp(state?.last_layer_build_at ?? null)}</span>
          <span>Last architecture build: {formatTimestamp(state?.last_architecture_build_at ?? null)}</span>
          <span>Last root cause &amp; impact build: {formatTimestamp(state?.last_causal_build_at ?? null)}</span>
          {state?.needs_rerun ? (
            <span className="reference-stale">
              Upstream data has changed since the last build. Run it again.
              <ul>
                {state.stale_reasons.map((reason, index) => (
                  <li key={index}>{reason}</li>
                ))}
              </ul>
            </span>
          ) : null}
        </div>
      </div>

      <p className="reference-description">
        Scores each person&apos;s expertise per topic and component from their activity, and finds which people share
        work items.
      </p>

      {error ? <p className="reference-error">{error}</p> : null}
      {justRan && !error ? (
        <p className="reference-success">
          Done. {expertise.length} expertise entries, {worksWith.length} collaboration pairs.
        </p>
      ) : null}

      {counts ? <p className="reference-description reference-counts-heading">Nodes and relationships:</p> : null}
      {counts ? (
        <div className="reference-counts">
          <span className="reference-count">Expertise entries: <strong>{counts.expertise}</strong></span>
          <span className="reference-count">Collaboration pairs: <strong>{counts.works_with_pairs}</strong></span>
        </div>
      ) : null}

      {counts ? <p className="reference-description reference-counts-heading">People (existing Person nodes):</p> : null}
      {counts ? (
        <div className="reference-counts">
          <span className="reference-count">Persons with expertise: <strong>{counts.persons_with_expertise}</strong></span>
          <span className="reference-count">Excluded persons: <strong>{counts.excluded_persons}</strong></span>
        </div>
      ) : null}

      {expertise.length === 0 && worksWith.length === 0 ? (
        <p className="reference-empty">No expertise & collaboration layer yet. Press the button to build it.</p>
      ) : (
        <>
          <h4 className="knowledge-card-title knowledge-section-title">
            Expertise <span className="knowledge-section-kind">(node)</span>
          </h4>
          <div className="reference-table-wrapper reference-table-wrapper-capped">
            <table className="reference-table">
              <thead>
                <tr>
                  <th>
                    Person <span className="knowledge-section-kind">(via HAS_EXPERTISE)</span>
                  </th>
                  <th>
                    Subject type <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th>
                    Subject <span className="knowledge-section-kind">(via EXPERTISE_IN)</span>
                  </th>
                  <th>
                    Score <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th>
                    Share <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th>
                    Rank <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th>
                    Activities <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th>
                    First activity <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th>
                    Last activity <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Evidence <span className="knowledge-section-kind">(via EXPERTISE_EVIDENCED_BY)</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {expertise.map((row, index) => (
                  <tr key={`${row.person_name}-${row.subject_name}-${index}`}>
                    <td>{row.person_name}</td>
                    <td>{row.subject_label}</td>
                    <td>{row.subject_name}</td>
                    <td>{row.score}</td>
                    <td>{row.share}</td>
                    <td>{row.rank}</td>
                    <td>{row.activity_count}</td>
                    <td>{formatTimestamp(row.first_activity_at)}</td>
                    <td>{formatTimestamp(row.last_activity_at)}</td>
                    <td className="reference-cell-wrap">{row.evidence.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h4 className="knowledge-card-title knowledge-section-title">
            Relationships <span className="knowledge-section-kind">(all relationship types)</span>
          </h4>
          <div className="reference-table-wrapper">
            <table className="reference-table">
              <thead>
                <tr>
                  <th>Relationship</th>
                  <th className="reference-cell-wrap">From → To</th>
                  <th className="reference-cell-center">Count</th>
                </tr>
              </thead>
              <tbody>
                {relationships.map((relationship) => (
                  <tr key={relationship.relationship_type}>
                    <td>{relationship.relationship_type}</td>
                    <td className="reference-cell-wrap">
                      {relationship.from_labels.join(", ")} → {relationship.to_labels.join(", ")}
                    </td>
                    <td className="reference-cell-center">{relationship.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h4 className="knowledge-card-title knowledge-section-title">
            Collaboration <span className="knowledge-section-kind">(relationship: WORKS_WITH)</span>
          </h4>
          <div className="reference-table-wrapper">
            <table className="reference-table">
              <thead>
                <tr>
                  <th>
                    Person A <span className="knowledge-section-kind">(start node)</span>
                  </th>
                  <th>
                    Person B <span className="knowledge-section-kind">(end node)</span>
                  </th>
                  <th>
                    Weight <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Work item types <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Shared work items <span className="knowledge-section-kind">(property)</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {worksWith.map((row, index) => (
                  <tr key={`${row.person_a}-${row.person_b}-${index}`}>
                    <td>{row.person_a}</td>
                    <td>{row.person_b}</td>
                    <td>{row.weight}</td>
                    <td className="reference-cell-wrap">{row.work_item_types.join(", ")}</td>
                    <td className="reference-cell-wrap">{row.shared_work_items.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h4 className="knowledge-card-title knowledge-section-title">
            Excluded persons <span className="knowledge-section-kind">(existing Person nodes)</span>
          </h4>
          <p className="knowledge-table-caption">
            Not given expertise or collaboration: mailboxes are not people, and ambiguous identities could be matched
            to the wrong person.
          </p>
          <div className="reference-table-wrapper layer-last-table">
            <table className="reference-table">
              <thead>
                <tr>
                  <th>
                    Person <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th>
                    Person key <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th>
                    Reason <span className="knowledge-section-kind">(computed from properties)</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {excludedPersons.map((row) => (
                  <tr key={row.person_key}>
                    <td>{row.person_name}</td>
                    <td>{row.person_key}</td>
                    <td>{row.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

function GraphAlgorithmsPanel() {
  const [state, setState] = useState<AlgorithmsState | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState("");
  const [justRan, setJustRan] = useState(false);

  const loadState = async () => {
    try {
      const response = await fetch("/api/algorithms");
      const data = (await response.json()) as AlgorithmsState;
      if (!response.ok || data.error) {
        throw new Error(data.error || "Could not read the graph algorithm results.");
      }
      setState(data);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Could not read the graph algorithm results.");
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
      const response = await fetch("/api/algorithms/run", { method: "POST" });
      const data = (await response.json()) as AlgorithmsState;
      if (!response.ok || data.error) {
        throw new Error(data.error || "Run failed.");
      }
      setState(data);
      setJustRan(true);
    } catch (runError) {
      setError(runError instanceof Error ? runError.message : "Run failed.");
    } finally {
      setIsRunning(false);
    }
  };

  const persons = state?.persons ?? [];
  const communities = state?.communities ?? [];
  const busFactor = state?.bus_factor ?? [];
  const components = state?.components ?? [];
  const counts = state?.counts;
  const personsByDegree = [...persons].sort(
    (a, b) => (b.collab_weighted_degree ?? -1) - (a.collab_weighted_degree ?? -1),
  );
  const personsByBetweenness = [...persons].sort(
    (a, b) => (b.collab_betweenness ?? -1) - (a.collab_betweenness ?? -1),
  );

  return (
    <div className="knowledge-panel algorithms-panel">
      <div className="reference-actions">
        <button
          className={`knowledge-build-button${state?.needs_rerun ? " knowledge-build-button-stale" : ""}`}
          type="button"
          disabled={isRunning}
          onClick={runBuild}
        >
          {isRunning ? "Running graph algorithms..." : "Run graph algorithms"}
        </button>
        <div className="reference-status">
          <span>Last knowledge build: {formatTimestamp(state?.last_layer_build_at ?? null)}</span>
          <span>Last architecture build: {formatTimestamp(state?.last_architecture_build_at ?? null)}</span>
          <span>Last root cause &amp; impact build: {formatTimestamp(state?.last_causal_build_at ?? null)}</span>
          <span>Last expertise &amp; collaboration build: {formatTimestamp(state?.last_collaboration_build_at ?? null)}</span>
          <span>Last algorithms run: {formatTimestamp(state?.last_algorithms_run_at ?? null)}</span>
          {state?.needs_rerun ? (
            <span className="reference-stale">
              Upstream data has changed since the last run. Run it again.
              <ul>
                {state.stale_reasons.map((reason, index) => (
                  <li key={index}>{reason}</li>
                ))}
              </ul>
            </span>
          ) : null}
        </div>
      </div>

      <p className="reference-description">
        Computes collaboration metrics, communities and bus factor from the existing graph. Metrics are stored as
        properties on existing nodes; communities are created as new Community nodes.
      </p>

      {error ? <p className="reference-error">{error}</p> : null}
      {justRan && !error ? (
        <p className="reference-success">
          Done. {communities.length} communities, {persons.length} persons analyzed.
        </p>
      ) : null}

      {counts ? <p className="reference-description reference-counts-heading">Nodes and relationships:</p> : null}
      {counts ? (
        <div className="reference-counts">
          <span className="reference-count">Communities: <strong>{counts.communities}</strong></span>
          <span className="reference-count">Community memberships: <strong>{counts.community_memberships}</strong></span>
        </div>
      ) : null}

      {counts ? <p className="reference-description reference-counts-heading">Analyzed (existing nodes):</p> : null}
      {counts ? (
        <div className="reference-counts">
          <span className="reference-count">Persons: <strong>{counts.persons}</strong></span>
          <span className="reference-count">Topics: <strong>{counts.topics}</strong></span>
          <span className="reference-count">Components: <strong>{counts.components}</strong></span>
        </div>
      ) : null}

      {persons.length === 0 && communities.length === 0 ? (
        <p className="reference-empty">No algorithm results yet. Press the button to run them.</p>
      ) : (
        <>
          <h4 className="knowledge-card-title knowledge-section-title">
            Communities <span className="knowledge-section-kind">(node, Louvain community detection algorithm)</span>
          </h4>
          <p className="knowledge-table-caption">
            Groups of people whose collaboration is stronger inside the group than with people outside it.
            <br />
            Louvain starts with every person in their own group, moves people between groups as long as the grouping
            improves, then merges the groups. Stronger WORKS_WITH weights pull people into the same group.
            <br />
            Louvain tries people in a random order, so two runs could give different groups. The random order always
            starts from the same fixed value (seed 42), so rerunning on the same data always gives the same groups.
            <br />
            Groups are then numbered collab-1, collab-2, … from largest to smallest.
          </p>
          <div className="reference-table-wrapper">
            <table className="reference-table">
              <thead>
                <tr>
                  <th>
                    Community <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th>
                    Size <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th className="reference-cell-wrap">
                    Members <span className="knowledge-section-kind">(via MEMBER_OF_COMMUNITY)</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {communities.map((row) => (
                  <tr key={row.community_id}>
                    <td>{row.community_id}</td>
                    <td>{row.size}</td>
                    <td className="reference-cell-wrap">{row.members.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h4 className="knowledge-card-title knowledge-section-title">
            Weighted degree <span className="knowledge-section-kind">(graph metric, written to Person node)</span>
          </h4>
          <p className="knowledge-table-caption">
            How much each person collaborates in total.
            <br />
            Adds up the weights of all the person&apos;s WORKS_WITH links. The weight is the number of work items two
            people share: issues, PRs, meetings, mail threads and events.
            <br />
            High value: the person is heavily involved in shared work, often a key person others depend on day to day.
            Low value: the person mostly works alone, or is new or on the edge of the team.
            <br />
            Shows how much a person collaborates, not how important they are for holding the team together; see
            Betweenness centrality below.
          </p>
          <div className="reference-table-wrapper">
            <table className="reference-table">
              <thead>
                <tr>
                  <th>
                    Person <span className="knowledge-section-kind">(Person node)</span>
                  </th>
                  <th>
                    Weighted degree <span className="knowledge-section-kind">(property)</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {personsByDegree.map((row) => (
                  <tr key={row.name}>
                    <td>{row.name}</td>
                    <td>{row.collab_weighted_degree ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h4 className="knowledge-card-title knowledge-section-title">
            Betweenness centrality{" "}
            <span className="knowledge-section-kind">(betweenness centrality algorithm, written to Person node)</span>
          </h4>
          <p className="knowledge-table-caption">
            How often a person lies on the shortest path between two other people.
            <br />
            For every pair of people, the algorithm finds the shortest path between them. Distance is 1 / weight, so
            people who share many work items are close. Each person&apos;s value is the share of all shortest paths
            between other pairs that pass through them, from 0 (never in between) to 1 (every path passes through them).
            <br />
            High value: the person is a bridge that connects groups which otherwise have no contact. If they leave, are
            away or are overloaded, the groups lose touch and information stops flowing, and everything passing through
            them can make them a bottleneck.
            <br />
            Shows where a person sits in the network, not how much they collaborate; see Weighted degree above.
          </p>
          <div className="reference-table-wrapper">
            <table className="reference-table">
              <thead>
                <tr>
                  <th>
                    Person <span className="knowledge-section-kind">(Person node)</span>
                  </th>
                  <th>
                    Betweenness <span className="knowledge-section-kind">(property)</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {personsByBetweenness.map((row) => (
                  <tr key={row.name}>
                    <td>{row.name}</td>
                    <td>{row.collab_betweenness ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h4 className="knowledge-card-title knowledge-section-title">
            Bus factor <span className="knowledge-section-kind">(algorithm on expertise shares, written to Topic and Component nodes)</span>
          </h4>
          <div className="knowledge-table-caption">
            <p>How few people hold more than half of the knowledge about a topic or component.</p>
            <p>
              <strong>How it is calculated</strong>
              <br />
              Takes every expert&apos;s share of the expertise (from the Expertise &amp; collaboration layer).
              <br />
              Sorts them largest first and adds up the shares until they reach at least 50%.
              <br />
              The bus factor is the number of people that took.
            </p>
            <p>
              <strong>Bus factor</strong>: how bad is it if the top expert disappears?
              <br />
              1 means one person holds more than half of the knowledge. If they leave, it is gone.
              <br />
              A high value means the knowledge is spread, so no single person is critical.
            </p>
            <p>
              <strong>Experts</strong>: how many people are there to ask?
              <br />
              Everyone with any expertise in the subject, no matter how much.
              <br />
              Top expert is the one with the largest share.
            </p>
            <p>
              <strong>Together</strong>: how evenly the knowledge is spread.
              <br />
              Many experts but a low bus factor means the knowledge exists but is unevenly shared.
              <br />
              Sorted lowest bus factor first, so the subjects where knowledge should be spread come first.
            </p>
          </div>
          <div className="reference-table-wrapper">
            <table className="reference-table">
              <thead>
                <tr>
                  <th>
                    Subject type <span className="knowledge-section-kind">(node label)</span>
                  </th>
                  <th>
                    Subject <span className="knowledge-section-kind">(Topic or Component node)</span>
                  </th>
                  <th>
                    Bus factor <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th>
                    Experts <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th>
                    Top expert <span className="knowledge-section-kind">(property)</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {busFactor.map((row, index) => (
                  <tr key={`${row.subject_label}-${row.subject_name}-${index}`}>
                    <td>{row.subject_label}</td>
                    <td>{row.subject_name}</td>
                    <td>{row.bus_factor}</td>
                    <td>{row.expert_count}</td>
                    <td>{row.top_expert ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h4 className="knowledge-card-title knowledge-section-title">
            Component metrics <span className="knowledge-section-kind">(simple counts, written to Component node)</span>
          </h4>
          <div className="knowledge-table-caption">
            <p>Simple counts of existing relationships, not an algorithm.</p>
            <p>
              <strong>Depends on</strong>: counts outgoing DEPENDS_ON (from the Architecture layer).
              <br />
              How many other components this component depends on. A high value means that if something it uses
              breaks, it is affected.
            </p>
            <p>
              <strong>Depended on by</strong>: counts incoming DEPENDS_ON (from the Architecture layer).
              <br />
              How many other components depend on this one. A high value means the component is central, so a change
              or fault here spreads.
            </p>
            <p>
              <strong>Affected events</strong>: counts incoming AFFECTED_COMPONENT (from the Root cause &amp; impact
              layer).
              <br />
              How many events have affected this component. A high value means it is a problem area.
            </p>
          </div>
          <div className="reference-table-wrapper layer-last-table">
            <table className="reference-table">
              <thead>
                <tr>
                  <th>
                    Component <span className="knowledge-section-kind">(Component node)</span>
                  </th>
                  <th>
                    Depends on <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th>
                    Depended on by <span className="knowledge-section-kind">(property)</span>
                  </th>
                  <th>
                    Affected events <span className="knowledge-section-kind">(property)</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {components.map((row) => (
                  <tr key={row.name}>
                    <td>{row.name}</td>
                    <td>{row.depends_on_count ?? "-"}</td>
                    <td>{row.depended_on_by_count ?? "-"}</td>
                    <td>{row.affected_event_count ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
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
            <span className="reference-stale">
              Import or knowledge build happened since the last embedding run. Run it again.
              <ul>
                {state.stale_reasons.map((reason, index) => (
                  <li key={index}>{reason}</li>
                ))}
              </ul>
            </span>
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

type BuildGraphLayersTab =
  | "reference"
  | "knowledge"
  | "architecture"
  | "causal"
  | "collaboration"
  | "algorithms"
  | "embeddings";

const buildGraphLayersTabs: Array<{ id: BuildGraphLayersTab; label: string }> = [
  { id: "reference", label: "Reference extraction" },
  { id: "knowledge", label: "Knowledge layer" },
  { id: "architecture", label: "Architecture layer" },
  { id: "causal", label: "Root cause & impact layer" },
  { id: "collaboration", label: "Expertise & collaboration layer" },
  { id: "algorithms", label: "Graph algorithms" },
  { id: "embeddings", label: "Embeddings" },
];

function BuildGraphLayersPanel() {
  const [activeInnerTab, setActiveInnerTab] = useState<BuildGraphLayersTab>("reference");

  return (
    <div className="build-graph-layers">
      <div className="center-tabs center-tabs-inner" role="tablist" aria-label="Build graph layers tabs">
        {buildGraphLayersTabs.map((tab) => (
          <button
            key={tab.id}
            className={`center-tab${activeInnerTab === tab.id ? " center-tab-active" : ""}`}
            type="button"
            role="tab"
            aria-selected={activeInnerTab === tab.id}
            onClick={() => setActiveInnerTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div className="center-tab-panel" role="tabpanel">
        {activeInnerTab === "reference" ? <ReferenceExtractionPanel /> : null}
        {activeInnerTab === "knowledge" ? <KnowledgeLayerPanel /> : null}
        {activeInnerTab === "architecture" ? <ArchitectureLayerPanel /> : null}
        {activeInnerTab === "causal" ? <CausalLayerPanel /> : null}
        {activeInnerTab === "collaboration" ? <CollaborationLayerPanel /> : null}
        {activeInnerTab === "algorithms" ? <GraphAlgorithmsPanel /> : null}
        {activeInnerTab === "embeddings" ? <EmbeddingPanel /> : null}
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

function RightGraphicsPanel() {
  return (
    <aside className="right-graphics-panel" aria-label="Layer graphics">
      <svg className="layer-ladder-svg" viewBox="0 0 420 360" role="img" aria-label="Graph layer map">
        <defs>
          <filter id="layerMapGlow" x="-80%" y="-80%" width="260%" height="260%">
            <feGaussianBlur stdDeviation="2.5" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        <path className="layer-map-line" d="M110 76 Q88 76 88 90 V100 Q88 108 80 108 Q88 108 88 116 V126 Q88 140 110 140" />
        <path className="layer-map-line" d="M110 148 Q88 148 88 162 V172 Q88 180 80 180 Q88 180 88 188 V198 Q88 212 110 212" />
        <path className="layer-map-line" d="M110 222 Q88 222 88 236 V244 Q88 252 80 252 Q88 252 88 260 V270 Q88 284 110 284" />
        <path className="layer-map-beam" pathLength={100} d="M110 76 Q88 76 88 90 V100 Q88 108 80 108 Q88 108 88 116 V126 Q88 140 110 140" />
        <path className="layer-map-beam" pathLength={100} style={{ animationDelay: "-1.1s" }} d="M110 148 Q88 148 88 162 V172 Q88 180 80 180 Q88 180 88 188 V198 Q88 212 110 212" />
        <path className="layer-map-beam" pathLength={100} style={{ animationDelay: "-2.2s" }} d="M110 222 Q88 222 88 236 V244 Q88 252 80 252 Q88 252 88 260 V270 Q88 284 110 284" />

        <g className="layer-map-node" transform="translate(126 58)">
          <circle className="layer-map-dot" cx="0" cy="18" r="5" />
          <circle className="layer-map-dot layer-map-dot-secondary" cx="16" cy="10" r="4" />
          <line className="layer-map-icon-line" x1="0" y1="18" x2="16" y2="10" />
          <text x="40" y="22">Expertise &amp; collaboration layer</text>
        </g>

        <g className="layer-map-node" transform="translate(126 124)">
          <path className="layer-map-icon-line" d="M0 20 H22" />
          <path className="layer-map-icon-line" d="M17 14 L24 20 L17 26" />
          <circle className="layer-map-dot" cx="0" cy="20" r="4" />
          <circle className="layer-map-dot layer-map-dot-secondary" cx="26" cy="20" r="4" />
          <text x="40" y="24">Root cause &amp; impact layer</text>
        </g>

        <g className="layer-map-node" transform="translate(126 202)">
          <rect className="layer-map-icon-box" x="-2" y="6" width="24" height="20" rx="3" />
          <path className="layer-map-icon-line" d="M4 13 H18 M4 19 H13" />
          <text x="40" y="23">Architecture layer</text>
        </g>

        <g className="layer-map-node" transform="translate(126 268)">
          <circle className="layer-map-icon-orbit" cx="12" cy="18" r="15" />
          <circle className="layer-map-dot" cx="12" cy="18" r="4" />
          <circle className="layer-map-dot layer-map-dot-secondary" cx="25" cy="12" r="3" />
          <text x="40" y="23">Knowledge layer</text>
        </g>
      </svg>

      <svg className="knowledge-layer-svg" viewBox="0 0 420 260" role="img" aria-label="Knowledge layer visualizer">
        <ellipse className="knowledge-orbit knowledge-orbit-main" cx="210" cy="132" rx="194" ry="72" />

        <g className="knowledge-cluster">
          <g className="knowledge-counter">
            <line x1="120" y1="142" x2="218" y2="62" className="knowledge-edge" />
            <line x1="218" y1="62" x2="252" y2="34" className="knowledge-edge" />
            <line x1="218" y1="62" x2="306" y2="104" className="knowledge-edge" />
            <line x1="218" y1="62" x2="306" y2="142" className="knowledge-edge" />
            <line x1="120" y1="142" x2="284" y2="186" className="knowledge-edge" />
            <line x1="120" y1="142" x2="228" y2="228" className="knowledge-edge" />
            <line x1="162" y1="126" x2="306" y2="104" className="knowledge-edge" />
            <line x1="162" y1="126" x2="306" y2="142" className="knowledge-edge" />
            <line x1="162" y1="126" x2="284" y2="186" className="knowledge-edge" />
            <line x1="306" y1="142" x2="306" y2="104" className="knowledge-edge" />
            <line x1="284" y1="186" x2="306" y2="142" className="knowledge-edge" />

            <circle cx="306" cy="142" r="22" fill="#fbbf24" className="knowledge-node knowledge-core-node" />
            <circle cx="120" cy="142" r="10" fill="#3b82f6" className="knowledge-node" />
            <circle cx="162" cy="126" r="7" fill="#3b82f6" className="knowledge-node" />
            <circle cx="218" cy="62" r="8" fill="#3b82f6" className="knowledge-node" />
            <circle cx="252" cy="34" r="8" fill="#3b82f6" className="knowledge-node" />
            <circle cx="214" cy="120" r="9" fill="#22d3ee" className="knowledge-node" />
            <circle cx="306" cy="104" r="9" fill="#22d3ee" className="knowledge-node" />
            <circle cx="284" cy="186" r="9" fill="#22d3ee" className="knowledge-node" />
            <circle cx="228" cy="228" r="9" fill="#3b82f6" className="knowledge-node" />
          </g>
        </g>

        <circle cx="96" cy="126" r="4.8" fill="#f8fafc" className="knowledge-star knowledge-star-one" />
        <circle cx="196" cy="156" r="3.6" fill="#f8fafc" className="knowledge-star knowledge-star-two" />
        <circle cx="354" cy="114" r="3.8" fill="#f8fafc" className="knowledge-star knowledge-star-three" />
        <circle cx="352" cy="162" r="3.6" fill="#f8fafc" className="knowledge-star knowledge-star-four" />
      </svg>
    </aside>
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
      <RightGraphicsPanel />
    </main>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
