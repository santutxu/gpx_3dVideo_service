"""Modelos Pydantic: opciones de entrada, métricas, payload de render y estado de trabajos."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Resolution(str, Enum):
    """Resoluciones de salida soportadas."""

    P720 = "720p"
    P1080 = "1080p"

    @property
    def size(self) -> tuple[int, int]:
        """Dimensiones ``(ancho, alto)`` en píxeles."""
        return (1280, 720) if self is Resolution.P720 else (1920, 1080)


class CameraStyle(str, Enum):
    """Estilos de cámara disponibles para la animación."""

    FOLLOW = "follow"  # Persecución: detrás del punto activo, orientada según el rumbo
    ORBIT = "orbit"  # Órbita lenta alrededor del punto activo
    OVERHEAD = "overhead"  # Vista cenital con el norte arriba


class Orientation(str, Enum):
    """Orientación del lienzo de salida."""

    HORIZONTAL = "horizontal"  # 16:9 paisaje
    VERTICAL = "vertical"  # 9:16 retrato para redes sociales (Reels/TikTok/Shorts)


class RenderOptions(BaseModel):
    """Parámetros opcionales de la animación."""

    model_config = ConfigDict(frozen=True)

    animation_speed: float = Field(
        default=1.0, ge=0.5, le=4.0, description="Multiplicador de velocidad (2.0 = vídeo la mitad de largo)."
    )
    resolution: Resolution = Resolution.P1080
    camera_style: CameraStyle = CameraStyle.FOLLOW
    orientation: Orientation = Orientation.HORIZONTAL

    def dimensions(self) -> tuple[int, int]:
        """(ancho, alto) efectivos según resolución + orientación."""
        w, h = self.resolution.size
        if self.orientation is Orientation.VERTICAL:
            w, h = h, w  # 1280x720 -> 720x1280, 1920x1080 -> 1080x1920
            # asegurar par para yuv420p (ya lo son, pero por si se añaden otras resoluciones)
            w, h = (w // 2) * 2, (h // 2) * 2
        return w, h


class MomentSpec(BaseModel):
    """Momento pedido por el usuario: un punto de la ruta con una foto o un clip adjunto."""

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    position: float | None = Field(
        default=None, ge=0, le=1, description="Fracción de la distancia total; desambigua rutas de ida y vuelta."
    )
    caption: str = Field(default="", max_length=80, description="Pie de foto opcional.")
    file_index: int = Field(ge=0, description="Posición del archivo dentro del campo 'moment_files'.")


class TrackMetrics(BaseModel):
    """Métricas globales del track."""

    point_count: int = Field(description="Puntos válidos tras la limpieza.")
    total_distance_m: float
    elevation_gain_m: float
    elevation_loss_m: float
    min_elevation_m: float
    max_elevation_m: float
    duration_s: float = Field(description="Duración total (real si hay marcas de tiempo; estimada si no).")
    moving_time_s: float
    duration_is_estimated: bool
    avg_speed_kmh: float = Field(description="Distancia / duración total.")
    avg_moving_speed_kmh: float = Field(description="Distancia / tiempo en movimiento.")
    has_elevation: bool
    has_timestamps: bool
    outliers_corrected: int = Field(description="Saltos GPS detectados y corregidos.")
    start_time: datetime | None = None


class FramePoint(BaseModel):
    """Estado de la escena en un fotograma concreto."""

    i: int = Field(description="Índice de fotograma (base 0).")
    t: float = Field(description="Tiempo relativo dentro del vídeo (s).")
    elapsed: float = Field(description="Tiempo transcurrido de la actividad real (s).")
    lat: float
    lon: float
    ele: float
    x: float = Field(description="Este local (m) respecto al origen.")
    y: float = Field(description="Norte local (m) respecto al origen.")
    z: float = Field(description="Altitud (m).")
    dist: float = Field(description="Distancia acumulada (m).")
    heading: float = Field(description="Rumbo suavizado (grados, 0 = norte, sentido horario).")
    cam: tuple[float, float, float] = Field(description="Posición de cámara sugerida (x, y, z) en metros locales.")


class CameraConfig(BaseModel):
    """Parámetros de cámara que el motor gráfico debe aplicar."""

    style: CameraStyle
    distance_m: float
    pitch_deg: float
    fov_deg: float = 45.0
    orbit_deg_per_s: float = 0.0
    vertical_exaggeration: float = 1.5


class RenderPayload(BaseModel):
    """Contrato JSON entre el backend y el motor gráfico headless."""

    schema_version: str = "1.0"
    job_id: str
    width: int
    height: int
    fps: int
    frame_count: int
    duration_s: float
    frame_pattern: str = Field(default="frame_%04d.png", description="Nombre esperado de cada fotograma.")
    origin: dict[str, float] = Field(description="Origen (lat, lon) de la proyección local en metros.")
    bounds: dict[str, float]
    camera: CameraConfig
    metrics: TrackMetrics
    frames: list[FramePoint]


class JobStatus(str, Enum):
    """Fases del ciclo de vida de un trabajo de render."""

    QUEUED = "queued"
    RENDERING = "rendering"
    COMPILING = "compiling"
    COMPLETED = "completed"
    FAILED = "failed"


class JobInfo(BaseModel):
    """Estado público de un trabajo."""

    job_id: str
    title: str = Field(description="Nombre del GPX original (sin extensión).")
    status: JobStatus
    progress: float = Field(ge=0.0, le=1.0)
    options: RenderOptions
    metrics: TrackMetrics
    frame_count: int
    video_duration_s: float = Field(description="Duración prevista del vídeo, momentos incluidos.")
    moment_count: int = 0
    created_at: datetime
    updated_at: datetime
    error: str | None = None
    status_url: str
    video_url: str | None = None


class StoredVideo(BaseModel):
    """Metadatos persistidos junto a cada MP4 (archivo ``<id>.json``)."""

    video_id: str
    owner: str | None = Field(default=None, description="Usuario que lo creó (None: anterior al control de acceso).")
    title: str = Field(description="Nombre del GPX original (sin extensión).")
    created_at: datetime
    size_bytes: int
    duration_s: float
    width: int
    height: int
    moment_count: int = 0
    options: RenderOptions
    metrics: TrackMetrics


class VideoInfo(StoredVideo):
    """Vídeo de la biblioteca con sus enlaces."""

    stream_url: str = Field(description="Reproducción en el navegador (admite peticiones Range).")
    download_url: str = Field(description="Descarga como archivo adjunto.")


class VideoList(BaseModel):
    """Página del listado de vídeos."""

    total: int
    limit: int
    offset: int
    items: list[VideoInfo]


_USERNAME = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_-]+$", description="Letras, números, - y _")
_PASSWORD = Field(min_length=8, max_length=128)


class RegisterRequest(BaseModel):
    """Alta de una cuenta."""

    username: str = _USERNAME
    password: str = _PASSWORD


class LoginRequest(BaseModel):
    """Inicio de sesión con usuario y contraseña, o con una clave de API."""

    username: str | None = Field(default=None, max_length=32)
    password: str | None = Field(default=None, max_length=128)
    api_key: str | None = Field(default=None, max_length=512)


class PasswordChange(BaseModel):
    """Cambio de contraseña."""

    current_password: str = Field(max_length=128)
    new_password: str = _PASSWORD


class UserInfo(BaseModel):
    """Usuario autenticado."""

    name: str
    is_admin: bool
    has_account: bool = Field(default=False, description="Cuenta registrada (puede gestionar claves y contraseña).")


class AuthConfig(BaseModel):
    """Configuración pública que necesita la pantalla de acceso."""

    auth_enabled: bool
    registration_open: bool
    map_attribution: str = ""
    max_moments: int = 0


class AdminUserCreate(RegisterRequest):
    """Alta de una cuenta por un administrador."""

    is_admin: bool = False


class AdminUserUpdate(BaseModel):
    """Cambios que un administrador puede aplicar a una cuenta."""

    is_admin: bool | None = None
    new_password: str | None = Field(default=None, min_length=8, max_length=128)


class AdminUserInfo(BaseModel):
    """Cuenta de usuario vista por un administrador."""

    id: int
    username: str
    is_admin: bool
    created_at: datetime
    video_count: int
    api_key_count: int
    storage_bytes: int


class ApiKeyCreate(BaseModel):
    """Datos para crear una clave de API."""

    name: str = Field(min_length=1, max_length=60, description="Etiqueta para reconocerla (p. ej. 'portátil').")


class ApiKeyInfo(BaseModel):
    """Clave de API (sin su valor)."""

    id: int
    name: str
    prefix: str = Field(description="Primeros caracteres, para identificarla.")
    created_at: datetime
    last_used_at: datetime | None


class ApiKeyCreated(ApiKeyInfo):
    """Clave recién creada: única ocasión en la que se devuelve el valor completo."""

    key: str


class ErrorResponse(BaseModel):
    """Cuerpo estándar de error."""

    error: str
    message: str
