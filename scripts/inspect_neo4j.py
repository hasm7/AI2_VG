import os

from dotenv import load_dotenv
from neo4j import GraphDatabase
from neo4j.exceptions import ClientError


load_dotenv()


def neo4j_config() -> tuple[str, str, str, str]:
    uri = os.getenv("NEO4J_URI", "neo4j://127.0.0.1:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD")
    database = os.getenv("NEO4J_DATABASE", "neo4j")

    if not password:
        raise RuntimeError("NEO4J_PASSWORD must be set in the environment or .env")

    return uri, user, password, database


def print_rows(title: str, rows: list[dict]) -> None:
    print(f"\n## {title}")
    if not rows:
        print("(no rows)")
        return

    columns = list(rows[0].keys())
    print("\t".join(columns))
    for row in rows:
        print("\t".join(str(row.get(column, "")) for column in columns))


def run_read_query(session, query: str) -> list[dict]:
    result = session.execute_read(lambda tx: list(tx.run(query)))
    return [record.data() for record in result]


def main() -> None:
    uri, user, password, database = neo4j_config()

    queries = [
        (
            "Node labels",
            """
            CALL db.labels() YIELD label
            CALL (label) {
                WITH label
                MATCH (n)
                WHERE label IN labels(n)
                RETURN count(n) AS count
            }
            RETURN label, count
            ORDER BY label
            """,
        ),
        (
            "Relationship types",
            """
            CALL db.relationshipTypes() YIELD relationshipType
            CALL (relationshipType) {
                WITH relationshipType
                MATCH ()-[r]->()
                WHERE type(r) = relationshipType
                RETURN count(r) AS count
            }
            RETURN relationshipType, count
            ORDER BY relationshipType
            """,
        ),
        (
            "Constraints",
            """
            SHOW CONSTRAINTS
            YIELD name, type, entityType, labelsOrTypes, properties
            RETURN name, type, entityType, labelsOrTypes, properties
            ORDER BY name
            """,
        ),
        (
            "Indexes",
            """
            SHOW INDEXES
            YIELD name, type, entityType, labelsOrTypes, properties, state
            RETURN name, type, entityType, labelsOrTypes, properties, state
            ORDER BY name
            """,
        ),
        (
            "Sample nodes",
            """
            MATCH (n)
            RETURN labels(n) AS labels, properties(n) AS properties
            LIMIT 10
            """,
        ),
        (
            "Sample relationships",
            """
            MATCH (a)-[r]->(b)
            RETURN labels(a) AS from_labels,
                   type(r) AS relationship,
                   properties(r) AS properties,
                   labels(b) AS to_labels
            LIMIT 10
            """,
        ),
    ]

    with GraphDatabase.driver(uri, auth=(user, password)) as driver:
        driver.verify_connectivity()
        with driver.session(database="system", default_access_mode="READ") as session:
            print_rows(
                "Databases",
                run_read_query(
                    session,
                    """
                    SHOW DATABASES
                    YIELD name, currentStatus, default, home
                    RETURN name, currentStatus, default, home
                    ORDER BY name
                    """,
                ),
            )

        with driver.session(database=database, default_access_mode="READ") as session:
            try:
                for title, query in queries:
                    print_rows(title, run_read_query(session, query))
            except ClientError as error:
                if "DatabaseNotFound" in str(error) or "graph reference not found" in str(error):
                    raise RuntimeError(f"Neo4j database not found: {database}") from error
                raise


if __name__ == "__main__":
    main()
