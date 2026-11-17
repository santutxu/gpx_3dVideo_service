"""Control de acceso: cuentas, claves de API, sesiones de navegador y propiedad de los recursos.

Formas de identificarse:
  * **Cuenta de usuario** (nombre + contraseña) desde la web: ``POST /api/v1/auth/login``
    devuelve una cookie de sesión firmada (HMAC-SHA256), ``HttpOnly`` y ``SameSite=Strict``.
  * **Clave de API** creada por el usuario en la web, enviada como
    ``Authorization: Bearer <clave>`` o ``X-API-Key: <clave>``.
  * **Claves estáticas** opcionales definidas por entorno
    (``GPX_API_KEYS="ana:clave1,root:clave2:admin"``), útiles para automatizaciones.

Cada trabajo y cada vídeo pertenece a quien lo creó. Los demás usuarios reciben
404 (no se revela su existencia); los administradores lo ven todo. La primera
cuenta registrada es administradora.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import Depends, Request
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from .config import Settings, get_settings
from .exceptions import GPXRenderError
from .users import UserStore, verify_password

logger = logging.getLogger(__name__)

SESSION_COOKIE = "gpx_session"
MIN_KEY_LENGTH = 16
RESERVED_USERNAMES = frozenset({"anonymous", "admin", "root", "system"})


class AuthenticationError(GPXRenderError):
    """Faltan credenciales o no son válidas."""

    status_code = 401
    error_code = "unauthorized"


class ForbiddenError(GPXRenderError):
    """La operación no está permitida para este usuario o en esta configuración."""

    status_code = 403
    error_code = "forbidden"


class TooManyAttemptsError(GPXRenderError):
    """Demasiados intentos fallidos de inicio de sesión."""

    status_code = 429
    error_code = "too_many_attempts"


class AuthConfigurationError(RuntimeError):
    """La configuración de claves es inválida (se detecta al arrancar)."""


@dataclass(frozen=True)
class Principal:
    """Usuario autenticado."""

    name: str
    is_admin: bool = False
    #: Identificador de la cuenta en la base de datos (None para claves estáticas de entorno).
    user_id: int | None = None

    def can_access(self, owner: str | None) -> bool:
        """¿Puede este usuario acceder a un recurso del propietario indicado?

        Los recursos sin propietario (anteriores al control de acceso) solo son
        visibles para administradores.
        """
        return self.is_admin or (owner is not None and owner.lower() == self.name.lower())


class LoginRateLimiter:
    """Límite de intentos fallidos por clave (IP + usuario) en una ventana deslizante."""

    def __init__(self, max_failures: int = 5, window_s: float = 300.0) -> None:
        self._max, self._window = max_failures, window_s
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        """Lanza :class:`TooManyAttemptsError` si la clave está bloqueada."""
        now = time.monotonic()
        with self._lock:
            attempts = self._failures.get(key)
            if not attempts:
                return
            while attempts and now - attempts[0] > self._window:
                attempts.popleft()
            if not attempts:
                del self._failures[key]
            elif len(attempts) >= self._max:
                wait = int(self._window - (now - attempts[0])) + 1
                raise TooManyAttemptsError(f"Demasiados intentos fallidos. Vuelve a probar en {wait} s.")

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._failures[key].append(time.monotonic())

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


class AuthManager:
    """Valida credenciales y emite/verifica sesiones firmadas."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.enabled = not settings.auth_disabled
        self.users = UserStore(settings.db_path)
        self.limiter = LoginRateLimiter()
        self._static: dict[bytes, Principal] = {}  # hash de clave de entorno → usuario
        if settings.api_keys.strip():
            self._load_static_keys(settings.api_keys)
        self._secret = _digest("gpx-session:" + (settings.session_secret or self.users.session_secret()))
        if not self.enabled:
            logger.warning("Autenticación DESACTIVADA (GPX_AUTH_DISABLED): cualquiera puede usar la API.")

    def _load_static_keys(self, raw: str) -> None:
        names: set[str] = set()
        for entry in filter(None, (e.strip() for e in raw.split(","))):
            parts = entry.split(":")
            if len(parts) not in (2, 3) or (len(parts) == 3 and parts[2] != "admin"):
                raise AuthConfigurationError("GPX_API_KEYS: cada entrada debe ser 'usuario:clave' o 'usuario:clave:admin'.")
            name, key = parts[0].strip(), parts[1].strip()
            if not name or not name.replace("-", "").replace("_", "").isalnum():
                raise AuthConfigurationError(f"GPX_API_KEYS: nombre de usuario inválido ('{name}').")
            if name.lower() in names:
                raise AuthConfigurationError(f"GPX_API_KEYS: usuario duplicado ('{name}').")
            if len(key) < MIN_KEY_LENGTH:
                raise AuthConfigurationError(
                    f"GPX_API_KEYS: la clave de '{name}' debe tener al menos {MIN_KEY_LENGTH} caracteres."
                )
            names.add(name.lower())
            self._static[_digest(key)] = Principal(name=name, is_admin=len(parts) == 3)

    # ------------------------------------------------------------------ #
    def is_name_reserved(self, username: str) -> bool:
        """Nombres que no se pueden registrar: reservados o usados por claves de entorno."""
        lowered = username.lower()
        return lowered in RESERVED_USERNAMES or any(p.name.lower() == lowered for p in self._static.values())

    def _static_principal(self, name: str) -> Principal | None:
        return next((p for p in self._static.values() if p.name.lower() == name.lower()), None)

    def authenticate_key(self, key: str) -> Principal | None:
        """Usuario de una clave de API (de entorno o creada en la web), o ``None``."""
        candidate = _digest(key)
        found: Principal | None = None
        for stored, principal in self._static.items():  # tiempo constante, sin salida anticipada
            if hmac.compare_digest(stored, candidate):
                found = principal
        if found is not None:
            return found
        user = self.users.user_for_api_key(key)
        return Principal(user.username, user.is_admin, user.id) if user else None

    def authenticate_password(self, username: str, password: str) -> Principal | None:
        """Valida usuario y contraseña. Tarda lo mismo exista o no la cuenta."""
        user = self.users.get_user(username)
        # Hash ficticio: evita revelar por tiempo de respuesta si el usuario existe.
        stored = user.password_hash if user else "scrypt$16384$8$1$AAAAAAAAAAAAAAAAAAAAAA==$" + "A" * 43 + "="
        if not verify_password(password, stored) or user is None:
            return None
        return Principal(user.username, user.is_admin, user.id)

    def _sign(self, body: str) -> str:
        mac = hmac.new(self._secret, body.encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(mac).decode().rstrip("=")

    def _fingerprint(self, name: str) -> str:
        """Huella de la contraseña actual: al cambiarla, las sesiones anteriores dejan de ser válidas."""
        user = self.users.get_user(name)
        return hashlib.sha256(user.password_hash.encode()).hexdigest()[:16] if user else ""

    def create_session(self, principal: Principal) -> str:
        """Token de sesión firmado: ``usuario.caducidad.firma``."""
        body = f"{principal.name}.{int(time.time()) + self._settings.session_ttl_s}"
        return f"{body}.{self._sign(body + '|' + self._fingerprint(principal.name))}"

    def verify_session(self, token: str) -> Principal | None:
        """Valida firma y caducidad, que la cuenta siga existiendo y que no haya cambiado la contraseña."""
        try:
            name, expires, signature = token.split(".")
            if int(expires) < time.time():
                return None
        except ValueError:
            return None
        user = self.users.get_user(name)
        fingerprint = hashlib.sha256(user.password_hash.encode()).hexdigest()[:16] if user else ""
        if not hmac.compare_digest(signature, self._sign(f"{name}.{expires}|{fingerprint}")):
            return None
        if user is not None:
            return Principal(user.username, user.is_admin, user.id)
        return self._static_principal(name)


_manager: AuthManager | None = None


def get_auth_manager() -> AuthManager:
    """Gestor de autenticación (singleton perezoso)."""
    global _manager  # noqa: PLW0603
    if _manager is None:
        _manager = AuthManager(get_settings())
    return _manager


# auto_error=False: la respuesta 401 la genera require_user con un formato homogéneo.
_bearer = HTTPBearer(auto_error=False, description="Clave de API")
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False, description="Clave de API")


def require_user(
    request: Request,
    bearer: HTTPAuthorizationCredentials | None = Depends(_bearer),
    header_key: str | None = Depends(_api_key_header),
) -> Principal:
    """Dependencia de FastAPI: exige un usuario autenticado.

    Orden de comprobación: ``Authorization: Bearer`` → ``X-API-Key`` → cookie de sesión.
    Es síncrona a propósito: consulta SQLite y FastAPI la ejecuta en su threadpool.

    Raises:
        AuthenticationError: 401 si no hay credenciales válidas.
    """
    manager = get_auth_manager()
    if not manager.enabled:
        return Principal(name="anonymous", is_admin=True)

    key = bearer.credentials if bearer else header_key
    if key:
        principal = manager.authenticate_key(key)
        if principal is None:
            raise AuthenticationError("Clave de API no válida.")
        return principal

    token = request.cookies.get(SESSION_COOKIE)
    if token and (principal := manager.verify_session(token)) is not None:
        return principal
    raise AuthenticationError("Se requiere autenticación: inicia sesión o envía una clave de API.")


def require_admin(user: Principal = Depends(require_user)) -> Principal:
    """Dependencia de FastAPI: exige un administrador (403 en caso contrario)."""
    if not user.is_admin:
        raise ForbiddenError("Esta operación está reservada a los administradores.")
    return user
