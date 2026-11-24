"""Cliente mínimo Polar AccessLink V3 (httpx sync para no añadir deps async)."""
from __future__ import annotations
import base64
import logging
import time
from typing import Optional
import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)

POLAR_AUTH_URL = "https://flow.polar.com/oauth2/authorization"
POLAR_TOKEN_URL = "https://polarremote.com/v2/oauth2/token"
POLAR_API_BASE = "https://www.polaraccesslink.com/v3"

# Timeout generoso pero no bloqueante para VPS 826 MB
TIMEOUT = httpx.Timeout(20.0, connect=10.0)

def _settings():
    return get_settings()

def build_auth_url(state: str) -> str:
    s = _settings()
    if not s.polar_client_id or not s.polar_redirect_url:
        raise RuntimeError("Polar no configurado (GPX_POLAR_CLIENT_ID/REDIRECT_URL)")
    from urllib.parse import urlencode
    q = urlencode({
        "response_type": "code",
        "client_id": s.polar_client_id,
        "redirect_uri": s.polar_redirect_url,
        "scope": "accesslink.read_all",
        "state": state,
    })
    return f"{POLAR_AUTH_URL}?{q}"

def exchange_code(code: str) -> dict:
    """Intercambia authorization code por access_token.
    Docs Polar 2026: POST https://polarremote.com/v2/oauth2/token con Basic Auth.
    El code expira en 10 min y es de un solo uso (reusarlo borra todos los tokens).
    Devuelve {"access_token": "...", "x_user_id": 123} o similar.
    """
    s = _settings()
    auth = base64.b64encode(f"{s.polar_client_id}:{s.polar_client_secret}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json;charset=UTF-8"}
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": s.polar_redirect_url}
    with httpx.Client(timeout=TIMEOUT) as client:
        resp = client.post(POLAR_TOKEN_URL, headers=headers, data=data)
        if resp.status_code != 200:
            logger.error("Polar token exchange %s: %s", resp.status_code, resp.text[:500])
            raise RuntimeError(f"Polar token error {resp.status_code}: {resp.text[:200]}")
        j = resp.json()
        token = j.get("access_token") or j.get("access-token") or j.get("token")
        uid = j.get("x_user_id") or j.get("x-user-id") or j.get("user_id") or j.get("polar_user_id")
        if not token:
            raise RuntimeError(f"Polar token sin access_token: {j}")
        return {"access_token": token, "x_user_id": int(uid) if uid else None, "raw": j}

def register_polar_user(access_token: str, member_id: str) -> int:
    """Registra al usuario en Polar (obligatorio la primera vez). Devuelve polar_user_id."""
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json", "Accept": "application/json"}
    with httpx.Client(timeout=TIMEOUT) as client:
        resp = client.post(f"{POLAR_API_BASE}/users", headers=headers, json={"member-id": member_id})
        if resp.status_code in (200, 201):
            j = resp.json()
            return int(j.get("polar-user-id") or j.get("polar_user_id") or j["x_user_id"])
        # 409 = ya registrado
        if resp.status_code == 409:
            # recuperar id vía GET
            resp2 = client.get(f"{POLAR_API_BASE}/users/{member_id}", headers=headers)
            if resp2.status_code == 200:
                return int(resp2.json().get("polar-user-id") or member_id)
        logger.warning("Polar register %s: %s", resp.status_code, resp.text[:300])
        raise RuntimeError(f"Polar register {resp.status_code}")

def list_exercises(access_token: str, polar_user_id: int | None = None) -> list[dict]:
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    # 1. Training-data API (nueva, sin transacciones)
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
        for url in [f"{POLAR_API_BASE}/exercises"]:
            resp = client.get(url, headers=headers)
            logger.info("Polar list %s -> %s %s", url, resp.status_code, resp.text[:1200])
            if resp.status_code == 200:
                try:
                    j = resp.json()
                    if isinstance(j, dict) and "exercises" in j and j["exercises"]:
                        return j["exercises"]
                    if isinstance(j, list) and j:
                        return j
                except Exception:
                    pass

        # 2. Transaction API (clásica) — requiere POST para crear transacción
        if polar_user_id:
            # POST crea transacción
            post_url = f"{POLAR_API_BASE}/users/{polar_user_id}/exercise-transactions"
            resp = client.post(post_url, headers=headers)
            logger.info("Polar POST %s -> %s %s", post_url, resp.status_code, resp.text[:1200])
            if resp.status_code in (200, 201):
                # Location header con URL de la transacción, o JSON con transactionId
                location = resp.headers.get("Location") or resp.headers.get("location")
                if not location:
                    try:
                        j = resp.json()
                        location = j.get("Location") or j.get("transactionId") or j.get("transaction-id")
                        if location and not location.startswith("http"):
                            location = f"{POLAR_API_BASE}/users/{polar_user_id}/exercise-transactions/{location}"
                    except Exception:
                        pass
                if location:
                    if not location.startswith("http"):
                        location = f"{POLAR_API_BASE}{location}" if location.startswith("/") else f"{POLAR_API_BASE}/users/{polar_user_id}/exercise-transactions/{location}"
                    # GET de la transacción lista ejercicios
                    resp2 = client.get(location, headers=headers)
                    logger.info("Polar GET %s -> %s %s", location, resp2.status_code, resp2.text[:1500])
                    if resp2.status_code == 200:
                        try:
                            j2 = resp2.json()
                            # puede ser {"exercises": ["id1","id2"]} o lista de objetos
                            if isinstance(j2, dict) and "exercises" in j2:
                                exs = j2["exercises"]
                                # si son IDs, fetch cada uno
                                if exs and isinstance(exs[0], str):
                                    detailed = []
                                    for eid in exs:
                                        r3 = client.get(f"{POLAR_API_BASE}/users/{polar_user_id}/exercises/{eid}", headers=headers)
                                        if r3.status_code == 200:
                                            detailed.append(r3.json())
                                    return detailed
                                elif exs:
                                    return exs
                            elif isinstance(j2, list) and j2:
                                return j2
                        except Exception as e:
                            logger.warning("Polar transaction parse %s", e)
            elif resp.status_code == 204:
                logger.info("Polar no hay transacciones nuevas (204)")
        return []

def download_gpx(access_token: str, exercise_id: str) -> bytes:
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/gpx+xml, application/xml, text/xml, */*"}
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
        resp = client.get(f"{POLAR_API_BASE}/exercises/{exercise_id}/gpx", headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"Polar GPX {exercise_id} {resp.status_code}: {resp.text[:300]}")
        # validar que sea GPX
        if b"<gpx" not in resp.content[:2000] and b"<trk" not in resp.content[:2000]:
            # algunos entrenos devuelven 200 vacío
            raise RuntimeError("GPX vacío o sin ruta")
        return resp.content
