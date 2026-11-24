"""Configuración centralizada del servicio (sobrescribible por variables de entorno)."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


def _clean_env(name: str, default: str | None = None) -> str | None:
    """Lee env var y limpia comentarios inline '#', comillas y espacios."""
    val = os.environ.get(name)
    if val is None:
        return default
    # systemd EnvironmentFile y .env a mano: soportar "VAL # comentario" y "VAL" con comillas
    val = val.split("#", 1)[0].strip().strip('"').strip("'").strip()
    return val if val != "" else default


def _env_int(name: str, default: int) -> int:
    raw = _clean_env(name)
    if raw is None:
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = _clean_env(name)
    if raw is None:
        return default
    return float(raw)


def _env_str(name: str, default: str) -> str:
    raw = _clean_env(name)
    return raw if raw is not None else default


def _env_bool(name: str, default: bool) -> bool:
    raw = _clean_env(name)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    """Parámetros de funcionamiento. Todas las variables de entorno usan el prefijo ``GPX_``."""

    # --- Ingesta -----------------------------------------------------------
    max_upload_bytes: int = field(default_factory=lambda: _env_int("GPX_MAX_UPLOAD_BYTES", 25 * 1024 * 1024))
    max_track_points: int = field(default_factory=lambda: _env_int("GPX_MAX_TRACK_POINTS", 500_000))

    # --- Suavizado ---------------------------------------------------------
    #: Ventana (nº de muestras, impar) del filtro de mediana/Hampel anti-saltos.
    outlier_window: int = 7
    #: Desviación mínima (m) respecto a la mediana local para considerar un punto como salto GPS.
    outlier_min_deviation_m: float = 25.0
    #: Ventana (impar) del filtro Savitzky-Golay aplicado a la posición horizontal.
    position_smooth_window: int = 11
    #: Ventana (impar) del filtro Savitzky-Golay aplicado a la altitud (más ruidosa).
    elevation_smooth_window: int = 21
    #: Histéresis (m) para acumular desnivel: ignora oscilaciones menores.
    elevation_gain_threshold_m: float = 3.0
    #: Por debajo de esta velocidad (m/s) el tramo se considera parado.
    moving_speed_threshold_ms: float = 0.5

    # --- Animación ---------------------------------------------------------
    fps: int = 30  # Requisito: 30 FPS
    seconds_per_km: float = field(default_factory=lambda: _env_float("GPX_SECONDS_PER_KM", 4.0))
    min_video_seconds: float = field(default_factory=lambda: _env_float("GPX_MIN_VIDEO_SECONDS", 20.0))
    max_video_seconds: float = field(default_factory=lambda: _env_float("GPX_MAX_VIDEO_SECONDS", 90.0))

    # --- Cinemática (intro/outro) ----------------------------------------
    #: Si es True cada vídeo lleva intro (ruta blanca) + tracking + outro (ruta naranja + stats) concatenados.
    cinematic_enabled: bool = field(default_factory=lambda: _env_bool("GPX_CINEMATIC_ENABLED", True))
    cinematic_intro_s: float = field(default_factory=lambda: _env_float("GPX_CINEMATIC_INTRO_S", 3.0))
    cinematic_outro_s: float = field(default_factory=lambda: _env_float("GPX_CINEMATIC_OUTRO_S", 4.0))

    # --- Momentos (fotos y clips intercalados) ------------------------------
    max_moments: int = field(default_factory=lambda: _env_int("GPX_MAX_MOMENTS", 12))
    max_moment_file_bytes: int = field(default_factory=lambda: _env_int("GPX_MAX_MOMENT_FILE_BYTES", 100 * 1024 * 1024))
    #: Segundos que permanece en pantalla una foto.
    moment_image_seconds: float = field(default_factory=lambda: _env_float("GPX_MOMENT_IMAGE_SECONDS", 3.5))
    #: Los clips más largos se recortan a esta duración.
    moment_clip_max_seconds: float = field(default_factory=lambda: _env_float("GPX_MOMENT_CLIP_MAX_SECONDS", 10.0))

    # --- Motor de render ---------------------------------------------------
    #: ``terrain3d`` (relieve + satélite con OpenGL headless), ``simulated`` (2D, sin red ni OpenGL)
    #: o ``node`` (worker Three.js externo).
    render_engine: str = field(default_factory=lambda: _env_str("GPX_RENDER_ENGINE", "terrain3d"))
    node_binary: str = field(default_factory=lambda: _env_str("GPX_NODE_BINARY", "node"))
    node_worker_script: str = field(default_factory=lambda: _env_str("GPX_NODE_WORKER_SCRIPT", "render_worker/worker.js"))
    render_timeout_s: int = field(default_factory=lambda: _env_int("GPX_RENDER_TIMEOUT_S", 3600))
    #: Renders simultáneos. Es el tamaño del executor propio del pipeline: los trabajos
    #: esperan en su cola sin ocupar hilos del threadpool general de Starlette.
    max_concurrent_renders: int = field(default_factory=lambda: _env_int("GPX_MAX_CONCURRENT_RENDERS", 2))
    #: Gracia al apagar el servicio: segundos que se espera a los renders en curso antes de
    #: marcarlos como fallidos y borrar su MP4 parcial.
    shutdown_grace_s: float = field(default_factory=lambda: _env_float("GPX_SHUTDOWN_GRACE_S", 5.0))
    #: Trabajos admitidos a la vez en todo el servicio (en curso + en cola). 0 = sin límite.
    max_queued_renders: int = field(
        default_factory=lambda: _env_int("GPX_MAX_QUEUED_RENDERS", 4 * _env_int("GPX_MAX_CONCURRENT_RENDERS", 2))
    )

    # --- Cuotas por usuario -------------------------------------------------
    #: Trabajos en curso (sin terminar) que puede tener abiertos una misma cuenta. 0 = sin límite.
    max_jobs_per_user: int = field(default_factory=lambda: _env_int("GPX_MAX_JOBS_PER_USER", 3))
    #: Bytes de vídeo que puede acumular una cuenta en ``GPX_OUTPUT_DIR``. 0 = sin límite.
    max_storage_bytes_per_user: int = field(default_factory=lambda: _env_int("GPX_MAX_STORAGE_BYTES_PER_USER", 0))

    # --- Terreno 3D (motor ``terrain3d``) ------------------------------------
    #: Teselas de elevación en codificación *terrarium* (plantilla con {z}/{x}/{y}).
    dem_url: str = field(
        default_factory=lambda: _env_str(
            "GPX_DEM_URL", "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
        )
    )
    #: Teselas de imagen satélite XYZ (plantilla con {z}/{x}/{y}).
    imagery_url: str = field(
        default_factory=lambda: _env_str(
            "GPX_IMAGERY_URL",
            "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        )
    )
    imagery_attribution: str = field(
        default_factory=lambda: _env_str(
            "GPX_IMAGERY_ATTRIBUTION", "Imagery © Esri, Maxar, Earthstar Geographics · Terrain: Mapzen/AWS"
        )
    )
    imagery_max_px: int = field(default_factory=lambda: _env_int("GPX_IMAGERY_MAX_PX", 6144))
    imagery_max_zoom: int = 17
    dem_max_px: int = 1024
    dem_max_zoom: int = 14
    #: Vértices por lado (máx.) de la malla del terreno.
    terrain_mesh_max: int = field(default_factory=lambda: _env_int("GPX_TERRAIN_MESH_MAX", 480))
    terrain_margin_ratio: float = 0.4
    terrain_min_margin_m: float = 1500.0
    max_tiles_per_layer: int = 700
    tile_download_workers: int = 12
    tile_timeout_s: float = 20.0
    tile_cache_dir: Path = field(
        default_factory=lambda: Path(
            _env_str("GPX_TILE_CACHE_DIR", str(Path(tempfile.gettempdir()) / "gpx_render_tile_cache"))
        )
    )

    # --- FFmpeg ------------------------------------------------------------
    ffmpeg_binary: str = field(default_factory=lambda: _env_str("GPX_FFMPEG_BINARY", "ffmpeg"))
    ffmpeg_crf: int = field(default_factory=lambda: _env_int("GPX_FFMPEG_CRF", 20))
    ffmpeg_preset: str = field(default_factory=lambda: _env_str("GPX_FFMPEG_PRESET", "medium"))
    ffmpeg_timeout_s: int = field(default_factory=lambda: _env_int("GPX_FFMPEG_TIMEOUT_S", 1800))
    #: Máximo que el compilador en streaming espera a que FFmpeg acepte un fotograma
    #: (o cierre el archivo) antes de darlo por atascado y matarlo. Ver ``StreamingVideoCompiler``.
    ffmpeg_stall_timeout_s: float = field(default_factory=lambda: _env_float("GPX_FFMPEG_STALL_TIMEOUT_S", 180.0))

    # --- Control de acceso -------------------------------------------------
    #: ``usuario:clave[,usuario2:clave2[:admin]]``. Vacío → se genera una clave de administrador.
    api_keys: str = field(default_factory=lambda: _env_str("GPX_API_KEYS", ""))
    auth_disabled: bool = field(default_factory=lambda: _env_bool("GPX_AUTH_DISABLED", False))
    #: Secreto para firmar las sesiones; por defecto se deriva de las claves.
    session_secret: str = field(default_factory=lambda: _env_str("GPX_SESSION_SECRET", ""))
    session_ttl_s: int = field(default_factory=lambda: _env_int("GPX_SESSION_TTL_S", 12 * 3600))
    #: Permitir que cualquiera cree una cuenta desde la web.
    allow_registration: bool = field(default_factory=lambda: _env_bool("GPX_ALLOW_REGISTRATION", True))
    #: ``strict`` (recomendado), ``lax`` o ``none`` (solo si la web se incrusta en un iframe de otro sitio).
    cookie_samesite: str = field(default_factory=lambda: _env_str("GPX_COOKIE_SAMESITE", "strict").lower())
    #: Forzar el atributo Secure de la cookie (p. ej. detrás de un proxy TLS). Por defecto: según el esquema.
    cookie_secure: bool = field(default_factory=lambda: _env_bool("GPX_COOKIE_SECURE", False))
    #: Base de datos SQLite de usuarios y claves.
    db_path: Path = field(
        default_factory=lambda: Path(
            _env_str(
                "GPX_DB_PATH",
                str(Path(_env_str("GPX_OUTPUT_DIR", str(Path(tempfile.gettempdir()) / "gpx_render_output"))) / "gpx_render.db"),
            )
        )
    )

    # --- Almacenamiento ----------------------------------------------------
    output_dir: Path = field(
        default_factory=lambda: Path(_env_str("GPX_OUTPUT_DIR", str(Path(tempfile.gettempdir()) / "gpx_render_output")))
    )

    # --- Polar AccessLink --------------------------------------------------
    polar_enabled: bool = field(default_factory=lambda: _env_bool("GPX_POLAR_ENABLED", False))
    polar_client_id: str = field(default_factory=lambda: _env_str("GPX_POLAR_CLIENT_ID", ""))
    polar_client_secret: str = field(default_factory=lambda: _env_str("GPX_POLAR_CLIENT_SECRET", ""))
    polar_redirect_url: str = field(default_factory=lambda: _env_str("GPX_POLAR_REDIRECT_URL", ""))
    polar_max_sync: int = field(default_factory=lambda: _env_int("GPX_POLAR_MAX_SYNC", 20))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Devuelve la configuración (singleton perezoso)."""
    return Settings()
