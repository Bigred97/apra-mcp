"""Fuzzy search across the curated APRA dataset registry."""
from __future__ import annotations

from rapidfuzz import fuzz, process

from . import curated as curated_mod
from .models import DatasetSummary


def list_summaries() -> list[DatasetSummary]:
    out: list[DatasetSummary] = []
    for cd in curated_mod.list_all():
        out.append(
            DatasetSummary(
                id=cd.id,
                name=cd.name,
                description=cd.description,
                update_frequency=cd.update_frequency,
                is_curated=True,
            )
        )
    return out


def search(query: str, limit: int = 10) -> list[DatasetSummary]:
    """Fuzzy-search curated datasets — two-pool ranker.

    High-signal pool (id + name + curated.search_keywords) scored with
    token_set_ratio. Description pool capped at DESCRIPTION_CAP via
    WRatio. The previous single-pool WRatio collapsed unrelated
    datasets to identical ~57 scores because their descriptions all
    contained common terms like 'Australia', 'annual', 'data'.
    """
    if not query.strip():
        raise ValueError(
            "query is required. Try 'banks', 'capital', 'super', 'insurance', "
            "'life', or any other APRA topic."
        )
    summaries = list_summaries()
    if not summaries:
        return []
    DESCRIPTION_CAP = 30
    KEYWORD_WEIGHT = 0.4
    PHRASE_BONUS = 15
    keyword_lookup = {cd.id: " ".join(cd.search_keywords) for cd in curated_mod.list_all()}
    query_lc = query.lower()
    # Three-pool design: id+name is the PRIMARY discriminator. Keywords
    # broaden recall at reduced weight (otherwise unrelated datasets
    # whose keyword bag happens to contain a query token tie with the
    # named dataset). Same pattern asic-mcp 0.6.8 uses.
    candidates: list[tuple[float, float, int]] = []
    for i, s in enumerate(summaries):
        name_str = f"{s.id} {s.name}".lower()
        kw_str = f"{name_str} {keyword_lookup.get(s.id, '')}".lower()
        desc_str = (s.description or "").lower()
        name_high = fuzz.token_set_ratio(query_lc, name_str)
        kw_high = fuzz.token_set_ratio(query_lc, kw_str)
        desc_raw = fuzz.WRatio(query_lc, desc_str) if desc_str else 0
        desc = min(desc_raw, DESCRIPTION_CAP)
        phrase = PHRASE_BONUS if query_lc and query_lc in kw_str else 0
        raw_adjusted = name_high + kw_high * KEYWORD_WEIGHT + desc * 0.3 + phrase
        candidates.append((raw_adjusted, name_high, i))
    candidates.sort(key=lambda t: (-t[0], -t[1]))
    top_pool = candidates[:limit]
    out: list[DatasetSummary] = []
    if top_pool:
        leader_adj = top_pool[0][0]
        scale_ref = max(leader_adj, 100.0)
        for raw, _name_high, idx in top_pool:
            rel = round(max(0.0, (raw / scale_ref) * 100.0), 1)
            out.append(summaries[idx].model_copy(update={"relevance": rel}))
    return out
