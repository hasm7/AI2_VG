"""Read-only enforcement for model-generated Cypher.

A code-level guard, not a prompt instruction: a prompt can be talked around,
a regex check that runs before the query ever reaches the driver cannot. It
stays in place permanently, including after a read-only Neo4j role exists —
two layers cost nothing. Used by the agent's explorer (`ai_agent/followup.py`).

The check strips string literals and comments first, then matches write
keywords on word boundaries. Stripping first is what keeps a property named
`created_at` from tripping the `CREATE` check: `\bCREATE\b` does not match
inside `created_at` (no boundary between `create` and the following `d`),
but a string literal containing the word `CREATE` as prose could otherwise
cause a false positive without stripping.
"""

import re

_STRING_LITERAL_PATTERN = re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"")
_LINE_COMMENT_PATTERN = re.compile(r"//[^\n]*")
_BLOCK_COMMENT_PATTERN = re.compile(r"/\*.*?\*/", re.DOTALL)

_WRITE_KEYWORDS = ["CREATE", "MERGE", "DELETE", "DETACH", "SET", "REMOVE", "DROP", "FOREACH"]
_WRITE_KEYWORD_PATTERNS = [
    (keyword, re.compile(rf"\b{keyword}\b", re.IGNORECASE)) for keyword in _WRITE_KEYWORDS
]
_LOAD_CSV_PATTERN = re.compile(r"\bLOAD\s+CSV\b", re.IGNORECASE)
_WRITE_PROCEDURE_PATTERN = re.compile(
    r"\b(db\.create\.\w+|apoc\.create\.\w+|apoc\.merge\.\w+)\b", re.IGNORECASE
)
_LIMIT_PATTERN = re.compile(r"\bLIMIT\s+\d+", re.IGNORECASE)

DEFAULT_LIMIT = 100
QUERY_TIMEOUT_SECONDS = 10.0


class CypherWriteRejected(Exception):
    """Raised when a query is refused because it looks like a write."""


def _strip_literals_and_comments(query: str) -> str:
    stripped = _BLOCK_COMMENT_PATTERN.sub(" ", query)
    stripped = _LINE_COMMENT_PATTERN.sub(" ", stripped)
    stripped = _STRING_LITERAL_PATTERN.sub(" ", stripped)
    return stripped


def find_write_violation(query: str) -> str | None:
    """Returns the offending keyword/procedure, or None if the query reads clean."""
    stripped = _strip_literals_and_comments(query)

    for keyword, pattern in _WRITE_KEYWORD_PATTERNS:
        if pattern.search(stripped):
            return keyword

    if _LOAD_CSV_PATTERN.search(stripped):
        return "LOAD CSV"

    procedure_match = _WRITE_PROCEDURE_PATTERN.search(stripped)
    if procedure_match:
        return procedure_match.group(0)

    return None


def ensure_limit(query: str, default_limit: int = DEFAULT_LIMIT) -> str:
    stripped_for_check = _strip_literals_and_comments(query)
    if _LIMIT_PATTERN.search(stripped_for_check):
        return query
    return f"{query.rstrip().rstrip(';')}\nLIMIT {default_limit}"


def enforce_read_only(query: str, default_limit: int = DEFAULT_LIMIT) -> str:
    """Raises `CypherWriteRejected` on a write; otherwise returns the query with a LIMIT guaranteed."""
    violation = find_write_violation(query)
    if violation:
        raise CypherWriteRejected(
            f"Query rejected: contains write operation '{violation}'. "
            "Only read-only Cypher is permitted. Rewrite as a read query."
        )
    return ensure_limit(query, default_limit)
