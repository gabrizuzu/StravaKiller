"""Server MCP locale (stdio): stessi strumenti di sola lettura della chat, per l'app Claude."""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from . import db, tools

server = MCPServer("garmin-coach")
db.init()


def _call(name: str, **args) -> str:
    return tools.run(name, {k: v for k, v in args.items() if v is not None})


@server.tool()
def get_summary() -> str:
    """Riepilogo: km settimana/anno, media 4 settimane, forma (CTL/ATL/TSB), VDOT, ultima corsa."""
    return _call("get_summary")


@server.tool()
def get_recommendations() -> str:
    """Consigli calcolati dall'app, con i numeri che li hanno generati."""
    return _call("get_recommendations")


@server.tool()
def list_activities(kind: str | None = None, days: int | None = None, limit: int | None = None) -> str:
    """Elenco attività, dalla più recente. kind='run' per le corse. Metri e secondi."""
    return _call("list_activities", kind=kind, days=days, limit=limit)


@server.tool()
def get_activity(activity_id: str) -> str:
    """Dettaglio attività: split al km, migliori sforzi, zone FC, tracciato ritmo/FC."""
    return _call("get_activity", activity_id=activity_id)


@server.tool()
def get_weekly_volume(weeks: int | None = None) -> str:
    """Volume settimanale di corsa: km, ore, numero di corse, dislivello."""
    return _call("get_weekly_volume", weeks=weeks)


@server.tool()
def get_training_load(days: int | None = None) -> str:
    """Serie giornaliera di carico: load, CTL (fitness), ATL (fatica), TSB (forma)."""
    return _call("get_training_load", days=days)


@server.tool()
def get_efficiency_and_zones(weeks: int | None = None) -> str:
    """Efficienza aerobica settimanale (m/min per battito) e minuti per zona FC."""
    return _call("get_efficiency_and_zones", weeks=weeks)


@server.tool()
def get_records_and_leaderboard(distance_m: int | None = None, limit: int | None = None) -> str:
    """Record e classifica tempi. distance_m: 400, 1000, 1609, 3000, 5000, 10000, 15000, 21097, 42195."""
    return _call("get_records_and_leaderboard", distance_m=distance_m, limit=limit)


@server.tool()
def get_race_predictions() -> str:
    """VDOT stimato (ultimi 120 giorni), previsioni gara e ritmi di allenamento."""
    return _call("get_race_predictions")


@server.tool()
def get_health(days: int | None = None) -> str:
    """Serie giornaliere: passi, FC a riposo, stress, sonno, HRV, Body Battery, peso."""
    return _call("get_health", days=days)


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
