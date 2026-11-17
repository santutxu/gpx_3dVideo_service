# GPX 3D Render Service

Backend FastAPI que convierte un `.gpx` en un vídeo MP4 animado (estilo Relive).

```
POST /api/v1/render-gpx ─▶ gpx_processor ─▶ renderer_orchestrator ─▶ video_compiler ─▶ MP4
   (valida, 202 + job)     (limpia/suaviza)   (payload JSON → PNGs)     (FFmpeg/libx264)
```

## Estructura

| Archivo | Responsabilidad |
|---|---|
| `app/main.py` | API FastAPI, validación de subida, background tasks |
| `app/gpx_processor.py` | Parsing, métricas, filtro anti-saltos, suavizado, remuestreo por fotograma |
| `app/renderer_orchestrator.py` | Payload JSON, motores `simulated` / `node`, verificación de fotogramas |
| `app/terrain.py`, `app/terrain_engine.py` | Descarga de relieve + satélite y render 3D con OpenGL headless |
| `app/video_compiler.py` | `subprocess` + FFmpeg (libx264, yuv420p, 30 FPS), por lotes o en streaming, limpieza |
| `app/auth.py`, `app/users.py` | Cuentas (SQLite), claves de API, sesiones firmadas y propiedad de los recursos |
| `app/static/` | Aplicación web (acceso, panel de rutas, subida, claves) |
| `app/video_library.py` | Catálogo persistente de vídeos |
| `app/jobs.py` | Registro de trabajos y pipeline en segundo plano con directorio temporal |
| `app/schemas.py`, `config.py`, `exceptions.py` | Modelos Pydantic, configuración, errores de dominio |

## Uso

```bash
pip install -r requirements.txt          # + FFmpeg en el PATH
uvicorn app.main:app --host 0.0.0.0 --port 8000

curl -H "X-API-Key: <clave>" -F file=@ruta.gpx -F resolution=720p -F camera_style=orbit -F animation_speed=1.5 \
     http://localhost:8000/api/v1/render-gpx        # → 202 {job_id, metrics, status_url}
curl http://localhost:8000/api/v1/jobs/<job_id>      # estado y progreso
curl -O http://localhost:8000/api/v1/jobs/<job_id>/video
```

Parámetros: `animation_speed` 0.5–4.0, `resolution` `720p|1080p`, `camera_style` `follow|orbit|overhead`.
Otros endpoints: `GET /api/v1/jobs/{id}/track` (puntos interpolados), `GET /health`, `/docs`.

Pruebas y demo sin servidor:

```bash
pip install -r requirements-dev.txt && pytest -q
python demo.py [ruta.gpx] [follow|orbit|overhead] [720p|1080p]   # genera demo_output.mp4
```

## Gestión de vídeos

Cada vídeo terminado se guarda en `GPX_OUTPUT_DIR` junto a un `<id>.json` con sus metadatos, por lo
que la biblioteca sobrevive a los reinicios del servidor. El `video_id` es el `job_id` del render.

| Método y ruta | Función |
|---|---|
| `GET /api/v1/videos?limit=50&offset=0` | Listar (más recientes primero) |
| `GET /api/v1/videos/{id}` | Metadatos: título, métricas, tamaño, duración, enlaces |
| `GET /api/v1/videos/{id}/stream` | Ver en el navegador (admite `Range`) |
| `GET /api/v1/videos/{id}/download` | Descargar como adjunto con el nombre del GPX |
| `DELETE /api/v1/videos/{id}` | Borrar vídeo y metadatos (204) |

```bash
curl http://localhost:8000/api/v1/videos
curl -OJ http://localhost:8000/api/v1/videos/<id>/download
curl -X DELETE http://localhost:8000/api/v1/videos/<id>
```

## Aplicación web

La interfaz de usuario "Cumbre" (en `app/static/`) es un SPA en español, con tema oscuro y acento naranja `#ff5c1e`. No usa dependencias externas; el DOM se construye con `textContent`.

Vistas:
- **Rutas** (`/`): panel con totales, progreso de trabajos (sondeo cada 2 s) y tarjetas de vídeo (reproductor, métricas, descarga, eliminación).
- **Nuevo vídeo** (`/upload`): arrastrar y soltar el GPX, elegir cámara, resolución y velocidad. **Momentos**: mapa satélite 2D con la ruta dibujada; al hacer clic se elige un punto (se ajusta a la ruta) y se adjunta una foto o clip (máximo 12 momentos, 100 MB cada uno). Se muestran en la tarjeta del vídeo.
- **Claves de API** (`/keys`): crear, listar, eliminar.
- **Cuenta** (`/account`): cambiar contraseña, ver datos.
- **Usuarios** (`/users`, **solo administradores**): listar, crear, cambiar rol, restablecer contraseña, eliminar.

## Control de acceso

- Claves de API (`GPX_API_KEYS` o generadas).
- Sesión firmada (cookie `gpx_session`, HttpOnly, 12 h, `SameSite=None` para el proxy).
- Registro abierto o cerrado (`GPX_ALLOW_REGISTRATION`).
- **Cambio de contraseña invalida las sesiones activas** (la firma incluye el hash).
- **Administradores** pueden gestionar cuentas, ver todos los vídeos y eliminar recursos.

## Límites y advertencias

- Los trabajos en curso se mantienen en memoria; el servidor se reinicia si se apaga el proceso.
- El vídeo final se guarda en `GPX_OUTPUT_DIR` (por defecto `/tmp/gpx_render_output`).
- Máximo 12 momentos por vídeo; 100 MB por archivo adjunto; foto 3,5 s, clip hasta 10 s.
- El mapa usa teselas satélite de Esri (atribución en la interfaz) servidas por el servidor (proxy autenticado).
- No hay recuperación de contraseña ni exportación masiva de datos.
- El servidor web debe ejecutarse tras un proxy HTTPS si se expone al exterior (la cookie es `SameSite=None; Secure`).

## Motor gráfico

`GPX_RENDER_ENGINE` selecciona el motor:

| Valor | Qué hace | Requisitos |
|---|---|---|
| `terrain3d` (por defecto) | 3D real: relieve de las montañas + imagen satélite, recorrido ceñido al terreno, cámara animada, HUD con perfil | `moderngl`, OpenGL headless (EGL), conexión a internet |
| `simulated` | Mapa 2D plano con Pillow (pruebas, sin red) | ninguno |
| `node` | Lanza `node $GPX_NODE_WORKER_SCRIPT --payload payload.json --out frames/` (worker no incluido) | Node.js |

Motor `terrain3d` (`app/terrain.py` + `app/terrain_engine.py`):

- **Relieve**: teselas *terrarium* de Mapzen/AWS. **Imagen**: Esri World Imagery (la atribución se
  pinta en el vídeo; revisa sus condiciones de uso antes de un uso comercial). Ambas URL son
  configurables (`GPX_DEM_URL`, `GPX_IMAGERY_URL`, `GPX_IMAGERY_ATTRIBUTION`).
- Las teselas se cachean en `GPX_TILE_CACHE_DIR`; la primera vez que se renderiza una zona tarda más.
- Usa la GPU si hay; si no, el rasterizador por software de Mesa (mucho más lento: ~0,5 s por
  fotograma a 1080p). En Debian/Ubuntu: `sudo apt install libegl1 libgl1-mesa-dri`.
- Los fotogramas se envían a FFmpeg y se borran según se generan (`StreamingVideoCompiler`), así
  que el directorio temporal no crece aunque el vídeo sea largo.
- Calidad: `GPX_IMAGERY_MAX_PX` (lado máximo de la textura, 6144 por defecto) y
  `GPX_TERRAIN_MESH_MAX` (vértices por lado de la malla, 480).

## Configuración (variables de entorno)

`GPX_MAX_UPLOAD_BYTES`, `GPX_SECONDS_PER_KM`, `GPX_MIN_VIDEO_SECONDS`, `GPX_MAX_VIDEO_SECONDS`,
`GPX_RENDER_ENGINE`, `GPX_NODE_WORKER_SCRIPT`, `GPX_MAX_CONCURRENT_RENDERS`, `GPX_FFMPEG_BINARY`,
`GPX_FFMPEG_CRF`, `GPX_FFMPEG_PRESET`, `GPX_OUTPUT_DIR` (ver `app/config.py`).
