#!/usr/bin/env bash
# Push this repo's skills into the installed Claude plugin directory.
#
# The plugin lives inside app-managed storage, so a Claude Desktop update or a
# plugin re-provision can silently revert it. This repo is the source of truth;
# re-run this after any such revert, and after every `git merge upstream/main`.
#
# Only skills present in BOTH the repo and the plugin are touched. The plugin
# also carries Anthropic's own bundled skills (docx, pdf, morning, skill-creator
# and friends) which are not in this repo and must be left alone. Nothing is
# ever deleted, so generated reports and market_breadth_history.json survive.
#
# Usage: bash sync_plugin.sh [--dry-run]

set -uo pipefail

PLUGIN="C:/Users/HP/AppData/Local/Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude/local-agent-mode-sessions/skills-plugin/ac623684-afe3-4fad-82c9-013a12d2d993/01ff6831-8a5f-4e0d-a714-084ac1fc5547/skills"
REPO="$(cd "$(dirname "$0")" && pwd)/skills"
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

[ -d "$PLUGIN" ] || { echo "plugin skills dir not found: $PLUGIN"; exit 1; }

# Must be the LocalCache path, not the AppData/Roaming/Claude alias -- python
# resolving an absolute path through the MSIX junction gets exists() == False.

if [ "$DRY" -eq 0 ]; then
  BACKUP="$HOME/.claude/backups/plugin-skills-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$BACKUP" && cp -r "$PLUGIN" "$BACKUP/"
  echo "backup: $BACKUP  ($(find "$BACKUP" -type f | wc -l) files)"
fi

changed=0
added=0
for s in $(comm -12 <(ls "$PLUGIN" | sort) <(ls "$REPO" | sort)); do
  while IFS= read -r f; do
    rel="${f#$REPO/$s/}"
    dst="$PLUGIN/$s/$rel"
    if [ ! -f "$dst" ]; then
      added=$((added+1))
      [ "$DRY" -eq 0 ] && { mkdir -p "$(dirname "$dst")"; cp "$f" "$dst"; }
    elif ! cmp -s "$f" "$dst"; then
      changed=$((changed+1))
      [ "$DRY" -eq 0 ] && cp "$f" "$dst"
    fi
  done < <(find "$REPO/$s" -type f)
done

[ "$DRY" -eq 1 ] && echo "DRY RUN -- nothing written"
echo "updated: $changed   added: $added"

echo "--- local patches present? ---"
chk() { printf "  %-34s %s\n" "$1" "$([ "$(grep -ci "$3" "$PLUGIN/$2" 2>/dev/null)" -gt 0 ] && echo ok || echo MISSING)"; }
chk "vcp yfinance fallback"       "vcp-screener/scripts/fmp_client.py"                    "yfinance"
chk "canslim yfinance fallback"   "canslim-screener/scripts/fmp_client.py"                "yfinance"
chk "ftd yfinance fallback"       "ftd-detector/scripts/fmp_client.py"                    "yfinance"
chk "vcp pence/EUR formatting"    "vcp-screener/scripts/report_generator.py"              "def _px"
chk "vcp USD turnover floors"     "vcp-screener/scripts/screen_vcp.py"                    "build_usd_factors"
chk "macro NaN guard"             "macro-regime-detector/scripts/calculators/utils.py"    "math.isnan"
chk "eta yfinance fallback"       "earnings-trade-analyzer/scripts/fmp_client.py"         "yfinance"
chk "eta yf profile"              "earnings-trade-analyzer/scripts/fmp_client.py"         "_yf_profile"
chk "eta exchange map"            "earnings-trade-analyzer/scripts/fmp_client.py"         "_YF_EXCHANGE_MAP"
chk "eta funnel diagnostic"       "earnings-trade-analyzer/scripts/analyze_earnings_trades.py" "Filter funnel"
chk "pead yfinance fallback"      "pead-screener/scripts/fmp_client.py"                   "yfinance"
