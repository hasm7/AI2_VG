import { Fragment, useEffect, useState } from "react";

// Microsoft GraphRAG: a second, separate RAG system next to the app's own graph layers. It is built by Microsoft's
// `graphrag` library in its own Python environment (the GraphRAG service), and none of it touches the graph layers or
// the AI agent.

type MicrosoftGraphRagTab = "input" | "index" | "entities" | "communities" | "query";

const microsoftGraphRagTabs: Array<{ id: MicrosoftGraphRagTab; label: string; description: string }> = [
  {
    id: "input",
    label: "Input (Import)",
    description: "The source texts exported read-only from PostgreSQL as the input documents for GraphRAG.",
  },
  {
    id: "index",
    label: "Index (Build)",
    description:
      "Runs the GraphRAG indexing pipeline: text units, entity and relationship extraction, hierarchical Leiden " +
      "communities, community reports and embeddings.",
  },
  {
    id: "entities",
    label: "Entities & relationships",
    description: "The entities and relationships that GraphRAG extracted from the input documents.",
  },
  {
    id: "communities",
    label: "Communities & community reports",
    description: "The hierarchical Leiden communities and the community report written for each of them.",
  },
  {
    id: "query",
    label: "Query (Search)",
    description: "Ask GraphRAG a question with global, local, DRIFT or basic search.",
  },
];

type GraphRagServiceStatus = {
  running: boolean;
  url?: string;
  error?: string;
  graphrag_version?: string;
  python_version?: string;
};

type GraphRagInputDocument = {
  id: string;
  title: string;
  text: string;
  source: string;
  source_label: string;
  source_ids: string[];
  first_at: string;
  last_at: string;
  characters: number;
};

type GraphRagInputState = {
  exported_at: string | null;
  path: string;
  content_hash: string | null;
  // Only in the answer to an export: whether the file was written (false when the data had not changed).
  changed?: boolean;
  counts: Record<string, number>;
  total_characters?: number;
  documents: GraphRagInputDocument[];
  error?: string;
};

type GraphRagConfig = {
  valid: boolean;
  error?: string;
  completion_model?: string;
  reasoning_effort?: string | null;
  // The model each search method uses (global, local, drift, basic).
  search_models?: Record<string, string>;
  embedding_model?: string;
  input?: { type: string; base_dir: string; id_column: string; title_column: string; text_column: string };
  chunking?: { type: string; size: number; overlap: number; encoding_model: string; prepend_metadata: string[] | null };
  extract_graph?: { entity_types: string[]; max_gleanings: number };
  max_cluster_size?: number;
  extract_claims?: boolean;
  community_report_max_length?: number;
  vector_store?: string;
};

type GraphRagIndexState = {
  error?: string;
  workflows: string[];
  // Why the index needs a build; empty (and up_to_date) when nothing has changed since the last build.
  changes: string[];
  up_to_date: boolean;
  index: {
    built_at: string;
    built_by_app: boolean;
    total_runtime_seconds: number;
    workflow_seconds: Record<string, number>;
    counts: {
      documents?: number;
      text_units?: number;
      entities?: number;
      relationships?: number;
      communities?: number;
      community_reports?: number;
      communities_per_level?: Record<string, number>;
    };
  } | null;
  run: {
    running: boolean;
    started_at: string;
    finished_at?: string;
    exit_code?: number;
    error?: string;
    completed_workflows: string[];
    current_workflow: string | null;
    errors: string[];
  } | null;
};

const indexWorkflowLabels: Record<string, string> = {
  load_input_documents: "Load input documents",
  create_base_text_units: "Split into text units",
  create_final_documents: "Link documents to text units",
  extract_graph: "Extract entities and relationships (model)",
  finalize_graph: "Finalize the graph",
  extract_covariates: "Extract claims (off)",
  create_communities: "Hierarchical Leiden communities",
  create_final_text_units: "Link text units to the graph",
  create_community_reports: "Community reports (model)",
  generate_text_embeddings: "Embeddings (model)",
};

const INDEX_POLL_MS = 3000;

const inputSourceLabels: Record<string, string> = {
  mail: "Mail threads",
  slack: "Slack threads",
  teams: "Teams meetings",
  issues: "Issues",
  documents: "Documents",
  pull_requests: "Pull requests",
};

function formatTime(value: string | null) {
  if (!value) {
    return "never";
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function formatDate(value: string) {
  return value ? value.slice(0, 10) : "-";
}

function GraphRagServiceLine() {
  const [status, setStatus] = useState<GraphRagServiceStatus | null>(null);

  useEffect(() => {
    fetch("/api/graphrag/status")
      .then((response) => response.json() as Promise<GraphRagServiceStatus>)
      .then(setStatus)
      .catch(() => setStatus({ running: false, error: "Backend is not reachable." }));
  }, []);

  if (!status) {
    return <p className="reference-description graphrag-service-line">GraphRAG service: checking...</p>;
  }
  return status.running ? (
    <p className="reference-description graphrag-service-line">
      GraphRAG service: running (graphrag {status.graphrag_version}, Python {status.python_version})
    </p>
  ) : (
    <p className="reference-error graphrag-service-line">
      GraphRAG service: not running. Start it with <code>.\scripts\run_graphrag_service.ps1</code>.
    </p>
  );
}

function GraphRagInputPanel() {
  const [state, setState] = useState<GraphRagInputState | null>(null);
  const [isExporting, setIsExporting] = useState(false);
  const [error, setError] = useState("");
  const [justExported, setJustExported] = useState(false);
  const [openDocumentId, setOpenDocumentId] = useState<string | null>(null);

  const loadState = async () => {
    try {
      const response = await fetch("/api/graphrag/input");
      const data = (await response.json()) as GraphRagInputState;
      if (!response.ok || data.error) {
        throw new Error(data.error || "Could not read the GraphRAG input.");
      }
      setState(data);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Could not read the GraphRAG input.");
    }
  };

  useEffect(() => {
    void loadState();
  }, []);

  const runExport = async () => {
    setError("");
    setIsExporting(true);
    setJustExported(false);

    try {
      const response = await fetch("/api/graphrag/input/export", { method: "POST" });
      const data = (await response.json()) as GraphRagInputState;
      if (!response.ok || data.error) {
        throw new Error(data.error || "Export failed.");
      }
      setState(data);
      setJustExported(true);
    } catch (exportError) {
      setError(exportError instanceof Error ? exportError.message : "Export failed.");
    } finally {
      setIsExporting(false);
    }
  };

  const documents = state?.documents ?? [];

  return (
    <div className="knowledge-panel graphrag-panel">
      <div className="reference-actions">
        <button className="knowledge-build-button" type="button" disabled={isExporting} onClick={runExport}>
          {isExporting ? "Exporting input documents..." : "Export input documents"}
        </button>
        <div className="reference-status">
          <span>Last export: {formatTime(state?.exported_at ?? null)}</span>
          {state ? <span>File: {state.path}</span> : null}
        </div>
      </div>

      <p className="reference-description">
        Reads the six sources from PostgreSQL (read only) and writes one document per mail thread, Slack thread, Teams
        meeting, issue, document and pull request, interpreted as the graph import interprets them. A parent and its
        children (a meeting and its segments, an issue and its versions and comments) become one document, with
        authors, times and identifiers written into the text. The file is written only when the data has changed.
        Nothing is sent to a model.
      </p>

      {error ? <p className="reference-error">{error}</p> : null}
      {justExported && !error ? (
        <p className="reference-success">
          {state?.changed
            ? `Done. ${documents.length} documents exported.`
            : "No changes since the last export. The file was not written."}
        </p>
      ) : null}

      {state?.exported_at ? (
        <>
          <p className="reference-description reference-counts-heading">Documents:</p>
          <div className="reference-counts">
            {Object.keys(inputSourceLabels).map((source) => (
              <span key={source} className="reference-count">
                {inputSourceLabels[source]}: <strong>{state.counts[source] ?? 0}</strong>
              </span>
            ))}
            <span className="reference-count">
              Total: <strong>{documents.length}</strong>
            </span>
            <span className="reference-count">
              Characters: <strong>{(state.total_characters ?? 0).toLocaleString()}</strong>
            </span>
          </div>
        </>
      ) : null}

      <h4 className="knowledge-card-title knowledge-section-title">
        Input documents <span className="knowledge-section-kind">(click a row to show the text GraphRAG reads)</span>
      </h4>
      <div className="reference-table-wrapper layer-last-table">
        {documents.length === 0 ? (
          <p className="reference-empty">No input documents yet. Press the button to export them.</p>
        ) : (
          <table className="reference-table">
            <thead>
              <tr>
                <th>Source</th>
                <th>Title</th>
                <th>Records</th>
                <th>Period</th>
                <th>Characters</th>
              </tr>
            </thead>
            <tbody>
              {documents.map((doc) => (
                <Fragment key={doc.id}>
                  <tr
                    style={{ cursor: "pointer" }}
                    onClick={() => setOpenDocumentId(openDocumentId === doc.id ? null : doc.id)}
                  >
                    <td>{doc.source_label}</td>
                    <td>{doc.title}</td>
                    <td className="reference-cell-wrap">{doc.source_ids.join(", ")}</td>
                    <td>
                      {formatDate(doc.first_at)}
                      {doc.last_at && doc.last_at.slice(0, 10) !== doc.first_at.slice(0, 10)
                        ? ` - ${formatDate(doc.last_at)}`
                        : ""}
                    </td>
                    <td>{doc.characters.toLocaleString()}</td>
                  </tr>
                  {openDocumentId === doc.id ? (
                    <tr>
                      <td colSpan={5}>
                        <pre style={{ whiteSpace: "pre-wrap", margin: 0, fontSize: 11 }}>{doc.text}</pre>
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function GraphRagIndexPanel() {
  const [config, setConfig] = useState<GraphRagConfig | null>(null);

  useEffect(() => {
    fetch("/api/graphrag/config")
      .then((response) => response.json() as Promise<GraphRagConfig>)
      .then(setConfig)
      .catch(() => setConfig({ valid: false, error: "Backend is not reachable." }));
  }, []);

  const rows: Array<[string, string]> = config?.valid
    ? [
        ["Indexing model (extraction, summaries, community reports)", config.completion_model ?? "-"],
        ["Reasoning effort (indexing model)", config.reasoning_effort ?? "model default"],
        [
          "Search model (Query tab)",
          [...new Set(Object.values(config.search_models ?? {}))].join(", ") || "-",
        ],
        ["Embedding model", config.embedding_model ?? "-"],
        [
          "Input",
          `${config.input?.type} (columns: id ${config.input?.id_column}, title ${config.input?.title_column}, ` +
            `text ${config.input?.text_column})`,
        ],
        [
          "Text units (chunking)",
          `${config.chunking?.size} tokens, overlap ${config.chunking?.overlap}, ${config.chunking?.encoding_model}` +
            (config.chunking?.prepend_metadata?.length
              ? `, prepends ${config.chunking.prepend_metadata.join(", ")}`
              : ""),
        ],
        ["Entity types", config.extract_graph?.entity_types.join(", ") ?? "-"],
        ["Extra extraction passes (gleanings)", String(config.extract_graph?.max_gleanings ?? "-")],
        ["Claims extraction", config.extract_claims ? "on" : "off"],
        ["Leiden: max community size", String(config.max_cluster_size ?? "-")],
        ["Community report: max length", `${config.community_report_max_length ?? "-"} words`],
        ["Vector store", config.vector_store ?? "-"],
      ]
    : [];

  return (
    <div className="knowledge-panel graphrag-panel">
      <h4 className="knowledge-card-title">Graph → Communities → Community reports → Embeddings</h4>
      <ul className="reference-description">
        <li>
          <strong>Graph:</strong> the model reads every text unit and extracts the entities in it and the relationships
          between them.
        </li>
        <li>
          <strong>Communities:</strong> the Leiden algorithm groups entities that have many relationships with each other
          and few with the rest of the graph. A community with more than 10 entities is split again, which gives levels
          from broad to detailed.
        </li>
        <li>
          <strong>Community reports:</strong> the model writes a summary of each community.
        </li>
        <li>
          <strong>Embeddings:</strong> the entities, community reports and text units are turned into vectors, so that a
          question can find them by meaning.
        </li>
      </ul>

      <h4 className="knowledge-card-title knowledge-section-title graphrag-index-title">
        Configuration <span className="knowledge-section-kind">(settings.yaml)</span>
      </h4>
      {!config ? (
        <p className="reference-empty">Loading the configuration...</p>
      ) : !config.valid ? (
        <p className="reference-error">Configuration not usable: {config.error}</p>
      ) : (
        <div className="reference-table-wrapper">
          <table className="reference-table">
            <tbody>
              {rows.map(([label, value]) => (
                <tr key={label}>
                  <td>{label}</td>
                  <td className="reference-cell-wrap">{value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <GraphRagIndexBuild />
    </div>
  );
}

function GraphRagIndexBuild() {
  const [state, setState] = useState<GraphRagIndexState | null>(null);
  const [error, setError] = useState("");
  const [isStarting, setIsStarting] = useState(false);

  const loadState = async () => {
    try {
      const response = await fetch("/api/graphrag/index");
      const data = (await response.json()) as GraphRagIndexState;
      if (!response.ok || data.error) {
        throw new Error(data.error || "Could not read the GraphRAG index.");
      }
      setState(data);
      setError("");
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Could not read the GraphRAG index.");
    }
  };

  useEffect(() => {
    void loadState();
  }, []);

  const isRunning = Boolean(state?.run?.running);

  // While a build runs, its progress is read again every few seconds.
  useEffect(() => {
    if (!isRunning) {
      return;
    }
    const timer = window.setInterval(() => void loadState(), INDEX_POLL_MS);
    return () => window.clearInterval(timer);
  }, [isRunning]);

  const startBuild = async () => {
    const confirmed = window.confirm(
      "Build the GraphRAG index?\n\n" +
        "This runs Microsoft's graphrag index and calls OpenAI, which costs money: the full build of the current " +
        "data cost 8.85 USD (measured, with gpt-5.6-terra). Model answers already in the cache are reused for " +
        "free; only what changed is sent to the model again.\n\nChanged since the last build:\n- " +
        changes.join("\n- "),
    );
    if (!confirmed) {
      return;
    }
    setError("");
    setIsStarting(true);
    try {
      const response = await fetch("/api/graphrag/index", { method: "POST" });
      const data = (await response.json()) as GraphRagIndexState;
      if (!response.ok || data.error) {
        throw new Error(data.error || "The build could not be started.");
      }
      setState(data);
    } catch (startError) {
      setError(startError instanceof Error ? startError.message : "The build could not be started.");
    } finally {
      setIsStarting(false);
    }
  };

  const index = state?.index;
  const run = state?.run;
  const counts = index?.counts;
  const levels = counts?.communities_per_level ?? {};
  // Nothing has changed since the last build: building again is refused (by the service too).
  const isUpToDate = Boolean(state?.up_to_date);
  const changes = state?.changes ?? [];

  return (
    <>
      <h4 className="knowledge-card-title knowledge-section-title graphrag-index-title">Index</h4>
      <div className="reference-actions">
        <button
          className={`knowledge-build-button${changes.length > 0 ? " knowledge-build-button-stale" : ""}`}
          type="button"
          disabled={isRunning || isStarting || !state || isUpToDate}
          onClick={startBuild}
        >
          {isRunning || isStarting ? "Building index..." : isUpToDate ? "Index is up to date" : "Build index"}
        </button>
        <div className="reference-status">
          <span>Last build: {formatTime(index?.built_at ?? null)}</span>
          {index ? (
            <span>
              Took {Math.round(index.total_runtime_seconds / 60)} min
              {index.built_by_app ? "" : " (built outside the app, from the command line)"}
            </span>
          ) : null}
          {isUpToDate ? (
            <span>Nothing has changed since the last build (input, settings.yaml and prompts).</span>
          ) : changes.length > 0 ? (
            <span className="reference-stale">
              The index needs a build:
              <ul>
                {changes.map((change) => (
                  <li key={change}>{change}</li>
                ))}
              </ul>
            </span>
          ) : null}
        </div>
      </div>

      {error ? <p className="reference-error">{error}</p> : null}

      {run ? (
        <>
          <p className="reference-description reference-counts-heading">
            {run.running
              ? `Building (started ${formatTime(run.started_at)}):`
              : run.error
                ? `The last build failed (${formatTime(run.finished_at ?? null)}):`
                : `The last build finished (${formatTime(run.finished_at ?? null)}):`}
          </p>
          {run.error ? <p className="reference-error">{run.error}</p> : null}
          {run.errors.map((line, index) => (
            <p key={index} className="reference-error">
              {line}
            </p>
          ))}
          <ul className="reference-description">
            {(state?.workflows ?? []).map((name) => (
              <li key={name}>
                {run.completed_workflows.includes(name) ? "Done: " : run.current_workflow === name ? "Running: " : "Waiting: "}
                {indexWorkflowLabels[name] ?? name}
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {counts ? (
        <>
          <p className="reference-description reference-counts-heading">Created by the last build:</p>
          <div className="reference-counts">
            <span className="reference-count">Documents: <strong>{counts.documents ?? 0}</strong></span>
            <span className="reference-count">Text units: <strong>{counts.text_units ?? 0}</strong></span>
            <span className="reference-count">Entities: <strong>{counts.entities ?? 0}</strong></span>
            <span className="reference-count">Relationships: <strong>{counts.relationships ?? 0}</strong></span>
            <span className="reference-count">Communities: <strong>{counts.communities ?? 0}</strong></span>
            <span className="reference-count">Community reports: <strong>{counts.community_reports ?? 0}</strong></span>
          </div>
          <p className="reference-description reference-counts-heading">
            Communities per level (each level splits the communities above it into smaller ones):
          </p>
          <ul className="reference-description">
            {Object.entries(levels).map(([level, count], position, all) => (
              <li key={level}>
                Level {level}: {count}
                {position === 0 || position === all.length - 1 ? (
                  <span style={{ marginLeft: 16 }}>{position === 0 ? "(top, broadest)" : "(most detailed)"}</span>
                ) : null}
              </li>
            ))}
          </ul>
        </>
      ) : !error ? (
        <p className="reference-empty">No index yet. Press the button to build it.</p>
      ) : null}

      {index ? (
        <div className="reference-table-wrapper layer-last-table">
          <table className="reference-table">
            <thead>
              <tr>
                <th>Step (workflow)</th>
                <th>Seconds in the last build</th>
              </tr>
            </thead>
            <tbody>
              {(state?.workflows ?? []).map((name) => (
                <tr key={name}>
                  <td>{indexWorkflowLabels[name] ?? name}</td>
                  <td>{index.workflow_seconds[name] ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </>
  );
}

type GraphRagEntity = { title: string; type: string; description: string; degree: number; frequency: number };
type GraphRagRelationship = { source: string; target: string; description: string; weight: number };
type GraphRagCommunity = {
  community: number;
  level: number;
  parent: number;
  children: number[];
  size: number;
  title: string | null;
  rating: number | null;
  // The community's most connected entities, shown under its title.
  top_entities: Array<{ title: string; type: string }>;
};
type GraphRagCommunityReport = {
  community: number;
  level: number;
  title: string;
  summary: string;
  rating: number;
  rating_explanation: string;
  findings: Array<{ summary: string; explanation: string }>;
  entities: Array<{ title: string; type: string; degree: number }>;
  error?: string;
};

// Long tables are shown in pages of this many rows.
const GRAPHRAG_PAGE_ROWS = 50;

// A long text is shown shortened; a click shows all of it, and another click shortens it again.
const SHORT_TEXT_CHARS = 160;

function ExpandableText({ text }: { text: string }) {
  const [isOpen, setIsOpen] = useState(false);
  if (text.length <= SHORT_TEXT_CHARS) {
    return <>{text}</>;
  }
  return (
    <span className="graphrag-expandable" onClick={() => setIsOpen(!isOpen)} title={isOpen ? "Show less" : "Show all"}>
      {isOpen ? text : `${text.slice(0, SHORT_TEXT_CHARS).trimEnd()}... `}
      <span className="graphrag-expandable-toggle">{isOpen ? " show less" : "show all"}</span>
    </span>
  );
}

function matchesSearch(search: string, ...fields: string[]) {
  const needle = search.trim().toLowerCase();
  return !needle || fields.some((field) => field.toLowerCase().includes(needle));
}

function GraphRagEntitiesPanel() {
  const [data, setData] = useState<{ entities: GraphRagEntity[]; relationships: GraphRagRelationship[] } | null>(null);
  const [error, setError] = useState("");
  const [entitySearch, setEntitySearch] = useState("");
  const [entityType, setEntityType] = useState("");
  const [entityRows, setEntityRows] = useState(GRAPHRAG_PAGE_ROWS);
  const [relationshipSearch, setRelationshipSearch] = useState("");
  const [relationshipRows, setRelationshipRows] = useState(GRAPHRAG_PAGE_ROWS);

  useEffect(() => {
    fetch("/api/graphrag/entities")
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok || payload.error) {
          throw new Error(payload.error || "Could not read the entities.");
        }
        setData(payload);
      })
      .catch((loadError) => setError(loadError instanceof Error ? loadError.message : "Could not read the entities."));
  }, []);

  const entities = data?.entities ?? [];
  const relationships = data?.relationships ?? [];
  const types = [...new Set(entities.map((entity) => entity.type))].sort();
  const shownEntities = entities.filter(
    (entity) =>
      (!entityType || entity.type === entityType) && matchesSearch(entitySearch, entity.title, entity.description),
  );
  const shownRelationships = relationships.filter((relationship) =>
    matchesSearch(relationshipSearch, relationship.source, relationship.target, relationship.description),
  );

  return (
    <div className="knowledge-panel graphrag-panel">
      <p className="reference-description">
        The graph the index built: every entity (node) the model extracted from the text units, and the relationships
        between them. An entity's relationships are its number of connections in the graph.
      </p>
      {error ? <p className="reference-error">{error}</p> : null}
      {!data && !error ? <p className="reference-empty">Loading the graph...</p> : null}

      {data ? (
        <>
          <h4 className="knowledge-card-title knowledge-section-title">
            Entities (node) <span className="knowledge-section-kind">({entities.length}, most connected first)</span>
          </h4>
          <div className="embedding-table-controls">
            <label>
              Search
              <input
                type="search"
                value={entitySearch}
                onChange={(event) => {
                  setEntitySearch(event.target.value);
                  setEntityRows(GRAPHRAG_PAGE_ROWS);
                }}
              />
            </label>
            <label>
              Type
              <select
                value={entityType}
                onChange={(event) => {
                  setEntityType(event.target.value);
                  setEntityRows(GRAPHRAG_PAGE_ROWS);
                }}
              >
                <option value="">All</option>
                {types.map((type) => (
                  <option key={type} value={type}>
                    {type} ({entities.filter((entity) => entity.type === type).length})
                  </option>
                ))}
              </select>
            </label>
            <span>{shownEntities.length} shown</span>
          </div>
          <div className="reference-table-wrapper">
            <table className="reference-table">
              <thead>
                <tr>
                  <th>Entity (node)</th>
                  <th>Type</th>
                  <th>Relationships</th>
                  <th>Description</th>
                </tr>
              </thead>
              <tbody>
                {shownEntities.slice(0, entityRows).map((entity) => (
                  <tr key={entity.title}>
                    <td>{entity.title}</td>
                    <td>{entity.type}</td>
                    <td>{entity.degree}</td>
                    <td>
                      <ExpandableText text={entity.description} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {shownEntities.length > entityRows ? (
            <button className="knowledge-build-button" type="button" onClick={() => setEntityRows(entityRows + GRAPHRAG_PAGE_ROWS)}>
              Show {Math.min(GRAPHRAG_PAGE_ROWS, shownEntities.length - entityRows)} more
            </button>
          ) : null}

          <h4 className="knowledge-card-title knowledge-section-title graphrag-index-title">
            Relationships <span className="knowledge-section-kind">({relationships.length}, strongest first)</span>
          </h4>
          <div className="embedding-table-controls">
            <label>
              Search
              <input
                type="search"
                value={relationshipSearch}
                onChange={(event) => {
                  setRelationshipSearch(event.target.value);
                  setRelationshipRows(GRAPHRAG_PAGE_ROWS);
                }}
              />
            </label>
            <span>{shownRelationships.length} shown</span>
          </div>
          <div className="reference-table-wrapper layer-last-table">
            <table className="reference-table">
              <thead>
                <tr>
                  <th>Source (start node)</th>
                  <th>Target (end node)</th>
                  <th>Weight</th>
                  <th>Description</th>
                </tr>
              </thead>
              <tbody>
                {shownRelationships.slice(0, relationshipRows).map((relationship, index) => (
                  <tr key={`${relationship.source}-${relationship.target}-${index}`}>
                    <td>{relationship.source}</td>
                    <td>{relationship.target}</td>
                    <td>{relationship.weight}</td>
                    <td>
                      <ExpandableText text={relationship.description} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {shownRelationships.length > relationshipRows ? (
            <button
              className="knowledge-build-button layer-last-table"
              type="button"
              onClick={() => setRelationshipRows(relationshipRows + GRAPHRAG_PAGE_ROWS)}
            >
              Show {Math.min(GRAPHRAG_PAGE_ROWS, shownRelationships.length - relationshipRows)} more
            </button>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

function GraphRagCommunityNode({
  community,
  byId,
  depth,
}: {
  community: GraphRagCommunity;
  byId: Map<number, GraphRagCommunity>;
  depth: number;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [report, setReport] = useState<GraphRagCommunityReport | null>(null);
  const [error, setError] = useState("");

  const toggle = () => {
    const opening = !isOpen;
    setIsOpen(opening);
    if (opening && !report) {
      fetch(`/api/graphrag/communities/${community.community}`)
        .then(async (response) => {
          const payload = (await response.json()) as GraphRagCommunityReport;
          if (!response.ok || payload.error) {
            throw new Error(payload.error || "Could not read the report.");
          }
          setReport(payload);
        })
        .catch((loadError) => setError(loadError instanceof Error ? loadError.message : "Could not read the report."));
    }
  };

  const children = community.children
    .map((child) => byId.get(child))
    .filter((child): child is GraphRagCommunity => Boolean(child))
    .sort((a, b) => (b.rating ?? 0) - (a.rating ?? 0));

  return (
    <li className="graphrag-community" style={{ marginLeft: depth * 18 }}>
      <button className="graphrag-community-row" type="button" onClick={toggle}>
        <span className="graphrag-community-arrow">{isOpen ? "▾" : "▸"}</span>
        <span className="graphrag-community-text">
          <span className="graphrag-community-title">{community.title ?? `Community ${community.community}`}</span>
          <span className="graphrag-community-entities">
            {community.top_entities.map((entity) => `${entity.title} (${entity.type.toLowerCase()})`).join(" · ")}
          </span>
        </span>
        <span className="graphrag-community-meta">
          level {community.level} · {community.size} entities · rating {community.rating ?? "-"}
          {children.length > 0 ? ` · ${children.length} sub-communities` : ""}
        </span>
      </button>
      {isOpen ? (
        <div className="graphrag-community-report">
          {error ? <p className="reference-error">{error}</p> : null}
          {!report && !error ? <p className="reference-empty">Loading the report...</p> : null}
          {report ? (
            <>
              <p className="reference-description">{report.summary}</p>
              <p className="reference-description">
                <strong>Rating {report.rating}:</strong> {report.rating_explanation}
              </p>
              <p className="reference-description reference-counts-heading">Findings:</p>
              <ul className="reference-description">
                {report.findings.map((finding, index) => (
                  <li key={index}>
                    <strong>{finding.summary}.</strong> {finding.explanation}
                  </li>
                ))}
              </ul>
              <p className="reference-description">
                <strong>Entities:</strong> {report.entities.map((entity) => entity.title).join(", ")}
              </p>
            </>
          ) : null}
          {children.length > 0 ? (
            <ul className="graphrag-community-list">
              {children.map((child) => (
                <GraphRagCommunityNode key={child.community} community={child} byId={byId} depth={1} />
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function GraphRagCommunitiesPanel() {
  const [communities, setCommunities] = useState<GraphRagCommunity[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch("/api/graphrag/communities")
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok || payload.error) {
          throw new Error(payload.error || "Could not read the communities.");
        }
        setCommunities(payload.communities);
      })
      .catch((loadError) =>
        setError(loadError instanceof Error ? loadError.message : "Could not read the communities."),
      );
  }, []);

  const byId = new Map((communities ?? []).map((community) => [community.community, community]));
  const topLevel = (communities ?? [])
    .filter((community) => community.level === 0)
    .sort((a, b) => (b.rating ?? 0) - (a.rating ?? 0));

  return (
    <div className="knowledge-panel graphrag-panel">
      <p className="reference-description">
        Communities found by the Leiden algorithm, broadest first (level 0). Click one to read its community report and
        open its sub-communities.
        <br />
        <em>Entities</em> = the nodes it holds · <em>Rating</em> = importance 0–10, judged by the model ·{" "}
        <em>Sub-communities</em> = the smaller communities it was split into.
      </p>
      {error ? <p className="reference-error">{error}</p> : null}
      {!communities && !error ? <p className="reference-empty">Loading the communities...</p> : null}
      {communities ? (
        <ul className="graphrag-community-list layer-last-table">
          {topLevel.map((community) => (
            <GraphRagCommunityNode key={community.community} community={community} byId={byId} depth={0} />
          ))}
        </ul>
      ) : null}
    </div>
  );
}

type GraphRagQueryMethod = "global" | "local" | "drift" | "basic";

type GraphRagContextTable = { total_rows: number; columns: string[]; rows: Array<Record<string, unknown>> };

type GraphRagQueryResult = {
  error?: string;
  method: GraphRagQueryMethod;
  question: string;
  answer: string;
  seconds: number;
  community_level: number | null;
  response_type: string;
  context: Record<string, GraphRagContextTable>;
  // Every model and embedding call made for this question, priced with litellm's price list.
  usage: {
    model_calls: number;
    prompt_tokens: number;
    completion_tokens: number;
    reasoning_tokens: number;
    embedding_calls: number;
    embedding_tokens: number;
    cost_usd: number;
    complete: boolean;
  };
};

const graphRagQueryMethods: Array<{ id: GraphRagQueryMethod; label: string; description: string; cost: string }> = [
  {
    id: "global",
    label: "Global",
    description: "Reads the community reports to answer questions about the whole dataset (themes, main problems).",
    cost: "many model calls, about 0.03 USD",
  },
  {
    id: "local",
    label: "Local",
    description:
      "Finds the entities the question is about and answers from their relationships, community reports and text units.",
    cost: "one model call, under 0.01 USD",
  },
  {
    id: "drift",
    label: "DRIFT",
    description: "Starts from the community reports like global search, then follows up with local searches.",
    cost: "2 starting calls and up to 3 follow-ups, about 0.01-0.03 USD",
  },
  {
    id: "basic",
    label: "Basic",
    description: "Plain vector RAG without the graph: answers from the most similar text units.",
    cost: "one model call, under 0.01 USD",
  },
];

function GraphRagQueryPanel() {
  const [question, setQuestion] = useState("");
  const [method, setMethod] = useState<GraphRagQueryMethod>("local");
  const [result, setResult] = useState<GraphRagQueryResult | null>(null);
  const [error, setError] = useState("");
  const [isAsking, setIsAsking] = useState(false);
  const chosen = graphRagQueryMethods.find((entry) => entry.id === method) ?? graphRagQueryMethods[0];

  const ask = async () => {
    if (!question.trim()) {
      return;
    }
    setError("");
    setIsAsking(true);
    try {
      const response = await fetch("/api/graphrag/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ method, question }),
      });
      const data = (await response.json()) as GraphRagQueryResult;
      if (!response.ok || data.error) {
        throw new Error(data.error || "The question could not be answered.");
      }
      setResult(data);
    } catch (askError) {
      setError(askError instanceof Error ? askError.message : "The question could not be answered.");
    } finally {
      setIsAsking(false);
    }
  };

  return (
    <div className="knowledge-panel graphrag-panel">
      <p className="reference-description">
        Ask a question and choose how GraphRAG searches the index. It runs Microsoft's own search, with the same
        defaults as <code>graphrag query</code> (community level 2, an answer in multiple paragraphs). The searches use
        the cheap model <code>gpt-6-luna</code>; the costs below are estimates, the real cost is shown under each
        answer. A question is stopped before it can cost more than <strong>0.10 USD</strong>.
      </p>

      <div className="graphrag-query-methods" role="radiogroup" aria-label="Search method">
        {graphRagQueryMethods.map((entry) => (
          <label key={entry.id} className="graphrag-query-method">
            <input
              type="radio"
              name="graphrag-method"
              value={entry.id}
              checked={method === entry.id}
              disabled={isAsking}
              onChange={() => setMethod(entry.id)}
            />
            <span>
              <strong>{entry.label}:</strong> {entry.description} <em>({entry.cost})</em>
            </span>
          </label>
        ))}
      </div>

      <textarea
        className="graphrag-query-input"
        rows={3}
        placeholder="For example: Why was the same expense paid twice?"
        value={question}
        disabled={isAsking}
        onChange={(event) => setQuestion(event.target.value)}
      />
      <div className="reference-actions">
        <button
          className="knowledge-build-button"
          type="button"
          disabled={isAsking || !question.trim()}
          onClick={ask}
        >
          {isAsking ? `Searching (${chosen.label})...` : "Ask"}
        </button>
        {isAsking ? (
          <span className="reference-description">Global and DRIFT search can take a minute or two.</span>
        ) : null}
      </div>

      {error ? <p className="reference-error">{error}</p> : null}

      {result ? (
        <>
          <h4 className="knowledge-card-title knowledge-section-title graphrag-index-title">
            Answer{" "}
            <span className="knowledge-section-kind">
              ({graphRagQueryMethods.find((entry) => entry.id === result.method)?.label} search, {result.seconds} s)
            </span>
          </h4>
          <p className="reference-description graphrag-query-question">
            <strong>Question:</strong> {result.question}
          </p>
          <div className="graphrag-query-answer">{result.answer}</div>
          {result.usage ? (
            <p className="reference-description">
              <strong>Cost:</strong> {result.usage.cost_usd.toFixed(4)} USD
              {result.usage.complete ? "" : " (incomplete: some calls had no price or token count)"} ·{" "}
              {result.usage.model_calls} model call{result.usage.model_calls === 1 ? "" : "s"} ·{" "}
              {result.usage.prompt_tokens.toLocaleString()} tokens in · {result.usage.completion_tokens.toLocaleString()}{" "}
              tokens out (of which {result.usage.reasoning_tokens.toLocaleString()} reasoning) ·{" "}
              {result.usage.embedding_calls} embedding call{result.usage.embedding_calls === 1 ? "" : "s"}
            </p>
          ) : null}

          <h4 className="knowledge-card-title knowledge-section-title graphrag-index-title">
            What GraphRAG used <span className="knowledge-section-kind">(the context given to the model)</span>
          </h4>
          {Object.keys(result.context).length === 0 ? (
            <p className="reference-empty">No context tables were returned for this search.</p>
          ) : (
            Object.entries(result.context).map(([name, table], index, all) => (
              <div key={name}>
                <p className="reference-description reference-counts-heading">
                  {name} ({table.total_rows}
                  {table.total_rows > table.rows.length ? `, first ${table.rows.length} shown` : ""}):
                </p>
                <div className={`reference-table-wrapper${index === all.length - 1 ? " layer-last-table" : ""}`}>
                  <table className="reference-table">
                    <thead>
                      <tr>
                        {table.columns.map((column) => (
                          <th key={column}>{column}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {table.rows.map((row, rowIndex) => (
                        <tr key={rowIndex}>
                          {table.columns.map((column) => (
                            <td key={column}>
                              <ExpandableText text={row[column] == null ? "" : String(row[column])} />
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ))
          )}
        </>
      ) : null}
    </div>
  );
}

export function MicrosoftGraphRagPanel() {
  const [activeInnerTab, setActiveInnerTab] = useState<MicrosoftGraphRagTab>("input");

  return (
    <div className="build-graph-layers">
      <div className="center-tabs center-tabs-inner" role="tablist" aria-label="Microsoft GraphRAG tabs">
        {microsoftGraphRagTabs.map((tab) => (
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
        <GraphRagServiceLine />
        {activeInnerTab === "input" ? (
          <GraphRagInputPanel />
        ) : activeInnerTab === "index" ? (
          <GraphRagIndexPanel />
        ) : activeInnerTab === "entities" ? (
          <GraphRagEntitiesPanel />
        ) : activeInnerTab === "communities" ? (
          <GraphRagCommunitiesPanel />
        ) : (
          <GraphRagQueryPanel />
        )}
      </div>
    </div>
  );
}
