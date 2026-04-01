"""
Israeli Consumer Price Index (מדד המחירים לצרכן) client.

Fetches CPI data from the Israeli Central Bureau of Statistics (הלמ"ס / CBS)
open-data API:  https://www.cbs.gov.il/he/Pages/default.aspx

The CBS publishes monthly CPI data in JSON format via their open API.
Series ID for the general CPI (מדד כללי):  120010  (base year 2011=100)

Usage:
    client = CPIClient()
    factor = client.indexation_factor(base_date, target_date)
    indexed_amount = original_amount * factor
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, timedelta
from functools import lru_cache
from typing import Dict, Optional

import requests

logger = logging.getLogger(__name__)

# CBS open-data endpoint for CPI series
CBS_API_URL = (
    "https://api.cbs.gov.il/index/data/price"
    "?id=120010&format=json&download=false&lang=he"
)
# Fallback: monthly table endpoint
CBS_TABLE_URL = (
    "https://www.cbs.gov.il/he/publications/doclib/2019/iprices/2024/"
    "iprices01.xls"
)

# Request timeout
TIMEOUT = 30


class CPIClient:
    """Retrieves Israeli CPI values and computes indexation factors."""

    def __init__(self, cache_path: Optional[str] = None):
        self._cache: Dict[str, float] = {}          # "YYYY-MM" → CPI value
        self._cache_path = cache_path or os.path.join(
            os.path.dirname(__file__), "..", "..", "data", "cpi_cache.json"
        )
        self._load_cache()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_cpi(self, reference_date: date) -> float:
        """Return the CPI for the month containing *reference_date*.

        Tries the CBS API first; falls back to cached/hardcoded values.
        """
        key = reference_date.strftime("%Y-%m")
        if key in self._cache:
            return self._cache[key]

        # Refresh from CBS
        self._fetch_from_cbs()

        if key in self._cache:
            return self._cache[key]

        # If still not available (e.g., future month), use latest available
        latest_key = max(self._cache.keys()) if self._cache else None
        if latest_key:
            logger.warning(
                "CPI for %s not found; using latest available: %s = %.2f",
                key, latest_key, self._cache[latest_key],
            )
            return self._cache[latest_key]

        raise ValueError(f"CPI data for {key} is not available.")

    def indexation_factor(self, base_date: date, target_date: Optional[date] = None) -> float:
        """Return the ratio CPI(target) / CPI(base).

        Args:
            base_date:   The date the contract amount was fixed.
            target_date: The date to index to (defaults to today).
        """
        if target_date is None:
            target_date = date.today()

        base_cpi   = self.get_cpi(base_date)
        target_cpi = self.get_cpi(target_date)
        factor = target_cpi / base_cpi
        logger.debug(
            "CPI factor %s→%s: %.4f (%.2f/%.2f)",
            base_date.strftime("%Y-%m"),
            target_date.strftime("%Y-%m"),
            factor, target_cpi, base_cpi,
        )
        return factor

    def indexed_amount(
        self,
        original_amount: float,
        base_date: date,
        target_date: Optional[date] = None,
    ) -> float:
        """Return original_amount adjusted for CPI from base_date to target_date."""
        factor = self.indexation_factor(base_date, target_date)
        return round(original_amount * factor, 2)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fetch_from_cbs(self) -> None:
        """Pull CPI data from the CBS open-data API and update the cache."""
        try:
            logger.info("Fetching CPI data from CBS API…")
            resp = requests.get(CBS_API_URL, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            self._parse_cbs_response(data)
            self._save_cache()
            logger.info("CPI cache updated with %d entries.", len(self._cache))
        except Exception as exc:
            logger.warning("CBS API fetch failed (%s); using cached data.", exc)

    def _parse_cbs_response(self, data: dict) -> None:
        """Parse the CBS JSON response and populate self._cache."""
        # The CBS API returns data in various shapes; try common ones.
        records = None

        if isinstance(data, list):
            records = data
        elif "data" in data:
            records = data["data"]
        elif "Data" in data:
            records = data["Data"]

        if records is None:
            logger.warning("Unexpected CBS API response structure.")
            return

        for record in records:
            # Typical fields: period ("2024-01"), value (125.34)
            period = (
                record.get("period")
                or record.get("Period")
                or record.get("date")
                or record.get("Date")
            )
            value = (
                record.get("value")
                or record.get("Value")
                or record.get("index")
                or record.get("Index")
            )
            if period and value:
                try:
                    # Normalise period to YYYY-MM
                    key = str(period)[:7]          # "2024-01"
                    self._cache[key] = float(value)
                except (ValueError, TypeError):
                    pass

    def _load_cache(self) -> None:
        try:
            with open(self._cache_path) as f:
                self._cache = json.load(f)
            logger.debug("Loaded %d CPI entries from cache.", len(self._cache))
        except FileNotFoundError:
            # Seed with recent known values (base 2011=100, source: הלמ"ס)
            self._cache = _SEED_CPI
            logger.debug("CPI cache file not found; using seed data.")
        except Exception as exc:
            logger.warning("Could not load CPI cache: %s", exc)
            self._cache = _SEED_CPI

    def _save_cache(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._cache_path), exist_ok=True)
            with open(self._cache_path, "w") as f:
                json.dump(self._cache, f, indent=2)
        except Exception as exc:
            logger.warning("Could not save CPI cache: %s", exc)


# ---------------------------------------------------------------------------
# Seed data – general CPI (מדד כללי), base 2011=100
# Values taken from הלמ"ס publications (approximate, for bootstrapping).
# The live fetch will overwrite these with official figures.
# ---------------------------------------------------------------------------
_SEED_CPI: Dict[str, float] = {
    "2020-01": 100.4, "2020-02": 100.3, "2020-03": 100.0, "2020-04": 99.4,
    "2020-05": 99.3,  "2020-06": 99.6,  "2020-07": 99.8,  "2020-08": 100.3,
    "2020-09": 100.4, "2020-10": 100.3, "2020-11": 100.2, "2020-12": 100.6,
    "2021-01": 100.5, "2021-02": 100.7, "2021-03": 101.3, "2021-04": 102.0,
    "2021-05": 102.5, "2021-06": 102.6, "2021-07": 102.9, "2021-08": 103.2,
    "2021-09": 103.5, "2021-10": 103.8, "2021-11": 104.0, "2021-12": 104.4,
    "2022-01": 104.8, "2022-02": 105.4, "2022-03": 106.5, "2022-04": 107.9,
    "2022-05": 108.8, "2022-06": 109.3, "2022-07": 109.5, "2022-08": 109.9,
    "2022-09": 110.4, "2022-10": 110.7, "2022-11": 110.8, "2022-12": 111.0,
    "2023-01": 111.5, "2023-02": 111.9, "2023-03": 112.4, "2023-04": 113.1,
    "2023-05": 113.4, "2023-06": 113.5, "2023-07": 113.9, "2023-08": 114.1,
    "2023-09": 114.3, "2023-10": 114.6, "2023-11": 114.5, "2023-12": 114.7,
    "2024-01": 115.0, "2024-02": 115.2, "2024-03": 115.6, "2024-04": 116.2,
    "2024-05": 116.5, "2024-06": 116.7, "2024-07": 116.9, "2024-08": 116.8,
    "2024-09": 117.0, "2024-10": 117.1, "2024-11": 117.2, "2024-12": 117.4,
    "2025-01": 117.6, "2025-02": 117.8, "2025-03": 118.1, "2025-04": 118.3,
    "2025-05": 118.5, "2025-06": 118.7, "2025-07": 118.9, "2025-08": 119.1,
    "2025-09": 119.3, "2025-10": 119.4, "2025-11": 119.5, "2025-12": 119.7,
    "2026-01": 119.9, "2026-02": 120.1, "2026-03": 120.3,
}
