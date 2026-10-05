from __future__ import annotations

from pydantic import ValidationError

from jobfinder.llm.base import LLMError, LLMProvider, load_prompt
from jobfinder.tailoring.master_schema import MasterResume
from jobfinder.tailoring.models import ReviewOutput, TailoredOutput, yaml_dump


def with_rules(name: str) -> str:
    """A task prompt plus the absolute rules shared by tailoring and review."""
    return load_prompt(name) + "\n\n" + load_prompt("tailoring_rules")


def review_documents(
    tailored: TailoredOutput, *, master: MasterResume, master_cover: str, posting_block: str,
    lang: str, llm: LLMProvider,
) -> tuple[TailoredOutput, dict]:
    """One recruiter pass that revises the draft; any failure keeps the draft."""
    user = (
        f"LANGUAGE: {lang}\n\nMASTER RESUME (truth):\n{yaml_dump(master)}\n\n"
        f"MASTER COVER LETTER (truth, candidate's voice):\n{master_cover}\n\n"
        f"{posting_block}\n\nTAILORED DRAFT:\n{tailored.model_dump_json(indent=1)}"
    )
    try:
        data = llm.complete_json(
            task="review_documents", system=with_rules("review_documents"), user=user,
            schema=ReviewOutput.model_json_schema(), tier="strong", language=lang,
        )
        review = ReviewOutput.model_validate(data)
    except (LLMError, ValidationError) as exc:
        return tailored, {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    revised = review.revised
    # the draft's rationale and gaps stay; the reviewer's changes are added, not substituted
    revised.change_log = tailored.change_log + [
        f"review: {c}" for c in revised.change_log if c not in tailored.change_log
    ]
    revised.gaps = list(dict.fromkeys(tailored.gaps + revised.gaps))
    return revised, {"score": review.score, "critique": review.critique}
