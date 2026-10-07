import { Fragment, useEffect, useState } from "react";

// Microsoft GraphRAG: a second, separate RAG system next to the app's own graph layers. It is built by Microsoft's
// `graphrag` library in its own Python environment (the GraphRAG service), and none of it touches the graph layers or
// the AI agent.

type MicrosoftGraphRagTab = "input" | "index" | "entities" | "communities";

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
    label: "Communities & reports",
    description: "The hierarchical Leiden communities and the community report written for each of them.",
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
        ["Chat model (extraction, reports, search)", config.completion_model ?? "-"],
        ["Reasoning effort (chat model)", config.reasoning_effort ?? "model default"],
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
        "This runs Microsoft's graphrag index and calls OpenAI, which costs money (a full build of the current " +
        "data was estimated at about 3-8 USD). Model answers already in the cache are reused for free; only " +
        "what changed is sent to the model again.\n\nChanged since the last build:\n- " +
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

export function MicrosoftGraphRagPanel() {
  const [activeInnerTab, setActiveInnerTab] = useState<MicrosoftGraphRagTab>("input");
  const activeTab = microsoftGraphRagTabs.find((tab) => tab.id === activeInnerTab) ?? microsoftGraphRagTabs[0];

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
        ) : (
          <div className="knowledge-panel graphrag-panel">
            <p className="reference-description">{activeTab.description}</p>
            <p className="reference-empty">Not built yet.</p>
          </div>
        )}
      </div>
    </div>
  );
}
