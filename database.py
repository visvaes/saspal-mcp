import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


DATABASE_PATH = Path(__file__).resolve().with_name("saspal.db")

_COMPANY_INFO_RECORDS: tuple[tuple[str, str, str], ...] = (
    (
        "company",
        "Company name",
        "SASPAL Technologies",
    ),
    (
        "company",
        "Positioning",
        "SASPAL Technologies is positioned as a software engineering and digital product partner "
        "working on modern engineering, automation, and AI-enabled experiences.",
    ),
    (
        "company",
        "What the company does",
        "The company develops and supports software solutions, product engineering work, and "
        "technology implementation efforts spanning web applications, integrations, and AI-assisted workflows.",
    ),
    (
        "company",
        "Summary",
        "This project reflects SASPAL's work in building AI-enabled application experiences and "
        "MCP-based integrations around Gmail and related business workflows.",
    ),
    (
        "services",
        "Custom software development",
        "Building tailored software solutions for business and product needs.",
    ),
    (
        "services",
        "Product engineering",
        "Supporting the design, development, and delivery of software products and features.",
    ),
    (
        "services",
        "Web application development",
        "Creating user-facing experiences and business workflows using modern web technologies.",
    ),
    (
        "services",
        "System integration and automation",
        "Connecting tools, APIs, and workflows to improve data flow and operational efficiency.",
    ),
    (
        "services",
        "AI-assisted workflow development",
        "Applying AI and automation to improve productivity, analysis, and intelligent user experiences.",
    ),
    (
        "ai_services",
        "AI workflow design",
        "Designing AI-assisted user experiences and workflow automation for business tasks.",
    ),
    (
        "ai_services",
        "Generative AI application support",
        "Supporting the development of applications that use LLM and generative AI capabilities.",
    ),
    (
        "ai_services",
        "MCP and tool integration",
        "Connecting AI systems to external tools, services, and data sources through MCP-style interfaces.",
    ),
    (
        "ai_services",
        "Intelligent assistants",
        "Building conversational and task-oriented assistant experiences for business use cases.",
    ),
    (
        "technology_stack",
        "Python",
        "Used for backend services, AI orchestration, and application logic in this project context.",
    ),
    (
        "technology_stack",
        "FastAPI",
        "Used for creating the web API layer and backend service endpoints.",
    ),
    (
        "technology_stack",
        "Model Context Protocol (MCP)",
        "Used to expose read-only tools and capabilities to a host and an LLM layer.",
    ),
    (
        "technology_stack",
        "JavaScript and frontend web development",
        "Used for browser-based user interfaces and client-side interactions.",
    ),
    (
        "technology_stack",
        "Google Gmail API",
        "Used for read-only Gmail access and message metadata retrieval in the project.",
    ),
    (
        "technology_stack",
        "OpenAI/OpenRouter compatible AI integrations",
        "Used for LLM-driven chat and summarization workflows in this project.",
    ),
    (
        "technology_stack",
        "Cloud and API integration",
        "Used for integrating AI, web apps, and external systems in a production-friendly way.",
    ),
    (
        "engagement_models",
        "Contract engagement",
        "Structured delivery under a formal client contract.",
    ),
    (
        "engagement_models",
        "Short-term support",
        "Focused work for a limited period to meet a specific business need.",
    ),
    (
        "engagement_models",
        "Project-based delivery",
        "Scoped product or engineering work delivered around defined project milestones.",
    ),
    (
        "engagement_models",
        "White-label delivery",
        "Collaborating behind the scenes to deliver solutions under the client's brand or platform.",
    ),
    (
        "engagement_models",
        "Ongoing engineering support",
        "Continuous technical support, iteration, and maintenance for existing products and systems.",
    ),
)


@contextmanager
def _database_connection() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_database() -> int:
    """Create the local schema and seed verified SASPAL records once.

    Returns the number of records inserted during this call.
    """
    with _database_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS company_info (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_company_info_record
            ON company_info (category, title, content)
            """
        )
        inserted_before = connection.total_changes
        connection.executemany(
            """
            INSERT OR IGNORE INTO company_info (category, title, content)
            VALUES (?, ?, ?)
            """,
            _COMPANY_INFO_RECORDS,
        )
        return connection.total_changes - inserted_before


def insert_company_info(category: str, title: str, content: str) -> bool:
    """Insert one record, returning False when the exact record already exists."""
    with _database_connection() as connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO company_info (category, title, content)
            VALUES (?, ?, ?)
            """,
            (category, title, content),
        )
        return cursor.rowcount == 1


def get_company_info() -> list[dict[str, object]]:
    """Return all company information records ordered by category and title."""
    with _database_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, category, title, content, created_at, updated_at
            FROM company_info
            ORDER BY category, title
            """
        ).fetchall()
        return [dict(row) for row in rows]


def get_info_by_category(category: str) -> list[dict[str, object]]:
    """Return all records in a category, ordered by title."""
    with _database_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, category, title, content, created_at, updated_at
            FROM company_info
            WHERE category = ?
            ORDER BY title
            """,
            (category,),
        ).fetchall()
        return [dict(row) for row in rows]


def search_company_info(query: str) -> list[dict[str, object]]:
    """Search record titles and content for a case-insensitive substring."""
    query = query.strip()
    if not query:
        return []

    with _database_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, category, title, content, created_at, updated_at
            FROM company_info
            WHERE instr(lower(title), lower(?)) > 0
               OR instr(lower(content), lower(?)) > 0
            ORDER BY category, title
            """,
            (query, query),
        ).fetchall()
        return [dict(row) for row in rows]


if __name__ == "__main__":
    inserted_count = initialize_database()
    print(f"Initialized {DATABASE_PATH.name}; inserted {inserted_count} records.")