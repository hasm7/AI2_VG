# To Do

Ideas and tasks that are agreed on but not scheduled. Open cleanup and the next session's main topic are in
`SESSION_HANDOFF.md`.

No open items.

## Done

- **Graph panel: `Entry points` button (2026-09-25).** First proposed as an `Embedded` filter; renamed because it
  marks entry points in the shown graph instead of filtering. Button in the bottom row, left-aligned next to `Chunks`, same look
  as the other filter buttons. It is a toggle, not a filter: when on, every embedded node (`Searchable`) in the shown
  graph gets a thick, strongly colored ring, so the AI's entry points are seen in context; when off, no rings. This
  replaces the first proposal (a filter showing only `Searchable` nodes), which would have left gaps where
  non-embedded nodes (`Issue`, `Document`, `TeamsMeeting`, `File`, `Expertise`, ...) tie them together. Display only;
  see `docs/GRAPH_DATA_HANDOFF.md`.
