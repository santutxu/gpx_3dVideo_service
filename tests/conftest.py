"""Configuración común de las pruebas."""

from __future__ import annotations

from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    """Devuelve el servicio a un estado conocido antes y después de cada prueba.

    Los renders ya no terminan dentro de la petición, así que hay que esperar a
    la cola; y el almacén de trabajos y la biblioteca de vídeos son singletons del
    proceso: sin limpieza, los vídeos de una prueba contaminan los recuentos de
    las siguientes (p. ej. ``video_count`` en la gestión de usuarios).
    """
    from app.main import library, pipeline, store

    for _ in range(2):
        pipeline.join(timeout=120)
        for job in store.list():
            store.remove(job.job_id)
        for video in library.list():
            library.delete(video.video_id)
    yield
    for _ in range(2):
        pipeline.join(timeout=120)
        for job in store.list():
            store.remove(job.job_id)
        for video in library.list():
            library.delete(video.video_id)
