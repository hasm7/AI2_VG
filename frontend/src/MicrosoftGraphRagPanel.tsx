import { useState } from "react";

// Microsoft GraphRAG: a second, separate RAG system next to the app's own graph layers. It is built by Microsoft's
// `graphrag` library in its own Python environment, and none of it touches the graph layers or the AI agent.

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
        <div className="knowledge-panel">
          <p className="reference-description">{activeTab.description}</p>
          <p className="reference-empty">Not built yet.</p>
        </div>
      </div>
    </div>
  );
}
