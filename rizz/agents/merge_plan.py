"""Phase 3 — structured merge plan emitted by the joiner.

The joiner's `Finish(...)` payload contains a fenced ```json``` block
with this shape:

    {
      "keep": ["wsA", "wsB"],
      "archive": [],
      "merge_order": ["wsA", "wsB"],
      "notes": "wsA implements; wsB documents."
    }

We parse it permissively: a fenced block, a ```json fenced block, or
just any balanced `{ ... }`. On parse failure we fall back to a safe
default (`keep = all_groups`) and stash the raw text in `notes` —
crucially, we **never** trigger a Replan on parse failure (that would
be a DoS vector if the joiner emits malformed JSON).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional

log = logging.getLogger(__name__)

_JSON_FENCE_RE = re.compile(
    r"```(?:json)?\s*(?P<body>\{.*?\})\s*```", re.DOTALL
)


@dataclass
class MergePlan:
    keep: List[str] = field(default_factory=list)
    archive: List[str] = field(default_factory=list)
    merge_order: List[str] = field(default_factory=list)
    notes: str = ""
    raw: Optional[str] = None  # populated when parsing fell back

    @property
    def parsed(self) -> bool:
        return self.raw is None


def parse_merge_plan(
    joiner_finish_text: Optional[str], *, all_groups: List[str]
) -> MergePlan:
    """Parse a merge plan from a joiner Finish() payload.

    Falls back gracefully: if no JSON block is found or parsing fails,
    return a plan that keeps every group (the safe default) and stash
    the raw text in `notes`. Never replans on parse failure.
    """
    if not joiner_finish_text:
        return _fallback(all_groups, "")

    body: Optional[str] = None
    m = _JSON_FENCE_RE.search(joiner_finish_text)
    if m:
        body = m.group("body")
    else:
        # Bare JSON object, anywhere in the text.
        first = joiner_finish_text.find("{")
        last = joiner_finish_text.rfind("}")
        if first != -1 and last > first:
            body = joiner_finish_text[first : last + 1]

    if body is None:
        return _fallback(all_groups, joiner_finish_text)

    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        log.warning("merge plan: JSON parse failed (%s); using fallback", e)
        return _fallback(all_groups, joiner_finish_text)
    if not isinstance(data, dict):
        log.warning("merge plan: top-level is not an object; using fallback")
        return _fallback(all_groups, joiner_finish_text)

    keep = [str(x) for x in data.get("keep", [])]
    archive = [str(x) for x in data.get("archive", [])]
    merge_order = [str(x) for x in data.get("merge_order", keep)]
    notes = str(data.get("notes", ""))

    # Validate against known groups; warn on stragglers.
    known = set(all_groups)
    unknown = (set(keep) | set(archive) | set(merge_order)) - known
    if unknown:
        log.warning(
            "merge plan: unknown groups %s — ignoring", sorted(unknown)
        )
        keep = [g for g in keep if g in known]
        archive = [g for g in archive if g in known]
        merge_order = [g for g in merge_order if g in known]

    return MergePlan(
        keep=keep,
        archive=archive,
        merge_order=merge_order,
        notes=notes,
    )


def _fallback(all_groups: List[str], raw: str) -> MergePlan:
    return MergePlan(
        keep=list(all_groups),
        archive=[],
        merge_order=list(all_groups),
        notes="",
        raw=raw,
    )
