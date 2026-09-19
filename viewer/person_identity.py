"""Person identity resolution for the SQL-to-Neo4j import.

One real person appears in several SQL sources under different fields. Mail and
Slack carry an email address, Slack and Teams participants carry both an email
and a source ID, and issues, documents and pull requests carry only a source ID
and a name. Because of that, identity cannot be resolved one source at a time:
the import first collects observations from every person-bearing field and then
clusters them with union-find before a single `Person` node is written.

Idempotency approach: incremental merging. Canonical `Person` nodes are written
from the clusters, and any pre-existing `Person` node whose key no longer
matches is merged into its canonical node with its relationships moved over
(see `remap_legacy_person_nodes` in `viewer/app.py`). A full rebuild is not
required, and relationships imported from other source groups are preserved.
"""

import re

import psycopg
from psycopg.rows import dict_row


# Source IDs are treated as globally unique across source instances because
# SQL_DATA_HANDOFF.md tells data generators to reuse source IDs consistently.
# Set this to False to scope source IDs per source_instance if that ever breaks.
SOURCE_ID_IS_GLOBAL = True


WHITESPACE = re.compile(r"\s+")


# One entry per person-bearing field group in the SQL schema. The queries are
# read-only and only select the columns needed for identity resolution.
OBSERVATION_QUERIES = [
    ("mail_messages.sender", """
        SELECT DISTINCT sender_address AS email, NULL::text AS source_id,
               sender_name AS name, source_instance
        FROM mail_messages
    """),
    ("mail_messages.recipient", """
        SELECT DISTINCT recipient->>'address' AS email, NULL::text AS source_id,
               recipient->>'name' AS name, source_instance
        FROM mail_messages,
             LATERAL jsonb_array_elements(recipients) AS recipient
        WHERE jsonb_typeof(recipients) = 'array'
    """),
    ("slack_messages.author", """
        SELECT DISTINCT author_email AS email, author_source_id AS source_id,
               author_name AS name, source_instance
        FROM slack_messages
    """),
    ("teams_meetings.participant", """
        SELECT DISTINCT COALESCE(participant->>'email', participant->>'address') AS email,
               COALESCE(participant->>'source_id', participant->>'id') AS source_id,
               participant->>'name' AS name, source_instance
        FROM teams_meetings,
             LATERAL jsonb_array_elements(participants) AS participant
        WHERE jsonb_typeof(participants) = 'array'
    """),
    ("teams_transcript_segments.speaker", """
        SELECT DISTINCT NULL::text AS email, speaker_source_id AS source_id,
               speaker_name AS name, source_instance
        FROM teams_transcript_segments
    """),
    # Issue creators live in `issues`; the per-version fields stay in
    # `issue_versions`. See docs/SQL_DATA_HANDOFF.md, section 4.
    ("issues.creator", """
        SELECT DISTINCT NULL::text AS email, creator_source_id AS source_id,
               creator_name AS name, source_instance
        FROM issues
    """),
    ("issue_versions.assignee", """
        SELECT DISTINCT NULL::text AS email, assignee_source_id AS source_id,
               assignee_name AS name, source_instance
        FROM issue_versions
    """),
    ("issue_versions.changed_by", """
        SELECT DISTINCT NULL::text AS email, changed_by_id AS source_id,
               changed_by_name AS name, source_instance
        FROM issue_versions
    """),
    ("issue_comments.author", """
        SELECT DISTINCT NULL::text AS email, author_source_id AS source_id,
               author_name AS name, source_instance
        FROM issue_comments
    """),
    ("document_versions.author", """
        SELECT DISTINCT NULL::text AS email, author_source_id AS source_id,
               author_name AS name, source_instance
        FROM document_versions
    """),
    ("pr_versions.author", """
        SELECT DISTINCT NULL::text AS email, author_source_id AS source_id,
               author_name AS name, source_instance
        FROM pr_versions
    """),
    ("pr_reviews.author", """
        SELECT DISTINCT NULL::text AS email, author_source_id AS source_id,
               author_name AS name, source_instance
        FROM pr_reviews
    """),
]


def normalise_email(value):
    """Strip and lowercase an email address. Empty values become None."""
    if not value:
        return None
    cleaned = value.strip().lower()
    return cleaned or None


def normalise_source_id(value):
    """Strip a source ID. Case is preserved because source IDs can be case-sensitive."""
    if not value:
        return None
    cleaned = value.strip()
    return cleaned or None


def normalise_name(value):
    """Strip, collapse internal whitespace and lowercase a name for comparison."""
    if not value:
        return None
    cleaned = WHITESPACE.sub(" ", value.strip()).lower()
    return cleaned or None


def display_name(value):
    """Strip and collapse whitespace but keep the original casing for display."""
    if not value:
        return None
    cleaned = WHITESPACE.sub(" ", value.strip())
    return cleaned or None


class Observation:
    """One occurrence of a person in one source field."""

    __slots__ = ("email", "source_id", "name", "display", "origin", "source_instance")

    def __init__(self, email=None, source_id=None, name=None, origin="", source_instance=None):
        self.email = normalise_email(email)
        self.source_id = normalise_source_id(source_id)
        self.name = normalise_name(name)
        self.display = display_name(name)
        self.origin = origin
        self.source_instance = source_instance

    def is_empty(self):
        return not (self.email or self.source_id or self.name)

    def is_name_only(self):
        return bool(self.name) and not self.email and not self.source_id

    def source_id_group(self):
        """Grouping value for rule B, scoped per source instance when needed."""
        if not self.source_id:
            return None
        if SOURCE_ID_IS_GLOBAL:
            return self.source_id
        return (self.source_instance, self.source_id)

    def as_dict(self):
        return {
            "email": self.email,
            "source_id": self.source_id,
            "name": self.display,
            "origin": self.origin,
            "source_instance": self.source_instance,
        }


class UnionFind:
    def __init__(self, size):
        self.parent = list(range(size))

    def find(self, item):
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != root:
            self.parent[item], item = root, self.parent[item]
        return root

    def union(self, left, right):
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return left_root
        # Keep the lowest index as root so clustering stays deterministic.
        if right_root < left_root:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        return left_root


class PersonCluster:
    """One resolved person, built from the observations of a single cluster."""

    def __init__(self, observations, ambiguous=False):
        self.observations = observations
        self.identity_ambiguous = ambiguous
        self.emails = sorted({obs.email for obs in observations if obs.email})
        self.source_ids = sorted({obs.source_id for obs in observations if obs.source_id})
        self.names = sorted({obs.display for obs in observations if obs.display})
        self.normalised_names = {obs.name for obs in observations if obs.name}

    @property
    def person_key(self):
        if self.emails:
            return f"email:{self.emails[0]}"
        if self.source_ids:
            return f"source:{self.source_ids[0]}"
        if self.normalised_names:
            return f"name:{sorted(self.normalised_names)[0]}"
        return None

    @property
    def email(self):
        return self.emails[0] if self.emails else None

    @property
    def source_id(self):
        return self.source_ids[0] if self.source_ids else None

    @property
    def name(self):
        # The longest name is most likely the full name. Ties prefer a name that
        # carries real casing, then fall back to alphabetical order so repeated
        # imports pick the same display name.
        if not self.names:
            return None
        return sorted(
            self.names,
            key=lambda value: (-len(value), value == value.lower(), value),
        )[0]

    @property
    def identity_confidence(self):
        return "strong" if self.emails or self.source_ids else "weak"

    def node_properties(self):
        return {
            "person_key": self.person_key,
            "name": self.name,
            "email": self.email,
            "source_id": self.source_id,
            "emails": self.emails,
            "source_ids": self.source_ids,
            "names": self.names,
            "identity_confidence": self.identity_confidence,
            "identity_ambiguous": self.identity_ambiguous,
        }


class PersonRegistry:
    """Lookup surface built from the clustered observations.

    `resolve_person_key` is the single place where a `person_key` is produced.
    No other code may build a person key by string concatenation.
    """

    def __init__(self, clusters):
        self.clusters = [cluster for cluster in clusters if cluster.person_key]
        self.by_email = {}
        self.by_source_id = {}
        self.by_name = {}
        self.name_only_keys = {}

        for cluster in self.clusters:
            key = cluster.person_key
            for email in cluster.emails:
                self.by_email[email] = key
            for source_id in cluster.source_ids:
                self.by_source_id[source_id] = key
            for name in cluster.normalised_names:
                self.by_name.setdefault(name, set()).add(key)
                if cluster.identity_confidence == "weak":
                    self.name_only_keys[name] = key

    def resolve_person_key(self, email=None, source_id=None, name=None):
        """Return the canonical person key for the fields a source row provides."""
        normalised_email = normalise_email(email)
        if normalised_email and normalised_email in self.by_email:
            return self.by_email[normalised_email]

        normalised_source_id = normalise_source_id(source_id)
        if normalised_source_id and normalised_source_id in self.by_source_id:
            return self.by_source_id[normalised_source_id]

        normalised_name = normalise_name(name)
        if normalised_name:
            if normalised_name in self.name_only_keys:
                return self.name_only_keys[normalised_name]
            keys = self.by_name.get(normalised_name, set())
            if len(keys) == 1:
                return next(iter(keys))

        return None

    def person_nodes(self):
        return [cluster.node_properties() for cluster in self.clusters]

    def ambiguous_clusters(self):
        return [cluster for cluster in self.clusters if cluster.identity_ambiguous]

    def multi_email_clusters(self):
        return [cluster for cluster in self.clusters if len(cluster.emails) > 1]


def cluster_observations(observations):
    """Cluster observations with union-find using rules A-D."""
    observations = [obs for obs in observations if not obs.is_empty()]
    union_find = UnionFind(len(observations))

    # Rule A: same email is the same person.
    first_by_email = {}
    # Rule B: same source_id is the same person.
    first_by_source_id = {}
    # Rule C falls out of A and B sharing one union-find structure: a Slack row
    # carrying both an email and a source_id bridges the two clusters.
    for index, obs in enumerate(observations):
        if obs.email:
            if obs.email in first_by_email:
                union_find.union(first_by_email[obs.email], index)
            else:
                first_by_email[obs.email] = index
        group = obs.source_id_group()
        if group:
            if group in first_by_source_id:
                union_find.union(first_by_source_id[group], index)
            else:
                first_by_source_id[group] = index

    # Names seen in clusters that were established by email or source_id.
    established_roots = set()
    for index, obs in enumerate(observations):
        if obs.email or obs.source_id:
            established_roots.add(union_find.find(index))

    roots_by_name = {}
    for index, obs in enumerate(observations):
        root = union_find.find(index)
        if obs.name and root in established_roots:
            roots_by_name.setdefault(obs.name, set()).add(root)

    # Rule D: a name-only observation attaches to an established cluster only
    # when exactly one cluster carries that name. Name-only observations that
    # share a name form their own cluster; a name never merges two established
    # identities.
    name_only_by_name = {}
    for index, obs in enumerate(observations):
        if obs.is_name_only():
            name_only_by_name.setdefault(obs.name, []).append(index)

    ambiguous_roots = set()
    for name, indexes in name_only_by_name.items():
        group_root = indexes[0]
        for index in indexes[1:]:
            group_root = union_find.union(group_root, index)

        candidates = roots_by_name.get(name, set())
        if len(candidates) == 1:
            union_find.union(next(iter(candidates)), group_root)
        elif len(candidates) > 1:
            ambiguous_roots.add(union_find.find(group_root))

    grouped = {}
    for index, obs in enumerate(observations):
        grouped.setdefault(union_find.find(index), []).append(obs)

    return [
        PersonCluster(members, ambiguous=root in ambiguous_roots)
        for root, members in grouped.items()
    ]


def collect_observations(database_url):
    """Scan every person-bearing field in the SQL schema (phase 1)."""
    observations = []
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            for origin, sql in OBSERVATION_QUERIES:
                cur.execute(sql)
                for row in cur.fetchall():
                    observations.append(Observation(
                        email=row["email"],
                        source_id=row["source_id"],
                        name=row["name"],
                        origin=origin,
                        source_instance=row["source_instance"],
                    ))
    return observations


def build_registry(database_url):
    """Run phases 1-3 and return the registry used by the whole import."""
    return PersonRegistry(cluster_observations(collect_observations(database_url)))
