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
| `skills/earnings-trade-analyzer/scripts/fmp_client.py` | yfinance fallback incl. `_yf_profile` and `_YF_EXCHANGE_MAP`. Without the exchange map, yfinance returns `NYQ`/`PCX`/`ASE`/`BTS` where the filter expects `NYSE`/`NYSEArca`/`AMEX`/`BATS`, and **every US name is silently dropped** |
| `skills/earnings-trade-analyzer/scripts/analyze_earnings_trades.py` | the "Filter funnel" diagnostic — prints which filter cut the candidates, so a zero-candidate run is legible instead of mysterious |
| `skills/pead-screener/scripts/fmp_client.py` | yfinance fallback |
| `skills/pead-screener/scripts/screen_pead.py` | fallback wiring |

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
of deltas, and it was **not a complete inventory**. Two lessons, both learned the hard way:

**It was partly stale.** Ten of its sixteen files were *older* than current upstream and would
have reverted real work if committed — upstream had since added `encoding="utf-8"` on report
writes, a more complete yfinance fallback in `market-top-detector`, `theme_match_score` ranking
in `theme-detector`, a futures position subsystem in `thesis_store.py`, and a field-name fix in
`thesis_ingest.py` (it was reading `status`/`stop_loss`, which never exist in a real PEAD
record, leaving every stop unset).

**It was also incomplete.** `earnings-trade-analyzer` and `pead-screener` carried local patches
that were never mirrored into it — including a funnel diagnostic added the same day. Syncing
upstream over the plugin on the assumption that those 16 files were the whole patch set
destroyed them. They were recovered from the pre-sync backup.

So: the authoritative inventory is **the installed plugin diffed against upstream**, never a
side copy. And judge staleness by diffing, not by file mtime — the mtime records when a file
was patched, not which upstream commit it descends from.

## Reconciliation still owed

`earnings-trade-analyzer` and `pead-screener` are pinned to a locally-forked
`fmp_client.py` built on an older upstream base. The fallbacks work, but the files have not
been rebased onto current upstream the way the other patches were. Worth doing before trusting
a `git merge upstream/main` on those two skills.
