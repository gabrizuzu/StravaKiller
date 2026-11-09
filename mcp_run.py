"""Avvio del server MCP con percorso assoluto, indipendente dalla cartella corrente."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.mcp_server import main  # noqa: E402

main()
