import { Fragment, useEffect, useState } from "react";

// Microsoft GraphRAG: a second, separate RAG system next to the app's own graph layers. It is built by Microsoft's
// `graphrag` library in its own Python environment (the GraphRAG service), and none of it touches the graph layers or
// the AI agent.

type MicrosoftGraphRagTab = "input" | "index" | "entities" | "communities";

const microsoftGraphRagTabs: Array<{ id: MicrosoftGraphRagTab; label: string; description: string }> = [
  {
    id: "input",
    label: "Input",
    description: "The source texts exported read-only from PostgreSQL as the input documents for GraphRAG.",
  },
  {
    id: "index",
    label: "Index",
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
    <div className="knowledge-panel">
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
      <div className="reference-table-wrapper">
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
        ) : (
          <div className="knowledge-panel">
            <p className="reference-description">{activeTab.description}</p>
            <p className="reference-empty">Not built yet.</p>
          </div>
        )}
      </div>
    </div>
  );
}
