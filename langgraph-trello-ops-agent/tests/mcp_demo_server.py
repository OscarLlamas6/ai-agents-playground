"""Servidor MCP mínimo (stdio) para probar USE_MCP=1: una herramienta de lectura y una que 'escribe'. Sin red."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("demo")


@mcp.tool()
def status_page(service: str) -> str:
    """Estado público de un servicio (solo lectura)."""
    return f"{service}: operativo"


@mcp.tool()
def notify_team(message: str) -> str:
    """Envía un aviso al equipo (escritura)."""
    return f"aviso enviado: {message}"


if __name__ == "__main__":
    mcp.run()
