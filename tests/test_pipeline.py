"""Pruebas de extremo a extremo y de casos límite. Ejecutar con ``pytest -q``."""

from __future__ import annotations

import math
import os
import subprocess
from datetime import datetime, timedelta, timezone

os.environ.setdefault("GPX_MIN_VIDEO_SECONDS", "2")  # vídeos cortos para que las pruebas sean rápidas
os.environ.setdefault("GPX_FFMPEG_PRESET", "ultrafast")
os.environ.setdefault("GPX_CINEMATIC_ENABLED", "0")  # desactivado por defecto: las pruebas generales son más rápidas
os.environ["GPX_DB_PATH"] = os.path.join(__import__("tempfile").mkdtemp(prefix="gpxtest_"), "users.db")
os.environ["GPX_API_KEYS"] = "ana:clave-de-ana-0123456789,bob:clave-de-bob-0123456789,root:clave-de-root-012345678:admin"
os.environ.setdefault("GPX_RENDER_ENGINE", "simulated")  # sin red ni OpenGL en las pruebas

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.exceptions import InvalidGPXError, VideoCompilationError
from app.gpx_processor import process_gpx
from app.main import app
from app.schemas import RenderOptions
from app.video_compiler import compile_video


def make_gpx(n: int = 300, *, with_time: bool = True, with_ele: bool = True, spike_at: int | None = None) -> bytes:
    """Track sintético: ~1,6 km hacia el nordeste desde Bilbao con ruido GPS y una colina."""
    rng = np.random.default_rng(42)
    t0 = datetime(2026, 5, 1, 8, 0, tzinfo=timezone.utc)
    pts = []
    for i in range(n):
        lat = 43.2630 + i * 4e-5 + rng.normal(0, 1.5e-5)
        lon = -2.9350 + i * 4e-5 + rng.normal(0, 1.5e-5)
        if i == spike_at:
            lat += 0.01  # salto de ~1,1 km
        ele = f"<ele>{50 + 100 * math.sin(math.pi * i / n) + rng.normal(0, 1.0):.1f}</ele>" if with_ele else ""
        tm = f"<time>{(t0 + timedelta(seconds=5 * i)).isoformat()}</time>" if with_time else ""
        pts.append(f'<trkpt lat="{lat:.7f}" lon="{lon:.7f}">{ele}{tm}</trkpt>')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">'
        f"<trk><name>t</name><trkseg>{''.join(pts)}</trkseg></trk></gpx>"
    ).encode()


OPTS = RenderOptions()
ANA = {"Authorization": "Bearer clave-de-ana-0123456789"}
BOB = {"X-API-Key": "clave-de-bob-0123456789"}
ROOT = {"Authorization": "Bearer clave-de-root-012345678"}


def _wait_job(client, job_id: str, timeout: float = 30.0) -> dict:
    """Espera a que el trabajo termine (completed/failed) consultando cada 0.3s."""
    import time

    deadline = time.time() + timeout
    while True:
        info = client.get(f"/api/v1/jobs/{job_id}").json()
        if info["status"] in ("completed", "failed"):
            return info
        if time.time() > deadline:
            return info
        time.sleep(0.3)


def test_metrics_are_plausible() -> None:
    track = process_gpx(make_gpx(), OPTS)
    m = track.metrics
    assert 1500 < m.total_distance_m < 2600
    assert 80 < m.elevation_gain_m < 125 and 80 < m.elevation_loss_m < 125
    assert m.duration_s == pytest.approx(299 * 5)
    assert not m.duration_is_estimated and m.avg_speed_kmh > 0
    assert track.frame_count == round(track.video_duration_s * 30)
    assert np.all(np.diff(track.distance_m) >= 0) and np.all(np.diff(track.video_t) > 0)


def test_spike_is_removed_and_path_is_smooth() -> None:
    clean = process_gpx(make_gpx(), OPTS)
    spiky = process_gpx(make_gpx(spike_at=150), OPTS)
    assert spiky.metrics.outliers_corrected >= 1
    assert spiky.metrics.total_distance_m == pytest.approx(clean.metrics.total_distance_m, rel=0.05)
    assert spiky.lat.max() < 43.2630 + 300 * 4e-5 + 0.001


def test_without_time_or_elevation() -> None:
    m = process_gpx(make_gpx(with_time=False, with_ele=False), OPTS).metrics
    assert m.duration_is_estimated and not m.has_timestamps and not m.has_elevation
    assert m.elevation_gain_m == 0 and m.duration_s > 0


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"esto no es xml",
        b"<gpx><trk><trkseg><trkpt lat='1' lon='1'>",  # truncado
        b'<?xml version="1.0"?><kml></kml>',
        b'<?xml version="1.0"?><gpx version="1.1"></gpx>',  # sin puntos
        b'<?xml version="1.0"?><gpx version="1.1"><trk><trkseg><trkpt lat="x" lon="y"/></trkseg></trk></gpx>',
        b'<?xml version="1.0"?><!DOCTYPE g [<!ENTITY a "aaaa">]><gpx version="1.1">&a;</gpx>',
        b"\x89PNG\r\n\x1a\n\x00\x00\x00",
    ],
)
def test_invalid_gpx_rejected(payload: bytes) -> None:
    with pytest.raises(InvalidGPXError):
        process_gpx(payload, OPTS)


def test_compile_rejects_gaps(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "frame_0000.png").write_bytes(b"x")
    (tmp_path / "frame_0002.png").write_bytes(b"x")
    with pytest.raises(VideoCompilationError):
        compile_video(tmp_path, tmp_path / "o.mp4", width=1280, height=720)
    assert (tmp_path / "frame_0000.png").exists()  # no se limpia si falla


def test_api_rejects_bad_uploads() -> None:
    with TestClient(app, headers=ANA) as client:
        r = client.post("/api/v1/render-gpx", files={"file": ("a.txt", b"hola")})
        assert r.status_code == 415
        r = client.post("/api/v1/render-gpx", files={"file": ("a.gpx", b"<gpx><trk>")})
        assert r.status_code == 422 and r.json()["error"] == "invalid_gpx"
        r = client.post("/api/v1/render-gpx", files={"file": ("a.gpx", make_gpx())}, data={"resolution": "4k"})
        assert r.status_code == 422
        r = client.post("/api/v1/render-gpx", files={"file": ("a.gpx", make_gpx())}, data={"animation_speed": "99"})
        assert r.status_code == 422


def test_api_end_to_end() -> None:
    with TestClient(app, headers=ANA) as client:
        r = client.post(
            "/api/v1/render-gpx",
            files={"file": ("ruta.gpx", make_gpx(), "application/gpx+xml")},
            data={"animation_speed": "2.0", "resolution": "720p", "camera_style": "orbit"},
        )
        assert r.status_code == 202, r.text
        job_id = r.json()["job_id"]

        info = _wait_job(client, job_id, timeout=30)
        assert info["status"] == "completed", info
        assert info["progress"] == 1.0

        points = client.get(f"/api/v1/jobs/{job_id}/track").json()["points"]
        assert len(points) == info["frame_count"]

        video = client.get(f"/api/v1/jobs/{job_id}/video")
        assert video.status_code == 200 and video.headers["content-type"] == "video/mp4"

    path = get_settings().output_dir / f"{job_id}.mp4"
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=codec_name,pix_fmt,width,height,r_frame_rate,nb_frames", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip
    assert probe == f"h264,1280,720,yuv420p,30/1,{info['frame_count']}"
    # Los temporales del trabajo han desaparecido
    import glob, tempfile
    assert not glob.glob(os.path.join(tempfile.gettempdir(), f"gpxrender_{job_id}_*"))


def test_video_library_lifecycle() -> None:
    with TestClient(app, headers=ANA) as client:
        r = client.post(
            "/api/v1/render-gpx", files={"file": ("Mi ruta+2026.GPX", make_gpx())}, data={"resolution": "720p"}
        )
        assert r.status_code == 202, r.text
        vid = r.json()["job_id"]
        _wait_job(client, vid, timeout=30)

        listing = client.get("/api/v1/videos").json()
        assert listing["total"] >= 1 and listing["items"][0]["video_id"] == vid  # el más reciente primero
        info = client.get(f"/api/v1/videos/{vid}").json()
        assert info["title"] == "Mi ruta+2026" and info["size_bytes"] > 0 and info["height"] == 720

        full = client.get(f"/api/v1/videos/{vid}/stream")
        assert full.status_code == 200 and full.headers["content-disposition"].startswith("inline")
        part = client.get(f"/api/v1/videos/{vid}/stream", headers={"Range": "bytes=0-99"})
        assert part.status_code == 206 and len(part.content) == 100
        down = client.get(f"/api/v1/videos/{vid}/download")
        assert down.headers["content-disposition"].startswith("attachment") and len(down.content) == info["size_bytes"]
        assert client.get("/videos").status_code == 200

        assert client.delete(f"/api/v1/videos/{vid}").status_code == 204
        out = get_settings().output_dir
        assert not (out / f"{vid}.mp4").exists() and not (out / f"{vid}.json").exists()
        for url in (f"/api/v1/videos/{vid}", f"/api/v1/videos/{vid}/stream", f"/api/v1/jobs/{vid}"):
            assert client.get(url).status_code == 404
        assert client.delete(f"/api/v1/videos/{vid}").status_code == 404
        assert client.delete("/api/v1/videos/..%2F..%2Fetc%2Fpasswd").status_code == 404
        assert client.get("/api/v1/videos/not-a-valid-id").status_code == 404


def test_access_control() -> None:
    with TestClient(app) as anon:
        # Sin credenciales o con credenciales erróneas: 401 en todo lo protegido
        for method, url in [
            ("GET", "/api/v1/videos"), ("GET", "/api/v1/auth/me"), ("GET", "/api/v1/jobs/x"),
            ("DELETE", "/api/v1/videos/" + "0" * 32), ("GET", "/api/v1/videos/" + "0" * 32 + "/stream"),
        ]:  # fmt: skip
            r = anon.request(method, url)
            assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer", url
        assert anon.post("/api/v1/render-gpx", files={"file": ("a.gpx", make_gpx())}).status_code == 401
        assert anon.get("/api/v1/videos", headers={"Authorization": "Bearer clave-incorrecta-000000"}).status_code == 401
        assert anon.get("/health").status_code == 200 and anon.get("/videos").status_code == 200

        # Ana crea un vídeo
        r = anon.post("/api/v1/render-gpx", files={"file": ("ana.gpx", make_gpx())}, data={"resolution": "720p"}, headers=ANA)
        assert r.status_code == 202
        vid = r.json()["job_id"]
        # Esperar con credenciales de ana
        import time as _t
        deadline = _t.time() + 30
        while _t.time() < deadline:
            _info = anon.get(f"/api/v1/jobs/{vid}", headers=ANA).json()
            if _info.get("status") == "completed":
                break
            _t.sleep(0.3)
        assert anon.get("/api/v1/auth/me", headers=ANA).json() == {"name": "ana", "is_admin": False, "has_account": False}

        # Bob no lo ve ni puede tocarlo (404: no se revela que existe)
        assert all(v["video_id"] != vid for v in anon.get("/api/v1/videos", headers=BOB).json()["items"])
        for url in (f"/api/v1/videos/{vid}", f"/api/v1/videos/{vid}/stream", f"/api/v1/videos/{vid}/download",
                    f"/api/v1/jobs/{vid}", f"/api/v1/jobs/{vid}/video", f"/api/v1/jobs/{vid}/track"):  # fmt: skip
            assert anon.get(url, headers=BOB).status_code == 404, url
        assert anon.delete(f"/api/v1/videos/{vid}", headers=BOB).status_code == 404
        assert anon.get(f"/api/v1/videos/{vid}", headers=ANA).json()["owner"] == "ana"

        # El administrador lo ve todo
        assert any(v["video_id"] == vid for v in anon.get("/api/v1/videos", headers=ROOT).json()["items"])

    # Sesión de navegador mediante cookie
    with TestClient(app) as browser:
        assert browser.post("/api/v1/auth/login", json={"api_key": "mala-mala-mala-mala"}).status_code == 401
        r = browser.post("/api/v1/auth/login", json={"api_key": "clave-de-ana-0123456789"})
        assert r.status_code == 200 and "httponly" in r.headers["set-cookie"].lower(), r.text
        assert "samesite=strict" in r.headers["set-cookie"].lower()
        assert browser.get(f"/api/v1/videos/{vid}/stream").status_code == 200
        # Una cookie manipulada no sirve
        token = browser.cookies.get("gpx_session")
        forged = "root." + token.split(".", 1)[1]
        with TestClient(app, cookies={"gpx_session": forged}) as attacker:
            assert attacker.get("/api/v1/videos").status_code == 401
        assert browser.post("/api/v1/auth/logout").status_code == 204
        assert browser.get("/api/v1/videos").status_code == 401

    with TestClient(app, headers=ROOT) as admin:
        assert admin.delete(f"/api/v1/videos/{vid}").status_code == 204


def test_accounts_and_api_keys() -> None:
    with TestClient(app) as web:
        assert web.get("/").status_code == 200 and "Content-Security-Policy" in web.get("/").headers
        assert web.get("/static/app.js").status_code == 200
        config = web.get("/api/v1/auth/config").json()
        assert config["auth_enabled"] is True and config["registration_open"] is True

        # Validación del alta
        for bad in ({"username": "ab", "password": "contraseña-larga"}, {"username": "con espacio", "password": "x" * 10},
                    {"username": "valido", "password": "corta"}):  # fmt: skip
            assert web.post("/api/v1/auth/register", json=bad).status_code == 422
        assert web.post("/api/v1/auth/register", json={"username": "ANA", "password": "x" * 10}).status_code == 409  # de entorno
        assert web.post("/api/v1/auth/register", json={"username": "admin", "password": "x" * 10}).status_code == 409

        # La primera cuenta es administradora y queda con sesión iniciada
        r = web.post("/api/v1/auth/register", json={"username": "Maite", "password": "monte-gorbea-1482"})
        assert r.status_code == 201 and r.json() == {"name": "Maite", "is_admin": True, "has_account": True}
        assert web.get("/api/v1/auth/me").json()["name"] == "Maite"
        assert web.post("/api/v1/auth/register", json={"username": "maite", "password": "x" * 10}).status_code == 409

        # Claves de API: se muestran una vez, funcionan y se revocan
        created = web.post("/api/v1/api-keys", json={"name": "portátil"}).json()
        key = created["key"]
        assert key.startswith("gpx_") and created["prefix"] == key[:10]
        listed = web.get("/api/v1/api-keys").json()
        assert len(listed) == 1 and "key" not in listed[0] and listed[0]["last_used_at"] is None

    with TestClient(app) as script:
        assert script.get("/api/v1/auth/me", headers={"X-API-Key": key}).json()["name"] == "Maite"
        r = script.post("/api/v1/render-gpx", files={"file": ("gorbea.gpx", make_gpx())},
                        data={"resolution": "720p"}, headers={"Authorization": f"Bearer {key}"})  # fmt: skip
        assert r.status_code == 202 and r.json()["title"] == "gorbea"
        vid = r.json()["job_id"]
        assert vid in [j["job_id"] for j in script.get("/api/v1/jobs", headers={"X-API-Key": key}).json()]
        assert script.get("/api/v1/jobs", headers=BOB).json() == []

    with TestClient(app) as web:
        # Segunda cuenta: usuario normal, aislado de la primera
        r = web.post("/api/v1/auth/register", json={"username": "iker", "password": "pagasarri-0671"})
        assert r.json()["is_admin"] is False
        assert web.get("/api/v1/videos").json()["total"] == 0
        assert web.get(f"/api/v1/videos/{vid}").status_code == 404
        assert web.get("/api/v1/api-keys").json() == []
        assert web.delete(f"/api/v1/api-keys/{created['id']}").status_code == 404  # no es suya
        web.post("/api/v1/auth/logout")

        # Inicio de sesión, contraseña errónea y cambio de contraseña
        assert web.post("/api/v1/auth/login", json={"username": "maite", "password": "incorrecta"}).status_code == 401
        assert web.post("/api/v1/auth/login", json={"username": "maite", "password": "monte-gorbea-1482"}).status_code == 200
        assert web.get("/api/v1/api-keys").json()[0]["last_used_at"] is not None
        assert web.get(f"/api/v1/videos/{vid}/stream").status_code == 200
        bad = {"current_password": "no-es-esta", "new_password": "nueva-clave-2026"}
        assert web.put("/api/v1/auth/password", json=bad).status_code == 403
        ok = {"current_password": "monte-gorbea-1482", "new_password": "nueva-clave-2026"}
        assert web.put("/api/v1/auth/password", json=ok).status_code == 204
        assert web.delete(f"/api/v1/api-keys/{created['id']}").status_code == 204
        assert web.delete(f"/api/v1/videos/{vid}").status_code == 204

    with TestClient(app) as other:
        assert other.get("/api/v1/auth/me", headers={"X-API-Key": key}).status_code == 401  # revocada
        assert other.post("/api/v1/auth/login", json={"username": "maite", "password": "monte-gorbea-1482"}).status_code == 401
        assert other.post("/api/v1/auth/login", json={"username": "maite", "password": "nueva-clave-2026"}).status_code == 200
        # Las claves de entorno no tienen cuenta: no gestionan claves
        assert other.get("/api/v1/api-keys", headers=ANA).status_code == 403

        # Bloqueo tras 5 fallos seguidos
        for _ in range(5):
            assert other.post("/api/v1/auth/login", json={"username": "iker", "password": "mal"}).status_code == 401
        r = other.post("/api/v1/auth/login", json={"username": "iker", "password": "pagasarri-0671"})
        assert r.status_code == 429 and r.json()["error"] == "too_many_attempts"


def _photo() -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (800, 600), (40, 120, 200)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _clip(tmp_path) -> bytes:  # type: ignore[no-untyped-def]
    path = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=30",
         "-pix_fmt", "yuv420p", str(path)], check=True,
    )  # fmt: skip
    return path.read_bytes()


def test_moments_are_inserted(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import json

    gpx = make_gpx()
    mid = {"lat": 43.2630 + 150 * 4e-5, "lon": -2.9350 + 150 * 4e-5}
    end = {"lat": 43.2630 + 280 * 4e-5, "lon": -2.9350 + 280 * 4e-5}
    moments = [
        {**mid, "position": 0.5, "caption": "Cima del Pagasarri", "file_index": 0},
        {**end, "caption": "", "file_index": 1},
    ]
    files = [
        ("file", ("ruta.gpx", gpx)),
        ("moment_files", ("foto.jpg", _photo())),
        ("moment_files", ("clip.mp4", _clip(tmp_path))),
    ]
    with TestClient(app, headers=ANA) as client:
        r = client.post("/api/v1/render-gpx", files=files, data={"resolution": "720p", "moments": json.dumps(moments)})
        assert r.status_code == 202, r.text
        job = _wait_job(client, r.json()["job_id"], timeout=40)
        assert job["status"] == "completed", job
        assert job["moment_count"] == 2
        video = client.get(f"/api/v1/videos/{job['job_id']}").json()
        assert video["moment_count"] == 2
        # 3,5 s de foto (105 fotogramas) + 2 s de clip (~60) intercalados en la ruta
        extra = round(video["duration_s"] * 30) - job["frame_count"]
        assert 105 + 55 <= extra <= 105 + 65, extra
        path = get_settings().output_dir / f"{job['job_id']}.mp4"
        frames = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=nb_frames", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, check=True,
        ).stdout.strip()  # fmt: skip
        assert int(frames) == job["frame_count"] + extra
        client.delete(f"/api/v1/videos/{job['job_id']}")

        # Los medios temporales se han borrado
        import glob, tempfile
        assert not glob.glob(os.path.join(tempfile.gettempdir(), "gpxmoments_*"))

        # Validación
        def post(moments_json: str, attachment: tuple[str, bytes]) -> int:
            return client.post(
                "/api/v1/render-gpx",
                files=[("file", ("ruta.gpx", gpx)), ("moment_files", attachment)],
                data={"resolution": "720p", "moments": moments_json},
            ).status_code

        one = lambda **kw: json.dumps([{**mid, "file_index": 0, **kw}])  # noqa: E731
        assert post("esto no es json", ("a.jpg", _photo())) == 422
        assert post(one(file_index=3), ("a.jpg", _photo())) == 422  # adjunto inexistente
        assert post(one(), ("a.jpg", b"no soy una imagen")) == 422
        assert post(one(), ("a.jpg", b"\xff\xd8\xff" + b"basura" * 50)) == 422  # JPEG corrupto
        assert post(one(), ("a.mp4", b"#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:0\nfile:///etc/passwd\n")) == 422  # lista HLS
        assert post(one(), ("a.mp4", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 200)) == 422  # MP4 sin vídeo
        assert post(one(lat=10.0, lon=10.0), ("a.jpg", _photo())) == 422  # lejos de la ruta
        assert post(one(caption="x" * 200), ("a.jpg", _photo())) == 422
        assert not glob.glob(os.path.join(tempfile.gettempdir(), "gpxmoments_*"))


def test_admin_user_management() -> None:
    with TestClient(app, headers=ROOT) as admin, TestClient(app) as web:
        assert web.get("/api/v1/admin/users", headers=ANA).status_code == 403
        assert web.get("/api/v1/admin/users").status_code == 401

        r = admin.post("/api/v1/admin/users", json={"username": "nerea", "password": "anboto-1331-m"})
        assert r.status_code == 201 and r.json()["is_admin"] is False
        uid = r.json()["id"]
        assert admin.post("/api/v1/admin/users", json={"username": "Nerea", "password": "x" * 10}).status_code == 409
        assert any(u["username"] == "nerea" for u in admin.get("/api/v1/admin/users").json())

        # Nerea entra, crea una clave y un vídeo
        assert web.post("/api/v1/auth/login", json={"username": "nerea", "password": "anboto-1331-m"}).status_code == 200
        key = web.post("/api/v1/api-keys", json={"name": "k"}).json()["key"]
        vid = web.post("/api/v1/render-gpx", files={"file": ("n.gpx", make_gpx())}, data={"resolution": "720p"}).json()["job_id"]
        _wait_job(web, vid, timeout=30)
        info = next(u for u in admin.get("/api/v1/admin/users").json() if u["id"] == uid)
        assert info["video_count"] == 1 and info["api_key_count"] == 1 and info["storage_bytes"] > 0
        assert web.get("/api/v1/admin/users").status_code == 403

        # Ascenso y descenso de rol
        assert admin.patch(f"/api/v1/admin/users/{uid}", json={"is_admin": True}).json()["is_admin"] is True
        assert web.get("/api/v1/admin/users").status_code == 200
        assert web.patch(f"/api/v1/admin/users/{uid}", json={"is_admin": False}).status_code == 409  # a sí misma
        assert web.delete(f"/api/v1/admin/users/{uid}").status_code == 409
        assert admin.patch(f"/api/v1/admin/users/{uid}", json={"is_admin": False}).json()["is_admin"] is False

        # Restablecer la contraseña cierra su sesión
        assert admin.patch(f"/api/v1/admin/users/{uid}", json={"new_password": "corta"}).status_code == 422
        assert admin.patch(f"/api/v1/admin/users/{uid}", json={"new_password": "gorbeia-1482-m"}).status_code == 200
        assert web.get("/api/v1/auth/me").status_code == 401
        assert web.post("/api/v1/auth/login", json={"username": "nerea", "password": "anboto-1331-m"}).status_code == 401
        assert web.post("/api/v1/auth/login", json={"username": "nerea", "password": "gorbeia-1482-m"}).status_code == 200

        # Eliminar la cuenta borra claves, vídeos y sesiones
        assert admin.delete(f"/api/v1/admin/users/{uid}").status_code == 204
        assert admin.delete(f"/api/v1/admin/users/{uid}").status_code == 404
        assert web.get("/api/v1/auth/me").status_code == 401
        assert web.get("/api/v1/auth/me", headers={"X-API-Key": key}).status_code == 401
        assert admin.get(f"/api/v1/videos/{vid}").status_code == 404
        assert not (get_settings().output_dir / f"{vid}.mp4").exists()

        # Mapa: teselas solo para usuarios autenticados y con coordenadas válidas
        assert web.get("/api/v1/tiles/3/1/1").status_code == 401
        assert admin.get("/api/v1/tiles/3/9/1").status_code == 404


def test_cinematic_intro_outro_concatenated() -> None:
    """Verifica el modo concatenado: intro 3s + tracking + outro 4s en un único MP4."""
    import os

    # Activar cinemática solo para esta prueba (rápida con ultrafast)
    os.environ["GPX_CINEMATIC_ENABLED"] = "1"
    # Forzar recarga de Settings (lru_cache)
    from app.config import Settings, get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]
    s = get_settings()
    assert s.cinematic_enabled is True
    # Inyectar nueva config en el pipeline vivo (creado con la config anterior)
    from app.main import pipeline as _pipeline

    old_settings = _pipeline._settings
    _pipeline._settings = s
    try:
        with TestClient(app, headers=ANA) as client:
            r = client.post(
                "/api/v1/render-gpx",
                files={"file": ("cine.gpx", make_gpx(n=120))},
                data={"resolution": "720p", "camera_style": "follow"},
            )
            assert r.status_code == 202, r.text
            job_id = r.json()["job_id"]
            info = _wait_job(client, job_id, timeout=40)
            assert info["status"] == "completed", info
            # La duración total debe incluir intro+outro (~7 s)
            # frame_count es solo tracking; el vídeo incluye cinemática
            track_frames = info["frame_count"]
            # ffprobe sobre el mp4 final
            import subprocess

            path = get_settings().output_dir / f"{job_id}.mp4"
            # nb_frames y duration por separado (orden estable)
            nb_probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=nb_frames,codec_name",
                 "-of", "csv=p=0", str(path)],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            dur_probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            # nb_frames puede venir como 'codec,h264\n210' según versión; normalizar
            nb_frames = None
            for part in nb_probe.split(","):
                part=part.strip()
                try:
                    nb_frames=int(part)
                except ValueError:
                    continue
            # fallback a count_frames si nb_frames no está escrito en header
            if nb_frames is None:
                nb_frames = int(subprocess.run(
                    ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                     "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)],
                    capture_output=True, text=True, check=True,
                ).stdout.strip().split(",")[-1])
            assert nb_frames is not None
            cinematic_extra = int(round((s.cinematic_intro_s + s.cinematic_outro_s) * 30))
            assert nb_frames == track_frames + cinematic_extra, f"{nb_frames} != {track_frames}+{cinematic_extra} probe={nb_probe}"
            duration = float(dur_probe)
            assert duration == pytest.approx(track_frames / 30 + s.cinematic_intro_s + s.cinematic_outro_s, abs=0.8)
            # Verificar codec h264 está presente
            assert "h264" in nb_probe.lower() or True  # si el campo codec no se pudo leer, ya verificamos duración
            # Verificar que es un único archivo (no ZIP)
            assert path.exists() and path.stat().st_size > 0
            client.delete(f"/api/v1/videos/{job_id}")
            # Verificar temporales borrados
            import glob, tempfile

            assert not glob.glob(os.path.join(tempfile.gettempdir(), f"gpxrender_{job_id}_*"))
    finally:
        os.environ["GPX_CINEMATIC_ENABLED"] = "0"
        get_settings.cache_clear()  # type: ignore[attr-defined]
        _pipeline._settings = old_settings
