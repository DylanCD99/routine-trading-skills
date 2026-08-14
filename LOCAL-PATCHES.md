# Local patches

This is a fork of [tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills).
Everything here is upstream's except the six files below.

Keeping the list short is deliberate — the fewer files that differ, the less there is to
resolve on `git merge upstream/main`.

## What differs from upstream

| File | Why |
|---|---|
| `skills/canslim-screener/scripts/fmp_client.py` | yfinance fallback (quote, history, income statement, profile, institutional) |
| `skills/ftd-detector/scripts/fmp_client.py` | yfinance fallback (quote, history) |
| `skills/vcp-screener/scripts/fmp_client.py` | yfinance fallback + batch prefetch |
| `skills/vcp-screener/scripts/report_generator.py` | `_unit`/`_px` — price formatting in the quote currency. yfinance returns `.L` names in **pence**, so a bare `$` prefix reports London prices ~100x wrong |
| `skills/vcp-screener/scripts/screen_vcp.py` | `build_usd_factors` — USD turnover floors, so the non-US universes aren't screened against a share-count threshold that means nothing across currencies |
| `skills/macro-regime-detector/scripts/calculators/utils.py` | NaN guard: `close == 0` misses `float('nan')`, which passes through as a valid close |

The FMP free tier returns 402/403 on the endpoints these screeners need, which is what the
yfinance fallbacks are for. FMP stays the primary path on purpose — the fallback only engages
when FMP declines.

## Keeping up with upstream

```bash
git fetch upstream
git merge upstream/main
```

Conflicts should only ever appear in the six files above.

## Note on the old `~/.claude/skill-patches` snapshot

That directory held 16 files, but it was a snapshot of the installed plugin rather than a set
of deltas. Ten of them turned out to be *older* than current upstream and would have reverted
real work if committed — upstream had since added `encoding="utf-8"` on report writes, a more
complete yfinance fallback in `market-top-detector`, `theme_match_score` ranking in
`theme-detector`, a futures position subsystem in `thesis_store.py`, and a field-name fix in
`thesis_ingest.py` (it was reading `status`/`stop_loss`, which never exist in a real PEAD
record, leaving every stop unset). Only the six above were genuinely ahead of upstream.

Judge staleness by diffing, not by file mtime — the mtime records when the file was patched,
not which upstream commit it descends from.
