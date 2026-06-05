"""Pydantic response models — typed contracts mirroring `mock.js` shapes.

Used as `response_model` on the granular endpoints (OpenAPI docs + validation).
The full `/api/dataset` bundle is returned as a composed object.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class FamilyOut(BaseModel):
    id: str
    name: str
    hue: int


class SkillOut(BaseModel):
    name: str
    level: str
    dura: int
    trend: str


class ScoreOut(BaseModel):
    total: float
    demand: float
    pay: float
    opp: float
    rank: int
    pctile: int


class SeriesPoint(BaseModel):
    year: int
    value: int


class ForecastPoint(BaseModel):
    year: int
    value: int
    lo: int
    hi: int


class RoleCountryOut(BaseModel):
    median: int
    series: list[SeriesPoint]
    demandSeries: list[SeriesPoint]
    forecast: list[ForecastPoint]
    demand: int
    interest: int
    score: ScoreOut
    sample: int
    conf: str
    kind: str
    source: str
    freshness: str
    transparency: float


class RoleOut(BaseModel):
    id: str
    name: str
    family: FamilyOut
    blurb: str
    skills: list[SkillOut]
    ladder: list[list[Any]]
    countries: dict[str, RoleCountryOut]


class CountryOut(BaseModel):
    code: str
    name: str
    cur: str
    curCode: str
    natFactor: float
    pppRate: float
    transparency: float
    c1: str
    c2: str


class JobScoreRow(BaseModel):
    id: str
    name: str
    family: FamilyOut | None = None
    median: int
    demand: int
    interest: int
    score: ScoreOut


class ProvenanceOut(BaseModel):
    role_id: str
    country: str
    source: str
    sample: int
    confidence: str
    kind: str
    freshness: str
    transparency: float
    is_seed: bool


class HealthOut(BaseModel):
    status: str
    env: str
    dataset_is_seed: bool | None = None
