from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional

class PolarStatus(BaseModel):
    connected: bool = False
    polar_user_id: Optional[int] = None
    last_sync_at: Optional[str] = None
    total_exercises: Optional[int] = None

class PolarExercise(BaseModel):
    id: str = Field(description="Polar exercise transaction id")
    polar_user_id: int
    start_time: str
    sport: Optional[str] = None
    duration_ms: Optional[int] = None
    distance_m: Optional[float] = None
    calories: Optional[int] = None
    has_route: bool = False
    already_imported: bool = False
    title: Optional[str] = None

class SyncResult(BaseModel):
    fetched: int = 0
    imported: int = 0
    skipped_no_route: int = 0
    skipped_already_imported: int = 0
    failed: int = 0
    errors: list[str] = Field(default_factory=list)
    video_ids: list[str] = Field(default_factory=list)

class ImportResult(BaseModel):
    video_id: str
    job_id: str
    polar_exercise_id: str
    title: str
