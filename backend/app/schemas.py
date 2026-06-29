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


class SalaryLensOut(BaseModel):
    median: int
    sample: int
    source: str | None = None
    currency: str
    basis: str


class SalaryLensesOut(BaseModel):
    # each lens is null where that lens has no data (honest "not enough data")
    advertised: SalaryLensOut | None = None
    realized: SalaryLensOut | None = None
    official: SalaryLensOut | None = None


class OutlookOut(BaseModel):
    horizon: int
    growthPct: float
    openingsPerYear: float | None = None
    rating: str | None = None
    shortage: str | None = None
    source: str | None = None


class RoleCountryOut(BaseModel):
    # median is null where the role/country has no salary number (honest gap)
    median: int | None = None
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
    # three salary lenses (advertised / realized / official), each on its own basis
    salaryLenses: SalaryLensesOut | None = None
    # forward demand-outlook (official occupation projection), when available
    outlook: OutlookOut | None = None


class PayLadderStepOut(BaseModel):
    ord: int
    label: str
    median: int
    n: int
    stepAbs: int | None = None
    stepPct: float | None = None
    country: str


class TrajectoryEdgeOut(BaseModel):
    to: str
    name: str
    similarity: float
    type: str
    source: str | None = None


class SkillImportanceOut(BaseModel):
    skill: str
    importance: float
    essential: bool
    source: str | None = None


class RoleOut(BaseModel):
    id: str
    name: str
    family: FamilyOut
    blurb: str
    skills: list[SkillOut]
    ladder: list[list[Any]]
    # real H-1B pay ladder + roles-only adjacency + O*NET/ESCO skill importance
    payLadder: list[PayLadderStepOut] = []
    trajectory: list[TrajectoryEdgeOut] = []
    importance: list[SkillImportanceOut] = []
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
    # full lineage tuple (threaded from the staging snapshot), when available
    snapshot_hash: str | None = None
    transform_version: str | None = None
    row_count: int | None = None
    as_of: str | None = None


class SourceProvenanceOut(BaseModel):
    source_id: str
    source_name: str
    kind: str
    snapshot_hash: str
    transform_version: str
    row_count: int
    as_of: str


class HealthOut(BaseModel):
    status: str
    env: str
    dataset_is_seed: bool | None = None
