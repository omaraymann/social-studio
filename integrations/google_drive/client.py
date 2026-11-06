"""Reusable connection and tools for the Google Drive MCP server."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

server_directory = (
    Path(__file__).resolve().parents[2].parent / "google-drive-mcp"
)

server_parameters = StdioServerParameters(
    command=str(Path.home() / ".local/bin/node"),
    args=[str(server_directory / "dist" / "index.js")],
    env={"GOOGLE_DRIVE_MCP_SCOPES": "drive.readonly"},
)


@asynccontextmanager
async def connect_drive(*, allow_uploads: bool = False):
    """Open one initialized MCP session and close it when the workflow ends."""
    parameters = server_parameters
    if allow_uploads:
        parameters = server_parameters.model_copy(update={
            "env": {"GOOGLE_DRIVE_MCP_SCOPES": "drive.readonly,drive.file"},
        })
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def list_drive_tools(session: ClientSession):
    """List tool definitions through an existing connection."""
    result = await session.list_tools()
    return result.tools


async def search_drive_files(
    session: ClientSession, query: str, page_size: int = 5, *, raw_query: bool = False
):
    """Search Drive through an existing connection."""
    return await session.call_tool(
        "search",
        arguments={"query": query, "pageSize": page_size, "rawQuery": raw_query},
    )


async def read_drive_text_file(session: ClientSession, file_id: str):
    """Read a text file through an existing connection."""
    return await session.call_tool(
        "readTextFile",
        arguments={"fileId": file_id},
    )


async def create_drive_folder(session: ClientSession, name: str, parent_id: str = "root"):
    """Create a folder; requires drive.file or drive permission."""
    return await session.call_tool(
        "createFolder", arguments={"name": name, "parent": parent_id},
    )


async def upload_drive_file(session: ClientSession, path: Path, parent_id: str):
    """Upload a local file to a specified folder without changing sharing."""
    return await session.call_tool(
        "uploadFile",
        arguments={"localPath": str(path.resolve(strict=True)),
                   "name": path.name, "parentFolderId": parent_id,
                   **({"mimeType": "text/plain"} if path.suffix.lower() == ".txt" else {})},
    )


async def main():
    # Reuse one connection for both requests in this local demo.
    async with connect_drive() as session:
        tools = await list_drive_tools(session)
        print(f"Available tools: {len(tools)}\n")

        result = await read_drive_text_file(
            session, "19UgQ2MnHVAX4P5I9sKGgOml56Mf9mR7p"
        )

        for block in result.content:
            if block.type == "text":
                print(block.text)


if __name__ == "__main__":
    asyncio.run(main())
