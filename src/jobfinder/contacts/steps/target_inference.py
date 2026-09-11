from __future__ import annotations

from jobfinder.contacts.base import ContactContext, StepResult
from jobfinder.llm.base import load_prompt, load_schema

MAX_TITLES = 6


class TargetInferenceStep:
    name = "target_inference"

    def run(self, ctx: ContactContext) -> StepResult:
        excerpt = (ctx.posting.description_text or "")[:2500]
        user = (
            f"TITLE: {ctx.posting.title}\nCOMPANY: {ctx.company.name}\n"
            f"SENIORITY: {ctx.posting.seniority or 'unspecified'}\nLANGUAGE: {ctx.language}\n\n"
            f"POSTING (excerpt)\n{excerpt}"
        )
        data = ctx.llm.complete_json(
            task="contact_titles", system=load_prompt("contact_titles"), user=user,
            schema=load_schema("contact_titles"), tier="fast", language=ctx.language,
        )
        titles: list[str] = []
        seen: set[str] = set()
        for raw in data.get("titles", []):
            t = (raw or "").strip()
            if t and t.lower() not in seen:
                titles.append(t)
                seen.add(t.lower())
        talent = (data.get("talent_title") or "").strip()
        if talent and talent.lower() not in seen:
            titles.append(talent)
        ctx.titles = titles[:MAX_TITLES]
        return StepResult(self.name, notes={"titles": ctx.titles, "language": data.get("language")})
