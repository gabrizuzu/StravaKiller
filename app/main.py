"""API locale e pagina web. Accetta richieste solo da questo computer."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import analytics as an
from . import coach, config, db, sync

STATIC = Path(__file__).parent / "static"
_HOSTS = {f"127.0.0.1:{config.PORT}", f"localhost:{config.PORT}"}
_ORIGINS = {f"http://{h}" for h in _HOSTS}
MAX_UPLOAD_BYTES = 2_000_000_000

db.init()
if config.DEMO:
    from . import demo
    demo.seed_if_empty()

app = FastAPI(title="StravaKiller", docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def local_only(request: Request, call_next):
    """Blocca DNS rebinding e richieste scriventi da siti esterni aperti nel browser."""
    if request.headers.get("host") not in _HOSTS:
        return JSONResponse({"detail": "host non consentito"}, status_code=403)
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD") and origin and origin not in _ORIGINS:
        return JSONResponse({"detail": "origine non consentita"}, status_code=403)
    return await call_next(request)


class SettingsIn(BaseModel):
    max_hr: float | None = None
    resting_hr: float | None = None
    goal: dict | None = None
    model: str | None = None


class SecretIn(BaseModel):
    name: str
    value: str = ""


class ChatIn(BaseModel):
    conversation_id: str
    message: str


@app.get("/api/status")
def status():
    with db.connect() as c:
        counts = {
            "activities": c.execute("SELECT COUNT(*) n FROM activities").fetchone()["n"],
            "runs": c.execute(f"SELECT COUNT(*) n FROM activities WHERE {an.RUN}").fetchone()["n"],
            "days": c.execute("SELECT COUNT(*) n FROM daily").fetchone()["n"],
        }
    return {"demo": config.DEMO, "job": sync.status, "last_sync": db.get_setting("last_sync"), "counts": counts,
            "has_garmin_key": bool(config.get_secret("garmin_api_key")),
            "has_anthropic_key": bool(config.get_secret("anthropic_api_key")),
            "data_dir": str(config.DATA_DIR)}


@app.post("/api/sync")
def run_sync():
    if config.DEMO:
        raise HTTPException(400, "In modalità demo la sincronizzazione è disattivata.")
    if not config.get_secret("garmin_api_key"):
        raise HTTPException(400, "Aggiungi prima la chiave Personal API di Garmin.")
    if not sync.start_sync():
        raise HTTPException(409, "Un'operazione è già in corso.")
    return {"started": True}


@app.post("/api/import")
async def import_files(files: list[UploadFile]):
    if config.DEMO:
        raise HTTPException(400, "In modalità demo l'import è disattivato.")
    blobs, total = [], 0
    for upload in files:
        data = await upload.read()
        total += len(data)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "File troppo grandi: massimo 2 GB per volta.")
        blobs.append((upload.filename or "", data))
    if not sync.start_import(blobs):
        raise HTTPException(409, "Un'operazione è già in corso.")
    return {"started": True}


@app.get("/api/overview")
def overview():
    return {"summary": an.summary(), "recommendations": an.recommendations(), "weekly": an.weekly(26),
            "recent": an.list_activities("run", limit=6)["items"]}


@app.get("/api/progress")
def progress(weeks: int = 52):
    weeks = max(4, min(weeks, 520))
    return {"weekly": an.weekly(weeks), "load": an.load_series(weeks * 7), "efficiency": an.efficiency(weeks),
            "zones": an.zones(weeks), "monthly": an.monthly(max(6, weeks // 4)),
            "fitness": an.fitness_history(max(6, weeks // 4)), "predictions": an.predictions()}


@app.get("/api/recommendations")
def recommendations():
    return an.recommendations()


@app.get("/api/leaderboard")
def leaderboard(distance: int = 5000, limit: int = 20):
    if distance not in an.DISTANCE_LABELS:
        raise HTTPException(400, "Distanza non supportata.")
    return {"distance_m": distance, "items": an.leaderboard(distance, max(1, min(limit, 100))),
            "progression": an.pr_progression(distance), "records": an.records(),
            "distances": [{"m": k, "label": v} for k, v in an.DISTANCE_LABELS.items()]}


@app.get("/api/activities")
def activities(kind: str | None = None, q: str | None = None, offset: int = 0, limit: int = 30):
    return an.list_activities(kind, q, None, max(0, offset), max(1, min(limit, 200)))


@app.get("/api/activities/{activity_id}")
def activity(activity_id: str):
    detail = an.activity_detail(activity_id)
    if detail is None:
        raise HTTPException(404, "Attività non trovata.")
    return detail


@app.get("/api/health")
def health(days: int = 90):
    return an.health(max(7, min(days, 3650)))


@app.get("/api/settings")
def read_settings():
    max_hr, rest_hr = an.hr_limits()
    return {"max_hr": db.get_setting("max_hr"), "resting_hr": db.get_setting("resting_hr"),
            "effective_max_hr": max_hr, "effective_resting_hr": rest_hr, "goal": db.get_setting("goal"),
            "model": db.get_setting("model") or coach.DEFAULT_MODEL, "models": coach.MODELS}


@app.post("/api/settings")
def write_settings(body: SettingsIn):
    if body.model is not None and body.model not in coach.MODELS:
        raise HTTPException(400, "Modello non valido.")
    db.set_setting("max_hr", body.max_hr)
    db.set_setting("resting_hr", body.resting_hr)
    db.set_setting("goal", body.goal)
    if body.model:
        db.set_setting("model", body.model)
    return {"ok": True}


@app.post("/api/secrets")
def write_secret(body: SecretIn):
    if body.name not in config.SECRET_NAMES:
        raise HTTPException(400, "Nome non valido.")
    config.save_secret(body.name, body.value.strip())
    return {"ok": True}


@app.post("/api/chat/new")
def chat_new():
    return {"conversation_id": coach.new_conversation()}


@app.get("/api/chat/{conversation_id}")
def chat_history(conversation_id: str):
    return coach.visible_history(conversation_id)


@app.post("/api/chat")
def chat(body: ChatIn):
    def events():
        for event in coach.reply(body.conversation_id, body.message):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
