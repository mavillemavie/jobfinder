from __future__ import annotations

from io import StringIO

from pydantic import BaseModel, Field
from ruamel.yaml import YAML

from jobfinder.tailoring.master_schema import MasterResume


class KeywordCoverage(BaseModel):
    matched: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class TailoredOutput(BaseModel):
    resume: MasterResume
    cover_letter: str
    change_log: list[str] = Field(default_factory=list)
    keyword_coverage: KeywordCoverage = Field(default_factory=KeywordCoverage)
    gaps: list[str] = Field(default_factory=list)


class ReviewOutput(BaseModel):
    score: int = Field(ge=0, le=100)
    critique: list[str] = Field(default_factory=list)
    revised: TailoredOutput


def yaml_dump(master: MasterResume) -> str:
    buf = StringIO()
    YAML().dump(master.model_dump(mode="json"), buf)
    return buf.getvalue()
