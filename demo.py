"""Demo sin servidor: ejecuta el pipeline completo sobre un GPX.

Uso: python demo.py [ruta.gpx] [follow|orbit|overhead] [720p|1080p]
Respeta GPX_CINEMATIC_ENABLED (por defecto 1) y concatena intro+tracking+outro en un único MP4.
"""
import sys
import tempfile
import time
from pathlib import Path

from app.config import get_settings
from app.gpx_processor import process_gpx
from app.renderer_orchestrator import RendererOrchestrator
from app.schemas import CameraStyle, RenderOptions, Resolution
from app.video_compiler import StreamingVideoCompiler

args = sys.argv[1:]
data = Path(args[0] if args else "sample_mountain.gpx").read_bytes()
opts = RenderOptions(
    camera_style=CameraStyle(args[1]) if len(args) > 1 else CameraStyle.FOLLOW,
    resolution=Resolution(args[2]) if len(args) > 2 else Resolution.P1080,
)
s = get_settings()
t = time.time()
track = process_gpx(data, opts, s)
print(track.metrics.model_dump_json(indent=2))
orch = RendererOrchestrator(settings=s)
payload = orch.build_payload("demo", track, opts)
out = Path("demo_output.mp4")
use_cinematic = bool(s.cinematic_enabled and hasattr(orch.engine, "render_intro"))
intro_n = int(round(s.cinematic_intro_s * payload.fps)) if use_cinematic else 0
outro_n = int(round(s.cinematic_outro_s * payload.fps)) if use_cinematic else 0
print(f"cinematic={'on' if use_cinematic else 'off'} intro={intro_n} track={payload.frame_count} outro={outro_n} fps={payload.fps}")
with tempfile.TemporaryDirectory(prefix="gpxrender_demo_") as tmp, StreamingVideoCompiler(
    out, width=payload.width, height=payload.height, fps=payload.fps
) as compiler:

    frames_dir = Path(tmp) / "frames"

    def on_progress(done: int, total: int) -> None:
        compiler.feed(frames_dir, done)
        print(f"\r{done}/{total}", end="", flush=True)

    if intro_n:
        intro_dir = Path(tmp) / "intro_frames"
        orch.render_intro(payload, intro_dir, None)
        for idx in range(intro_n):
            p = intro_dir / f"frame_{idx:04d}.png"
            compiler.write_png(p.read_bytes())
            p.unlink(missing_ok=True)
        try:
            intro_dir.rmdir()
        except OSError:
            pass
        print(f"intro {intro_n} frames inyectados")

    orch.render_frames(payload, Path(tmp), on_progress, verify=False)
    compiler.feed(frames_dir, payload.frame_count)

    if outro_n:
        outro_dir = Path(tmp) / "outro_frames"
        orch.render_outro(payload, outro_dir, None)
        for idx in range(outro_n):
            p = outro_dir / f"frame_{idx:04d}.png"
            compiler.write_png(p.read_bytes())
            p.unlink(missing_ok=True)
        try:
            outro_dir.rmdir()
        except OSError:
            pass
        print(f"outro {outro_n} frames inyectados")

    # finish espera _next == payload.frame_count (los de la pista principal)
    # los extras cinemáticos + momentos se cuentan vía frames_written
    compiler.finish(payload.frame_count)
    total = compiler.frames_written
    print(f"\n{total} fotogramas -> {out} ({out.stat().st_size / 1e6:.1f} MB) en {time.time() - t:.1f}s (track {payload.frame_count}+cinematic {intro_n+outro_n})")
