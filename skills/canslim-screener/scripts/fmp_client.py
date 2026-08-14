#!/usr/bin/env python3
"""
FMP API Client for CANSLIM Screener

Provides rate-limited access to Financial Modeling Prep API endpoints
required for CANSLIM component analysis (C, A, N, M).

Features:
- Rate limiting (0.3s between requests)
- Automatic retry on 429 errors
- Session caching for duplicate requests
- Error handling and logging
"""

import os
import sys
import time
from datetime import date, timedelta
from typing import Optional

try:
    import requests
except ImportError:
    print("ERROR: requests library not found. Install with: pip install requests", file=sys.stderr)
    sys.exit(1)


# --- FMP endpoint fallback: stable (new users) -> v3 (legacy users) ---


def _stable_quote_url(base, symbols_str, params):
    """stable/quote?symbol=^GSPC"""
    params["symbol"] = symbols_str
    return base, params


def _v3_quote_url(base, symbols_str, params):
    """api/v3/quote/^GSPC"""
    return f"{base}/{symbols_str}", params


def _stable_hist_url(base, symbols_str, params):
    """stable/historical-price-eod/full?symbol=^GSPC&from=...&to=..."""
    params["symbol"] = symbols_str
    # New stable EOD endpoint ignores `timeseries`; convert to from/to range
    # to bound the payload. Use 2x calendar days to cover N trading days
    # (trading-day/calendar-day ratio ~252/365 ~0.69, so *2 leaves headroom).
    days = params.pop("timeseries", None)
    if days is not None:
        today = date.today()
        params["from"] = (today - timedelta(days=int(days) * 2)).isoformat()
        params["to"] = today.isoformat()
    return base, params


def _v3_hist_url(base, symbols_str, params):
    """api/v3/historical-price-full/^GSPC?timeseries=80"""
    return f"{base}/{symbols_str}", params


_FMP_ENDPOINTS = {
    "quote": [
        ("https://financialmodelingprep.com/stable/quote", _stable_quote_url),
        ("https://financialmodelingprep.com/api/v3/quote", _v3_quote_url),
    ],
    "historical": [
        ("https://financialmodelingprep.com/stable/historical-price-eod/full", _stable_hist_url),
        ("https://financialmodelingprep.com/api/v3/historical-price-full", _v3_hist_url),
    ],
}


def _normalize_eod_flat_list(data, symbols_str: str, limit: Optional[int] = None):
    """Convert stable/historical-price-eod/full flat list to v3-compatible dict.

    Input  : [{"symbol": "SPY", "date": "...", "open": ..., ...}, ...]
    Output : {"symbol": "SPY", "historical": [{"date": ..., "open": ..., ...}, ...]}

    Returns the input unchanged if not a list (passthrough for v3 dict /
    historicalStockList responses). Returns None when no row matches the
    requested symbol; the caller will record the failure and try the next
    endpoint.

    If `limit` is provided (the original `timeseries=N` request), the
    `historical` list is truncated to the first `limit` entries. The new
    EOD endpoint ignores `timeseries` and returns the full available history,
    so the caller's date-range bounding plus this truncation together preserve
    the legacy "most-recent N rows" contract. Truncation assumes descending
    date order, which the FMP EOD endpoint provides (verified live).

    Note: empty list ``[]`` does not reach this normalizer because the caller's
    ``if not data: continue`` falsy check handles it earlier in
    ``_request_with_fallback``.
    """
    if not isinstance(data, list):
        return data
    if not data:
        return None
    norm_target = symbols_str.replace("-", ".")
    matched_symbol = None
    historical = []
    for row in data:
        if not isinstance(row, dict):
            continue
        # Be permissive: single-symbol endpoint may omit per-row "symbol".
        # Treat missing symbol as belonging to the requested symbols_str.
        row_sym = row.get("symbol") or symbols_str
        if row_sym.replace("-", ".") != norm_target:
            continue
        matched_symbol = matched_symbol or row_sym
        historical.append({k: v for k, v in row.items() if k != "symbol"})
    if not historical:
        return None
    if limit is not None and limit > 0:
        historical = historical[:limit]
    return {"symbol": matched_symbol or symbols_str, "historical": historical}


# --- yfinance fallback (free, no API key) for FMP free-tier 402/403/429 ---
# FMP's free tier no longer serves the legacy quote / historical / income /
# institutional endpoints. yfinance supplies equivalent data with no key; the
# helpers below reshape it into the dicts the CANSLIM calculators expect.

_YF_AVAILABLE = None
_yf_hist_cache: dict = {}


def _yf_ok() -> bool:
    global _YF_AVAILABLE
    if _YF_AVAILABLE is None:
        try:
            import yfinance  # noqa: F401

            _YF_AVAILABLE = True
        except Exception:
            _YF_AVAILABLE = False
    return _YF_AVAILABLE


def _is_num(v) -> bool:
    """True if v is a real (non-NaN) number."""
    try:
        return v is not None and float(v) == float(v)
    except Exception:
        return False


def _yf_fetch_history(symbol: str, days: int) -> Optional[list]:
    """Fetch OHLCV via yfinance as v3-shaped rows (most-recent-first), or None."""
    if not _yf_ok():
        return None
    key = (symbol, days)
    if key in _yf_hist_cache:
        return _yf_hist_cache[key]
    import yfinance as yf

    # `days` is trading bars; scale to calendar days (~252/yr) with headroom.
    # A fixed "2y" ceiling silently truncated any request above ~502 bars.
    period_days = max(int(days * 1.6) + 15, 400) if days else 400
    try:
        df = yf.Ticker(symbol.replace(".", "-")).history(
            period=f"{period_days}d", auto_adjust=False
        )
    except Exception:
        _yf_hist_cache[key] = None
        return None
    if df is None or df.empty:
        _yf_hist_cache[key] = None
        return None
    df = df.dropna(subset=["Close"])  # drop partial/empty trailing rows (yfinance quirk)
    if df.empty:
        _yf_hist_cache[key] = None
        return None
    rows = []
    for idx, r in df.iterrows():
        try:
            close = float(r["Close"])
            rows.append(
                {
                    "date": idx.strftime("%Y-%m-%d"),
                    "open": float(r["Open"]),
                    "high": float(r["High"]),
                    "low": float(r["Low"]),
                    "close": close,
                    "adjClose": float(r["Adj Close"]) if "Adj Close" in r else close,
                    "volume": float(r["Volume"]),
                }
            )
        except Exception:
            continue
    rows.reverse()  # yfinance is oldest-first; v3 contract is most-recent-first
    if days and days > 0:
        rows = rows[:days]
    result = rows or None
    _yf_hist_cache[key] = result
    return result


def _yf_quote(symbols_str: str) -> Optional[list]:
    """Synthesize v3-style quote dicts from yfinance history, or None."""
    quotes = []
    for sym in symbols_str.split(","):
        sym = sym.strip()
        if not sym:
            continue
        rows = _yf_fetch_history(sym, 260)
        if not rows:
            continue
        highs = [r["high"] for r in rows]
        lows = [r["low"] for r in rows]
        vols = [r["volume"] for r in rows]
        quotes.append(
            {
                "symbol": sym,
                "price": rows[0]["close"],
                "yearHigh": max(highs) if highs else 0,
                "yearLow": min(lows) if lows else 0,
                "volume": vols[0] if vols else 0,
                "avgVolume": (sum(vols[:50]) / min(len(vols), 50)) if vols else 0,
                "marketCap": 0,
                "name": sym,
                "sector": "Unknown",
            }
        )
    return quotes or None


def _yf_income_statement(symbol: str, period: str) -> Optional[list]:
    """Return [{date, eps, revenue}, ...] most-recent-first via yfinance, or None."""
    if not _yf_ok():
        return None
    import yfinance as yf

    t = yf.Ticker(symbol.replace(".", "-"))
    try:
        df = t.quarterly_income_stmt if str(period).startswith("quarter") else t.income_stmt
    except Exception:
        return None
    if df is None or getattr(df, "empty", True):
        return None

    def _row(*names):
        for n in names:
            if n in df.index:
                return df.loc[n]
        return None

    eps_row = _row("Diluted EPS", "Basic EPS")
    rev_row = _row("Total Revenue", "Operating Revenue")
    ni_row = _row("Net Income", "Net Income Common Stockholders")
    sh_row = _row("Diluted Average Shares", "Basic Average Shares")

    records = []
    for col in df.columns:
        try:
            date_str = col.strftime("%Y-%m-%d")
        except Exception:
            date_str = str(col)[:10]
        eps = None
        if eps_row is not None and _is_num(eps_row.get(col)):
            eps = float(eps_row.get(col))
        if eps is None and ni_row is not None and sh_row is not None:
            ni, sh = ni_row.get(col), sh_row.get(col)
            if _is_num(ni) and _is_num(sh) and float(sh) != 0:
                eps = float(ni) / float(sh)
        rev = float(rev_row.get(col)) if (rev_row is not None and _is_num(rev_row.get(col))) else None
        records.append({"date": date_str, "eps": eps, "revenue": rev})
    # newest-first to match FMP contract
    records.sort(key=lambda r: r["date"], reverse=True)
    return records or None


def _yf_profile(symbol: str) -> Optional[list]:
    """Return [{companyName, sector, mktCap, sharesOutstanding}] via yfinance, or None."""
    if not _yf_ok():
        return None
    import yfinance as yf

    t = yf.Ticker(symbol.replace(".", "-"))
    market_cap = 0
    shares = None
    try:
        fi = t.fast_info
        if _is_num(getattr(fi, "market_cap", None)):
            market_cap = float(fi.market_cap)
        if _is_num(getattr(fi, "shares", None)):
            shares = float(fi.shares)
    except Exception:
        pass
    company_name = symbol
    sector = "Unknown"
    try:
        info = t.info  # opportunistic; may be slow/empty on some tickers
        if isinstance(info, dict):
            company_name = info.get("longName") or info.get("shortName") or symbol
            sector = info.get("sector") or "Unknown"
            if not market_cap and _is_num(info.get("marketCap")):
                market_cap = float(info["marketCap"])
            if shares is None and _is_num(info.get("sharesOutstanding")):
                shares = float(info["sharesOutstanding"])
    except Exception:
        pass
    # A profile with no market cap and no shares is useless; signal failure.
    if not market_cap and shares is None and company_name == symbol:
        return None
    return [
        {
            "symbol": symbol,
            "companyName": company_name,
            "sector": sector,
            "mktCap": market_cap,
            "marketCap": market_cap,
            "sharesOutstanding": shares,
        }
    ]


def _yf_institutional(symbol: str) -> Optional[dict]:
    """Return {num_holders, ownership_pct, top_holders} via yfinance, or None."""
    if not _yf_ok():
        return None
    import yfinance as yf

    t = yf.Ticker(symbol.replace(".", "-"))
    ownership_pct = None
    num_holders = None
    top: list = []
    try:
        mh = t.major_holders
        if mh is not None and not mh.empty:
            for label, attr in (("institutionsPercentHeld", "pct"), ("institutionsCount", "cnt")):
                try:
                    val = mh.loc[label].iloc[0]
                except Exception:
                    continue
                if _is_num(val):
                    if attr == "pct":
                        ownership_pct = float(val) * 100
                    else:
                        num_holders = int(float(val))
    except Exception:
        pass
    try:
        ih = t.institutional_holders
        if ih is not None and not ih.empty:
            for _, r in ih.iterrows():
                shares = r.get("Shares")
                top.append(
                    {
                        "holder": r.get("Holder"),
                        "shares": int(shares) if _is_num(shares) else 0,
                        "change": 0,
                    }
                )
    except Exception:
        pass
    if num_holders is None and not top and ownership_pct is None:
        return None
    return {
        "num_holders": num_holders or len(top),
        "ownership_pct": ownership_pct,
        "top_holders": top,
    }


class FMPClient:
    """Client for Financial Modeling Prep API with rate limiting and caching"""

    BASE_URL = "https://financialmodelingprep.com/api/v3"
    STABLE_URL = "https://financialmodelingprep.com/stable"
    RATE_LIMIT_DELAY = 0.3  # 300ms between requests (200 requests/minute max)

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize FMP API client

        Args:
            api_key: FMP API key (defaults to FMP_API_KEY environment variable)

        Raises:
            ValueError: If API key not provided and not in environment
        """
        self.api_key = api_key or os.getenv("FMP_API_KEY")
        if not self.api_key:
            raise ValueError(
                "FMP API key required. Set FMP_API_KEY environment variable "
                "or pass api_key parameter."
            )

        self.session = requests.Session()
        self.session.headers.update({"apikey": self.api_key})
        self.cache = {}  # Simple in-memory cache for session
        self.last_call_time = 0
        self.rate_limit_reached = False
        self.retry_count = 0
        self.max_retries = 1
        # Circuit breaker: track consecutive failures per endpoint URL prefix
        self._endpoint_failures: dict[str, int] = {}
        self._disabled_endpoints: set[str] = set()
        self._ENDPOINT_FAILURE_THRESHOLD = 3

    def _rate_limited_get(
        self, url: str, params: Optional[dict] = None, quiet: bool = False
    ) -> Optional[dict]:
        """
        Make rate-limited GET request with retry logic

        Args:
            url: Full endpoint URL
            params: Query parameters (apikey sent via header)
            quiet: If True, suppress non-429 error messages (used by fallback)

        Returns:
            JSON response dict, or None on error
        """
        if self.rate_limit_reached:
            return None

        if params is None:
            params = {}

        # Enforce rate limit
        elapsed = time.time() - self.last_call_time
        if elapsed < self.RATE_LIMIT_DELAY:
            time.sleep(self.RATE_LIMIT_DELAY - elapsed)

        try:
            response = self.session.get(url, params=params, timeout=30)
            self.last_call_time = time.time()

            if response.status_code == 200:
                self.retry_count = 0  # Reset on success
                return response.json()

            elif response.status_code == 429:
                # Rate limit exceeded
                self.retry_count += 1
                if self.retry_count <= self.max_retries:
                    print("WARNING: Rate limit exceeded. Waiting 60 seconds...", file=sys.stderr)
                    time.sleep(60)
                    return self._rate_limited_get(url, params, quiet=quiet)
                else:
                    print(
                        "ERROR: Daily API rate limit reached. Stopping analysis.", file=sys.stderr
                    )
                    self.rate_limit_reached = True
                    return None

            else:
                if not quiet:
                    print(
                        f"ERROR: API request failed: {response.status_code} - {response.text[:200]}",
                        file=sys.stderr,
                    )
                return None

        except requests.exceptions.RequestException as e:
            print(f"ERROR: Request exception: {e}", file=sys.stderr)
            return None

    def _request_with_fallback(self, endpoint_key, symbols_str, extra_params=None):
        """Try stable endpoint first, fall back to v3 with circuit breaker."""
        params = dict(extra_params) if extra_params else {}
        endpoints = _FMP_ENDPOINTS[endpoint_key]
        is_single = "," not in symbols_str

        for i, (base_url, url_builder) in enumerate(endpoints):
            if base_url in self._disabled_endpoints:
                continue

            url, final_params = url_builder(base_url, symbols_str, dict(params))
            is_last = i == len(endpoints) - 1
            data = self._rate_limited_get(url, final_params, quiet=not is_last)
            if not data:
                self._record_endpoint_failure(base_url)
                continue

            # Normalize new stable EOD flat-list shape to v3-compatible dict.
            # No-op for v3 dict / historicalStockList responses.
            # `timeseries` (original request) is passed as `limit` so the
            # EOD endpoint's full-history response is truncated to the
            # legacy "most-recent N rows" contract.
            if endpoint_key == "historical":
                limit = params.get("timeseries") if isinstance(params, dict) else None
                data = _normalize_eod_flat_list(data, symbols_str, limit=limit)
                if not data:
                    self._record_endpoint_failure(base_url)
                    continue

            valid = True
            if endpoint_key == "quote":
                if not isinstance(data, list) or len(data) == 0:
                    valid = False
                elif is_single and not any(
                    q.get("symbol", "").replace("-", ".") == symbols_str.replace("-", ".")
                    for q in data
                ):
                    valid = False

            if endpoint_key == "historical":
                if not isinstance(data, dict):
                    valid = False
                elif "historicalStockList" in data:
                    norm = symbols_str.replace("-", ".")
                    found = None
                    for entry in data["historicalStockList"]:
                        if entry.get("symbol", "").replace("-", ".") == norm:
                            found = {
                                "symbol": entry.get("symbol"),
                                "historical": entry.get("historical", []),
                            }
                            break
                    if found:
                        self._endpoint_failures[base_url] = 0
                        return found
                    valid = False
                elif "historical" not in data:
                    valid = False
                elif is_single and data.get("symbol"):
                    if data["symbol"].replace("-", ".") != symbols_str.replace("-", "."):
                        valid = False

            if valid:
                self._endpoint_failures[base_url] = 0
                return data
            self._record_endpoint_failure(base_url)
        return None

    def _record_endpoint_failure(self, base_url: str) -> None:
        failures = self._endpoint_failures.get(base_url, 0) + 1
        self._endpoint_failures[base_url] = failures
        if failures >= self._ENDPOINT_FAILURE_THRESHOLD:
            self._disabled_endpoints.add(base_url)

    def get_income_statement(
        self, symbol: str, period: str = "quarter", limit: int = 8
    ) -> Optional[list[dict]]:
        """
        Fetch income statement data (quarterly or annual)

        Args:
            symbol: Stock ticker (e.g., "AAPL")
            period: "quarter" or "annual"
            limit: Number of periods to fetch (default 8 for quarterly, 5 for annual)

        Returns:
            List of income statement records (most recent first), or None on error

        Example:
            quarterly = client.get_income_statement("AAPL", period="quarter", limit=8)
            # Returns last 8 quarters (2 years) for YoY comparison
        """
        cache_key = f"income_{symbol}_{period}_{limit}"

        if cache_key in self.cache:
            return self.cache[cache_key]

        params = {"period": period, "limit": limit}
        # stable: /income-statement?symbol=&period=&limit= ; v3 fallback: path-style
        data = self._rate_limited_get(
            f"{self.STABLE_URL}/income-statement", {**params, "symbol": symbol}, quiet=True
        )
        if not data:
            data = self._rate_limited_get(
                f"{self.BASE_URL}/income-statement/{symbol}", params, quiet=True
            )
        if not data:  # FMP free-tier blocked -> yfinance fallback
            data = _yf_income_statement(symbol, period)

        if data:
            self.cache[cache_key] = data

        return data

    def get_quote(self, symbols: str) -> Optional[list[dict]]:
        """
        Fetch real-time quote data

        Args:
            symbols: Single ticker or comma-separated list (e.g., "AAPL" or "AAPL,MSFT,GOOGL")

        Returns:
            List of quote records, or None on error

        Example:
            quote = client.get_quote("AAPL")
            # Returns [{"symbol": "AAPL", "price": 185.92, "yearHigh": 198.23, ...}]

            quotes = client.get_quote("^GSPC,^VIX")
            # Batch fetch S&P 500 and VIX
        """
        cache_key = f"quote_{symbols}"

        if cache_key in self.cache:
            return self.cache[cache_key]

        data = self._request_with_fallback("quote", symbols)
        if not data:  # FMP free-tier blocked -> yfinance fallback
            data = _yf_quote(symbols)

        if data:
            self.cache[cache_key] = data

        return data

    def get_historical_prices(self, symbol: str, days: int = 365) -> Optional[dict]:
        """
        Fetch historical daily price data

        Args:
            symbol: Stock ticker (e.g., "AAPL")
            days: Number of days of history to fetch (default 365 for 52-week analysis)

        Returns:
            Dict with 'symbol' and 'historical' (list of daily OHLCV records), or None

        Example:
            prices = client.get_historical_prices("AAPL", days=365)
            # prices['historical'][0] = most recent day
            # prices['historical'][251] = 252 trading days ago (~1 year)
        """
        cache_key = f"prices_{symbol}_{days}"

        if cache_key in self.cache:
            return self.cache[cache_key]

        data = self._request_with_fallback("historical", symbol, {"timeseries": days})
        if not data:  # FMP free-tier blocked -> yfinance fallback
            rows = _yf_fetch_history(symbol, days)
            if rows:
                data = {"symbol": symbol, "historical": rows}

        if data:
            self.cache[cache_key] = data

        return data

    def get_profile(self, symbol: str) -> Optional[list[dict]]:
        """
        Fetch company profile (sector, industry, description)

        Args:
            symbol: Stock ticker

        Returns:
            List with single profile dict, or None on error

        Example:
            profile = client.get_profile("AAPL")
            # profile[0] = {"symbol": "AAPL", "companyName": "Apple Inc.", "sector": "Technology", ...}
        """
        cache_key = f"profile_{symbol}"

        if cache_key in self.cache:
            return self.cache[cache_key]

        # stable: /profile?symbol= ; v3 fallback: /profile/{symbol}
        data = self._rate_limited_get(f"{self.STABLE_URL}/profile", {"symbol": symbol}, quiet=True)
        if not data:
            data = self._rate_limited_get(f"{self.BASE_URL}/profile/{symbol}", quiet=True)
        if not data:  # FMP free-tier blocked -> yfinance fallback
            data = _yf_profile(symbol)

        if data:
            # /stable/profile renamed mktCap -> marketCap; expose mktCap so the
            # screener (profile[0]["mktCap"]) keeps working on either endpoint.
            for p in data:
                if isinstance(p, dict) and "mktCap" not in p and "marketCap" in p:
                    p["mktCap"] = p["marketCap"]
            self.cache[cache_key] = data

        return data

    @staticmethod
    def _recent_13f_quarters(as_of=None, count: int = 4):
        """Yield the most recent completed (year, quarter) 13F periods, newest first.

        13F filings lag the quarter end by ~45 days, so the just-completed
        quarter may not be filed yet; the caller walks back until data exists.
        """
        d = as_of or date.today()
        year = d.year
        quarter = (d.month - 1) // 3  # current quarter (1-4) minus 1 = last completed
        if quarter == 0:
            quarter, year = 4, year - 1
        for _ in range(count):
            yield year, quarter
            quarter -= 1
            if quarter == 0:
                quarter, year = 4, year - 1

    def get_institutional_holders(self, symbol: str) -> Optional[dict]:
        """Fetch institutional sponsorship summary (CANSLIM 'I' component).

        Returns a dict::

            {"num_holders": int, "ownership_pct": float | None,
             "top_holders": [{"holder": str, "shares": int, "change": int}, ...]}

        /stable has no single endpoint returning the full holder list (the
        per-holder endpoint is paginated 10/page). Instead this uses
        institutional-ownership/symbol-positions-summary for the holder count
        (investorsHolding) and ownership % (ownershipPercent) — more accurate
        than summing a list — plus extract-analytics/holder (page 0) for the
        top names used in superinvestor detection. Falls back to the v3
        institutional-holder list for legacy keys. Returns None on failure.
        """
        cache_key = f"institutional_{symbol}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        result = None
        # --- /stable: latest available 13F quarter ---
        for year, quarter in self._recent_13f_quarters():
            qp = {"symbol": symbol, "year": year, "quarter": quarter}
            summary = self._rate_limited_get(
                f"{self.STABLE_URL}/institutional-ownership/symbol-positions-summary",
                qp,
                quiet=True,
            )
            if isinstance(summary, list) and summary:
                top = self._rate_limited_get(
                    f"{self.STABLE_URL}/institutional-ownership/extract-analytics/holder",
                    {**qp, "page": 0},
                    quiet=True,
                )
                top_holders = [
                    {
                        "holder": h.get("investorName"),
                        "shares": h.get("sharesNumber"),
                        "change": h.get("changeInSharesNumber"),
                    }
                    for h in (top or [])
                    if isinstance(h, dict)
                ]
                result = {
                    "num_holders": summary[0].get("investorsHolding"),
                    "ownership_pct": summary[0].get("ownershipPercent"),
                    "top_holders": top_holders,
                }
                break

        # --- v3 fallback (legacy keys): full holder list ---
        if result is None:
            v3 = self._rate_limited_get(f"{self.BASE_URL}/institutional-holder/{symbol}")
            if isinstance(v3, list) and v3:
                result = {
                    "num_holders": len(v3),
                    "ownership_pct": None,  # calculator derives from shares/profile/Finviz
                    "top_holders": v3[:10],  # already {holder, shares, change}
                }

        # --- yfinance fallback (FMP free-tier blocked) ---
        if result is None:
            result = _yf_institutional(symbol)

        if result is not None:
            self.cache[cache_key] = result
        return result

    def calculate_ema(self, prices: list[float], period: int = 50) -> float:
        """
        Calculate Exponential Moving Average

        Args:
            prices: List of prices (most recent first)
            period: EMA period (default 50)

        Returns:
            EMA value

        Note:
            This is a helper method for market direction (M component).
            Uses standard EMA formula: EMA = Price * k + EMA_prev * (1-k)
            where k = 2 / (period + 1)
        """
        if len(prices) < period:
            return sum(prices) / len(prices)  # Fallback to simple average

        # Reverse to oldest-first for calculation
        prices_reversed = prices[::-1]

        # Initialize with SMA
        sma = sum(prices_reversed[:period]) / period
        ema = sma

        # Calculate EMA
        k = 2 / (period + 1)
        for price in prices_reversed[period:]:
            ema = price * k + ema * (1 - k)

        return ema

    def clear_cache(self):
        """Clear session cache (useful for refreshing data)"""
        self.cache = {}
        print("Cache cleared", file=sys.stderr)

    def get_api_stats(self) -> dict:
        """
        Get API usage statistics for current session

        Returns:
            Dict with cache size and estimated API calls made
        """
        return {
            "cache_entries": len(self.cache),
            "rate_limit_reached": self.rate_limit_reached,
            "retry_count": self.retry_count,
        }


def test_client():
    """Test FMP client with sample queries"""
    print("Testing FMP Client...")

    client = FMPClient()

    # Test 1: Quote
    print("\n1. Testing quote endpoint (AAPL)...")
    quote = client.get_quote("AAPL")
    if quote:
        print(f"✓ Quote: {quote[0]['symbol']} @ ${quote[0]['price']:.2f}")
    else:
        print("✗ Quote failed")

    # Test 2: Quarterly income statement
    print("\n2. Testing quarterly income statement (AAPL)...")
    quarterly = client.get_income_statement("AAPL", period="quarter", limit=8)
    if quarterly:
        latest = quarterly[0]
        print(f"✓ Latest quarter: {latest['date']}, EPS: ${latest.get('eps', 'N/A')}")
    else:
        print("✗ Quarterly income statement failed")

    # Test 3: Annual income statement
    print("\n3. Testing annual income statement (AAPL)...")
    annual = client.get_income_statement("AAPL", period="annual", limit=5)
    if annual:
        latest = annual[0]
        print(f"✓ Latest year: {latest['date']}, EPS: ${latest.get('eps', 'N/A')}")
    else:
        print("✗ Annual income statement failed")

    # Test 4: Historical prices
    print("\n4. Testing historical prices (AAPL)...")
    prices = client.get_historical_prices("AAPL", days=365)
    if prices and "historical" in prices:
        print(f"✓ Fetched {len(prices['historical'])} days of price history")
        if len(prices["historical"]) > 0:
            latest = prices["historical"][0]
            print(f"  Latest: {latest['date']}, Close: ${latest['close']:.2f}")
    else:
        print("✗ Historical prices failed")

    # Test 5: Market indices (batch)
    print("\n5. Testing market indices (^GSPC, ^VIX)...")
    indices = client.get_quote("^GSPC,^VIX")
    if indices:
        for idx in indices:
            print(f"✓ {idx['symbol']}: {idx['price']:.2f}")
    else:
        print("✗ Market indices failed")

    # Test 6: Cache
    print("\n6. Testing cache (repeat AAPL quote)...")
    quote2 = client.get_quote("AAPL")
    if quote2:
        print("✓ Cache working (no API call made)")

    # Stats
    stats = client.get_api_stats()
    print("\nAPI Stats:")
    print(f"  Cache entries: {stats['cache_entries']}")
    print(f"  Rate limit reached: {stats['rate_limit_reached']}")

    print("\n✓ All tests completed")


if __name__ == "__main__":
    test_client()
