import sqlite3
from typing import TypedDict, cast

from database import DATABASE_PATH, get_info_by_category, search_company_info as search_database
from mcp.server import MCPServer


SERVER_NAME = "SASPAL Technologies MCP"

mcp = MCPServer(SERVER_NAME)


class CompanyInfo(TypedDict):
    """General read-only company information for SASPAL Technologies."""

    company_name: str
    positioning: str
    what_the_company_does: str
    summary: str


class ServiceOffering(TypedDict):
    """A software engineering or digital delivery service offering."""

    name: str
    description: str


class TechnologyArea(TypedDict):
    """A technology area or capability used in the project context."""

    name: str
    description: str


class EngagementModel(TypedDict):
    """A read-only engagement model description."""

    name: str
    description: str


_DATABASE_ERROR = {"error": "SASPAL information is temporarily unavailable."}


def _category_records(category: str) -> list[dict[str, object]] | None:
    """Read one information category, returning None if the database is unavailable."""
    if not DATABASE_PATH.is_file():
        return None
    try:
        return get_info_by_category(category)
    except (OSError, sqlite3.Error):
        return None


def _named_records(category: str) -> list[dict[str, str]] | dict[str, str]:
    records = _category_records(category)
    if records is None:
        return _DATABASE_ERROR.copy()
    return [
        {"name": str(record["title"]), "description": str(record["content"])}
        for record in records
    ]


@mcp.tool()
def get_company_info() -> CompanyInfo | dict[str, str]:
    """Return verified, general company information about SASPAL Technologies and its positioning."""
    records = _category_records("company")
    if records is None:
        return _DATABASE_ERROR.copy()

    values = {
        str(record["title"]): str(record["content"])
        for record in records
    }
    return {
        "company_name": values.get("Company name", ""),
        "positioning": values.get("Positioning", ""),
        "what_the_company_does": values.get("What the company does", ""),
        "summary": values.get("Summary", ""),
    }


@mcp.tool()
def get_services() -> list[ServiceOffering] | dict[str, str]:
    """Return general software engineering and development services offered by SASPAL Technologies."""
    return cast(list[ServiceOffering] | dict[str, str], _named_records("services"))


@mcp.tool()
def get_ai_services() -> list[dict[str, str]] | dict[str, str]:
    """Return SASPAL's AI and Generative AI capability areas in a read-only, general form."""
    return _named_records("ai_services")


@mcp.tool()
def get_technology_stack() -> list[TechnologyArea] | dict[str, str]:
    """Return technologies and technical areas reflected in SASPAL's current project context."""
    return cast(list[TechnologyArea] | dict[str, str], _named_records("technology_stack"))


@mcp.tool()
def get_engagement_models() -> list[EngagementModel] | dict[str, str]:
    """Return available engagement models such as contract, project-based, and ongoing engineering support."""
    return cast(list[EngagementModel] | dict[str, str], _named_records("engagement_models"))


@mcp.tool()
def search_company_info(query: str) -> list[dict[str, str]] | dict[str, str]:
    """Search the SASPAL company information and return the most relevant results for the supplied query."""
    if not query.strip():
        return []
    if not DATABASE_PATH.is_file():
        return _DATABASE_ERROR.copy()

    try:
        records = search_database(query)
    except (OSError, sqlite3.Error):
        return _DATABASE_ERROR.copy()

    return [
        {
            "section": str(record["category"]),
            "label": str(record["title"]),
            "value": str(record["content"]),
        }
        for record in records[:10]
    ]


if __name__ == "__main__":
    mcp.run()
