"""Endpoints Polar bajo /api/v1/polar/*"""
from __future__ import annotations
import hmac
import hashlib
import time
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from ..auth import Principal, require_user
from ..config import get_settings
from ..gpx_processor import process_gpx
from ..jobs import Job
from ..schemas import RenderOptions
from .models import PolarStatus, PolarExercise, SyncResult, ImportResult
from .store import PolarStore
from . import client as polar_client

from pathlib import Path
from fastapi.concurrency import run_in_threadpool
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/polar", tags=["polar"])

# Store lazy (usa mismo db_path que users)
def get_polar_store() -> PolarStore:
    from ..config import get_settings
    return PolarStore(get_settings().db_path)

def _sign_state(user_id: int) -> str:
    s = get_settings()
    ts = str(int(time.time()))
    msg = f"{user_id}.{ts}.{uuid.uuid4().hex[:8]}"
    sig = hmac.new(s.session_secret.encode() if s.session_secret else b"cumbre", msg.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{msg}.{sig}"

def _verify_state(state: str, max_age_s: int = 600) -> int:
    """Devuelve user_id si firma válida y no expirada."""
    s = get_settings()
    try:
        parts = state.split(".")
        if len(parts) != 4:
            raise ValueError("state malformed")
        user_id_s, ts_s, nonce, sig = parts
        msg = f"{user_id_s}.{ts_s}.{nonce}"
        exp_sig = hmac.new(s.session_secret.encode() if s.session_secret else b"cumbre", msg.encode(), hashlib.sha256).hexdigest()[:16]
        if not hmac.compare_digest(sig, exp_sig):
            raise ValueError("bad sig")
        if int(time.time()) - int(ts_s) > max_age_s:
            raise ValueError("expired")
        return int(user_id_s)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"state inválido: {e}")

@router.get("/status", response_model=PolarStatus)
def polar_status(user: Principal = Depends(require_user), store: PolarStore = Depends(get_polar_store)):
    s = get_settings()
    if not s.polar_enabled:
        return PolarStatus(connected=False)
    if user.user_id is None:
        # usuarios vía API key estática no tienen polar (no hay user_id persistente)
        return PolarStatus(connected=False)
    acc = store.get_account(user.user_id)
    if not acc:
        return PolarStatus(connected=False)
    return PolarStatus(connected=True, polar_user_id=acc.polar_user_id, last_sync_at=acc.last_sync_at)

@router.get("/auth-url")
def polar_auth_url(request: Request, user: Principal = Depends(require_user)):
    s = get_settings()
    if not s.polar_enabled:
        raise HTTPException(status_code=503, detail="Polar no configurado en el servidor (GPX_POLAR_ENABLED=0)")
    if user.user_id is None:
        raise HTTPException(status_code=403, detail="Necesitas una cuenta registrada (no clave estática)")
    state = _sign_state(user.user_id)
    url = polar_client.build_auth_url(state)
    return {"auth_url": url, "state": state}

@router.get("/callback")
def polar_callback(code: str = Query(...), state: str = Query(...), store: PolarStore = Depends(get_polar_store)):
    # Verificar state y obtener user_id original
    user_id = _verify_state(state)
    s = get_settings()
    try:
        token_data = polar_client.exchange_code(code)
        access_token = token_data["access_token"]
        # Intentar registrar polar user; member-id puede ser el id interno
        member_id = f"cumbre_{user_id}"
        try:
            polar_user_id = polar_client.register_polar_user(access_token, member_id)
        except Exception as e:
            # si ya registrado, usar el x_user_id del token
            polar_user_id = token_data.get("x_user_id") or 0
            if not polar_user_id:
                raise HTTPException(status_code=502, detail=f"Polar register falló: {e}")
        store.save_account(user_id, int(polar_user_id), access_token)
        logger.info("Polar vinculado: user_id=%s polar_user_id=%s", user_id, polar_user_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Polar callback error")
        raise HTTPException(status_code=502, detail=f"Error Polar: {e}")
    # Redirigir a frontend /polar con toast
    return RedirectResponse(url="/?polar=connected", status_code=302)

@router.post("/disconnect")
def polar_disconnect(user: Principal = Depends(require_user), store: PolarStore = Depends(get_polar_store)):
    if user.user_id is None:
        raise HTTPException(status_code=403, detail="No hay cuenta")
    store.delete_account(user.user_id)
    return {"ok": True}

@router.get("/exercises", response_model=list[PolarExercise])
def polar_exercises(
    only_with_route: bool = True,
    user: Principal = Depends(require_user),
    store: PolarStore = Depends(get_polar_store),
):
    if user.user_id is None:
        raise HTTPException(status_code=403, detail="No hay cuenta")
    acc = store.get_account(user.user_id)
    if not acc:
        raise HTTPException(status_code=404, detail="No vinculado a Polar. Conecta primero.")
    try:
        raw = polar_client.list_exercises(acc.access_token, acc.polar_user_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Polar API: {e}")

    imported = store.imported_map(user.user_id)
    out: list[PolarExercise] = []
    for ex in raw:
        eid = str(ex.get("id") or ex.get("exerciseId") or ex.get("transactionId") or "")
        if not eid:
            continue
        has_route = ex.get("has_route")
        if has_route is None:
            has_route = ex.get("hasRoute")
        if has_route is None:
            has_route = ex.get("has-route")
        if has_route is None:
            has_route = bool(ex.get("distance") or ex.get("distance_m"))
        else:
            has_route = bool(has_route)
        if only_with_route and not has_route:
            continue
        # duration puede venir como "PT360.832S" o ms numérico
        dur_raw = ex.get("duration") or ex.get("duration_ms") or ex.get("durationMs")
        duration_ms = None
        if isinstance(dur_raw, (int, float)):
            duration_ms = int(dur_raw)
            # si parece segundos (< 10h) convierte a ms
            if duration_ms < 100000:
                duration_ms = int(duration_ms * 1000)
        elif isinstance(dur_raw, str) and dur_raw.startswith("PT"):
            try:
                import re
                m = re.match(r"PT(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?", dur_raw)
                if m:
                    h = float(m.group(1) or 0); mi = float(m.group(2) or 0); s = float(m.group(3) or 0)
                    duration_ms = int((h*3600 + mi*60 + s)*1000)
            except Exception:
                pass
        start = str(ex.get("start_time") or ex.get("startTime") or ex.get("start-time") or "")
        sport = str(ex.get("sport") or ex.get("detailed_sport_info") or ex.get("sportId") or "Polar")
        out.append(PolarExercise(
            id=eid,
            polar_user_id=acc.polar_user_id,
            start_time=start,
            sport=sport,
            duration_ms=duration_ms,
            distance_m=ex.get("distance") or ex.get("distance_m"),
            has_route=has_route,
            already_imported=eid in imported,
            title=f"{sport} {start[:10]}",
        ))
    return out

def _import_one(store: PolarStore, user: Principal, polar_exercise_id: str) -> ImportResult:
    """Descarga GPX y crea Job+Video (síncrono, llamado desde endpoint)."""
    if user.user_id is None:
        raise HTTPException(status_code=403, detail="No hay cuenta")
    acc = store.get_account(user.user_id)
    if not acc:
        raise HTTPException(status_code=404, detail="No vinculado")
    if store.is_imported(user.user_id, polar_exercise_id):
        raise HTTPException(status_code=409, detail="Ya importado")

    gpx_bytes = polar_client.download_gpx(acc.access_token, polar_exercise_id)

    # Validar y procesar GPX igual que upload
    from ..main import store as job_store, pipeline, library
    from ..config import get_settings
    settings = get_settings()
    options = RenderOptions()  # defaults 1080p follow; el usuario puede re-renderizar luego

    # process_gpx es sync
    track = process_gpx(gpx_bytes, options, settings)
    # Título desde Polar
    title = f"Polar {polar_exercise_id[:8]}"
    try:
        # intentar extraer sport+fecha del listado para título bonito
        exs = polar_client.list_exercises(acc.access_token, acc.polar_user_id)
        for ex in exs:
            if str(ex.get("id")) == polar_exercise_id:
                sport = ex.get("sport") or "Polar"
                date = str(ex.get("startTime") or "")[:10]
                title = f"{sport} {date}" if date else sport
                break
    except Exception:
        pass
    title = title[:120]

    import uuid as _uuid
    from ..jobs import Job
    job_id = _uuid.uuid4().hex
    job = Job(job_id=job_id, options=options, track=track, title=title, owner=user.name)
    job_store.add(job)
    pipeline.submit(job_id)
    store.mark_imported(user.user_id, polar_exercise_id, job_id)
    return ImportResult(video_id=job_id, job_id=job_id, polar_exercise_id=polar_exercise_id, title=title)

@router.post("/import/{polar_exercise_id}", response_model=ImportResult)
def polar_import_one(polar_exercise_id: str, user: Principal = Depends(require_user), store: PolarStore = Depends(get_polar_store)):
    return _import_one(store, user, polar_exercise_id)

@router.post("/sync", response_model=SyncResult)
def polar_sync(limit: int = Query(20, ge=1, le=50), only_with_route: bool = True,
               user: Principal = Depends(require_user), store: PolarStore = Depends(get_polar_store)):
    if user.user_id is None:
        raise HTTPException(status_code=403, detail="No hay cuenta")
    acc = store.get_account(user.user_id)
    if not acc:
        raise HTTPException(status_code=404, detail="No vinculado")
    try:
        raw = polar_client.list_exercises(acc.access_token, acc.polar_user_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Polar API: {e}")

    res = SyncResult()
    for ex in raw[:limit]:
        eid = str(ex.get("id") or ex.get("exerciseId") or "")
        if not eid:
            continue
        res.fetched += 1
        hr = ex.get("has_route")
        if hr is None: hr = ex.get("hasRoute")
        if hr is None: hr = ex.get("has-route")
        has_route = bool(hr) if hr is not None else bool(ex.get("distance"))
        if only_with_route and not has_route:
            res.skipped_no_route += 1
            continue
        if store.is_imported(user.user_id, eid):
            res.skipped_already_imported += 1
            continue
        try:
            out = _import_one(store, user, eid)
            res.imported += 1
            res.video_ids.append(out.video_id)
        except HTTPException as he:
            if he.status_code == 409:
                res.skipped_already_imported += 1
            else:
                res.failed += 1
                res.errors.append(f"{eid}: {he.detail}")
        except Exception as e:
            res.failed += 1
            res.errors.append(f"{eid}: {e}")
    store.touch_sync(user.user_id)
    return res
