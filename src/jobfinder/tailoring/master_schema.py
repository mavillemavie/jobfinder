from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field
from ruamel.yaml import YAML

from jobfinder import paths

_SKILL_SPLIT = re.compile(
    r"[,;&/()|]|\s+(?:and|with|in|of|for|to|using|via|et|avec|en|pour)\s+", re.I
)
_FILLER = {"a", "an", "the", "and", "or", "strong", "sense", "etc"}


class ContactInfo(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    linkedin: str | None = None
    website: str | None = None


class SkillGroup(BaseModel):
    category: str
    items: list[str] = Field(default_factory=list)


class Experience(BaseModel):
    company: str
    title: str
    location: str | None = None
    start: str | None = None
    end: str | None = None
    bullets: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class Education(BaseModel):
    institution: str
    credential: str | None = None
    field: str | None = None
    year: str | None = None


class LanguageSkill(BaseModel):
    name: str
    level: str | None = None


class Project(BaseModel):
    name: str
    description: str | None = None
    tags: list[str] = Field(default_factory=list)


class MasterResume(BaseModel):
    contact: ContactInfo
    summary: str = ""
    skills: list[SkillGroup] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    languages: list[LanguageSkill] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)

    def skill_terms(self) -> set[str]:
        """Lower-cased skill items and experience/project tags (exact items; the truth check
        and the tailoring prompt rely on these being the master's own wording)."""
        terms = {item.lower().strip() for g in self.skills for item in g.items}
        terms |= {t.lower().strip() for e in self.experience for t in e.tags}
        terms |= {t.lower().strip() for p in self.projects for t in p.tags}
        return {t for t in terms if t}

    def overlap_terms(self) -> set[str]:
        """Short terms for the discovery keyword-overlap gate: every skill term plus the parts
        of any phrase-like item, so "advanced excel (pivot tables, power query)" also counts as
        "advanced excel", "pivot tables" and "power query". Parts longer than four words or made
        only of filler are dropped."""
        out = set(self.skill_terms())
        for term in list(out):
            for raw in _SKILL_SPLIT.split(term):
                words = [w for w in raw.replace(".", " ").split() if w not in _FILLER]
                part = " ".join(words).strip(" -")
                if len(part) >= 3 and 0 < len(words) <= 4:
                    out.add(part)
        return out

    @classmethod
    def from_yaml(cls, path: Path) -> MasterResume:
        with path.open(encoding="utf-8") as f:
            return cls.model_validate(YAML(typ="safe").load(f))

    def to_yaml(self, path: Path) -> None:
        y = YAML()
        y.default_flow_style = False
        y.width = 120
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            y.dump(self.model_dump(mode="json"), f)


def master_resume_path(lang: str = "en") -> Path:
    name = "resume.yaml" if lang == "en" else f"resume.{lang}.yaml"
    return paths.master_dir() / name


def cover_letter_path(lang: str = "en") -> Path:
    name = "cover-letter.md" if lang == "en" else f"cover-letter.{lang}.md"
    return paths.master_dir() / name


def master_exists(lang: str = "en") -> bool:
    return master_resume_path(lang).exists()


def load_master(lang: str = "en") -> MasterResume:
    path = master_resume_path(lang)
    if not path.exists():
        raise FileNotFoundError(f"no master resume at {path}; run `jobfinder ingest`")
    return MasterResume.from_yaml(path)
