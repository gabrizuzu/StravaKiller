"""Strumenti di sola lettura sui dati, condivisi da chat della webapp e server MCP."""
from __future__ import annotations

import json

from . import analytics as an

_INT = {"type": "integer"}
_NO_ARGS = {"type": "object", "properties": {}, "additionalProperties": False}


def _obj(**props):
    return {"type": "object", "properties": props, "additionalProperties": False}


TOOLS = {
    "get_summary": {
        "description": "Riepilogo: km della settimana e dell'anno, media 4 settimane, forma attuale (CTL/ATL/TSB), VDOT, data ultima corsa.",
        "input_schema": _NO_ARGS, "run": lambda a: an.summary()},
    "get_recommendations": {
        "description": "Consigli calcolati dalle regole dell'app, con i numeri che li hanno generati.",
        "input_schema": _NO_ARGS, "run": lambda a: an.recommendations()},
    "list_activities": {
        "description": "Elenco attività, dalla più recente. kind='run' per le sole corse. Distanze in metri, durate in secondi.",
        "input_schema": _obj(kind={"type": "string"}, days={**_INT, "description": "Solo gli ultimi N giorni"},
                             limit={**_INT, "description": "Massimo 100, predefinito 20"}),
        "run": lambda a: an.list_activities(a.get("kind"), None, a.get("days"), 0, min(a.get("limit", 20), 100))},
    "get_activity": {
        "description": "Dettaglio di un'attività: split al km, migliori sforzi, zone FC, tracciato ritmo/FC.",
        "input_schema": {**_obj(activity_id={"type": "string"}), "required": ["activity_id"]},
        "run": lambda a: an.activity_detail(a["activity_id"]) or {"error": "attività non trovata"}},
    "get_weekly_volume": {
        "description": "Volume settimanale di corsa: km, ore, numero di corse, dislivello.",
        "input_schema": _obj(weeks=_INT), "run": lambda a: an.weekly(min(a.get("weeks", 12), 260))},
    "get_training_load": {
        "description": "Serie giornaliera di carico: load, CTL (fitness), ATL (fatica), TSB (forma).",
        "input_schema": _obj(days=_INT), "run": lambda a: an.load_series(min(a.get("days", 60), 730))},
    "get_efficiency_and_zones": {
        "description": "Efficienza aerobica settimanale (m/min per battito) e minuti per zona FC.",
        "input_schema": _obj(weeks=_INT),
        "run": lambda a: {"efficiency": an.efficiency(min(a.get("weeks", 12), 104)), "zones": an.zones(min(a.get("weeks", 12), 104))}},
    "get_records_and_leaderboard": {
        "description": "Record personali e classifica dei tempi. distance_m tra 400, 1000, 1609, 3000, 5000, 10000, 15000, 21097, 42195.",
        "input_schema": _obj(distance_m=_INT, limit=_INT),
        "run": lambda a: {"records": an.records(),
                          "leaderboard": an.leaderboard(a.get("distance_m", 5000), min(a.get("limit", 10), 50))}},
    "get_race_predictions": {
        "description": "VDOT stimato dalle prestazioni degli ultimi 120 giorni, previsioni gara in secondi e ritmi di allenamento in secondi/km.",
        "input_schema": _NO_ARGS, "run": lambda a: an.predictions() or {"error": "dati insufficienti"}},
    "get_health": {
        "description": "Serie giornaliere di salute: passi, FC a riposo, stress, sonno (ore e punteggio), HRV, Body Battery, peso.",
        "input_schema": _obj(days=_INT), "run": lambda a: an.health(min(a.get("days", 30), 730))},
}

_CHECKS = {"integer": lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0, "string": lambda v: isinstance(v, str)}


def validate(name: str, args) -> str | None:
    """Con lo streaming eager il server non valida l'input: lo facciamo qui."""
    tool = TOOLS.get(name)
    if tool is None:
        return f"strumento sconosciuto: {name}"
    if not isinstance(args, dict):
        return "input non valido"
    schema = tool["input_schema"]
    for key, value in args.items():
        spec = schema["properties"].get(key)
        if spec is None or not _CHECKS[spec["type"]](value):
            return f"parametro non valido: {key}"
    missing = [k for k in schema.get("required", []) if k not in args]
    return f"parametri mancanti: {missing}" if missing else None


def run(name: str, args: dict) -> str:
    error = validate(name, args)
    if error:
        raise ValueError(error)
    return json.dumps(TOOLS[name]["run"](args), ensure_ascii=False)


def api_definitions() -> list[dict]:
    return [{"name": name, "description": t["description"], "input_schema": t["input_schema"],
             "eager_input_streaming": True} for name, t in TOOLS.items()]
