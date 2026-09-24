# To Do

Ideas and tasks that are agreed on but not scheduled. Open cleanup and the next session's main topic are in
`SESSION_HANDOFF.md`.

## Graph panel: `Embedded` filter button

**Idea (2026-09-25):** a filter button that shows every embedded node (label `Searchable`, 79 today) and the
relationships between them, so all entry points into the graph can be seen in one picture, across all layers.

**Proposal:**

- Button `Embedded` in the graph panel's bottom row, left-aligned next to `Chunks`, same look as the other filter
  buttons.
- Shows the `Searchable` nodes and only the relationships where **both** ends are `Searchable`.

**Things to know before building it:**

- **The picture will have gaps.** Nodes that are not embedded (`Issue`, `Document`, `TeamsMeeting`, `File`,
  `Expertise`, ...) are missing, and so are the relationships through them. For example the five `AUTH-17` versions
  show, but not the `Issue` node that ties them together. That is honest: it shows exactly what the AI can find
  directly.
- **It works differently from the other filters.** Every filter today selects relationship types
  (`GRAPH_SOURCE_RELATIONSHIPS` in `backend/app.py`) and shows the nodes around them. This one selects nodes and shows
  the relationships between them, so it needs its own branch in `load_neo4j_graph`, not just a new list entry.
- The graph panel is sensitive and breaks easily.

**Safe order of work:**

1. Backend first: the new branch in `load_neo4j_graph` (read-only query on `:Searchable`). Restart the backend, check
   that `/api/graph?source=<key>` answers correctly and that every other filter and `Full graph` give the same counts
   as before.
2. The button last (frontend), then ask the user to press F5.
3. Update `docs/GRAPH_DATA_HANDOFF.md` (filter table) and `docs/EMBEDDING_LAYER_HANDOFF.md`.

**Alternative considered, not recommended now:** instead of a filter, mark the embedded nodes in `Full graph` (for
example a thin ring), so the entry points are seen in context. It needs a change in how the graph is drawn, which is
the most fragile part of the panel.
