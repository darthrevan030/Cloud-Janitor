"""AWS Cost Explorer client with tag-based lookup, service-level fallback,
and a file-based TTL cache.

Cost Explorer does not expose true per-resource-ID cost for most services —
only resources with an activated cost allocation tag can be isolated exactly.
This client implements a three-tier fallback:

  1. Tag-based lookup (Requirement 1) — OPPORTUNISTIC BEST-EFFORT. Depends on
     a cost-allocation tag literally named to match the resource's own ID,
     which nothing in this codebase creates (ResourceTagger infers env/team/
     owner, not a self-referential ID tag) and which AWS requires to be
     manually activated in Billing preferences. Expected to rarely fire.
  2. Service/usage-type-level attribution (Requirement 2) — the PRIMARY
     expected outcome whenever a Cost-Explorer-backed figure is obtained at
     all. A shared/aggregate figure, not an exact per-resource cost.
  3. The caller's own pricing-constant heuristic, as a last resort — this is
     also the ONLY tier reachable for a resource created earlier in the
     current calendar month, since the query period below never includes the
     in-progress month (see _trailing_complete_months() below).

Required IAM permissions: ``ce:GetCostAndUsage`` (tiers 1-2 above) and
``sts:GetCallerIdentity`` (account ID resolution via AWSProvider._account_id(),
used to key the cache — see Requirement 3.1).

IMPORTANT — Cost Explorer's MONTHLY granularity requires the query period to
be calendar-month-aligned: both ``TimePeriod.Start`` and ``TimePeriod.End``
must be the 1st of a month, or AWS raises ``ValidationException``. A period
ending on "today" (true on every day but the 1st) would fail validation
almost every day, silently forcing every lookup to the pricing-heuristic tier
while LOOKING like Cost Explorer is being queried successfully. See
Requirement 1a.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from cloud_janitor.core.paths import COST_EXPLORER_CACHE_PATH

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 86400  # 24 hours


@dataclass
class CostLookupResult:
    """Result of a Cost Explorer cost lookup.

    Attributes:
        amount: The resolved monthly cost figure (rounded to 2 decimal places).
        currency: The currency code (typically "USD").
        data_source: One of "cost_explorer_tag" or "cost_explorer_service",
            identifying which lookup tier produced this result.
    """

    amount: float
    currency: str
    data_source: str


class CostExplorerCache:
    """File-based TTL cache for Cost Explorer responses.

    Cache structure (on disk): a JSON dict mapping string keys to entries of
    the form ``{"response": <any>, "timestamp": <float>}``.

    TTL semantics note (resolves a MEDIUM defect from design review): the
    cache key embeds the query period's start/end dates. Under the corrected
    ``_trailing_complete_months()`` (Requirement 1a), those dates are
    calendar-month-aligned and therefore change only once per calendar month,
    not once per day as the original "end = today" design would have caused.
    ``JANITOR_CE_CACHE_TTL_SECONDS`` (default 86400 = 24h) therefore governs
    freshness WITHIN a given month — e.g. re-querying Cost Explorer
    periodically in case a prior month's figures were still settling — not a
    day-to-day cache-busting mechanism. This is a deliberate consequence of
    decoupling the cache key from a literal "today" value (the Requirement 1a
    fix), not a separately-invented cache scheme; the env var's description
    should be read with this in mind rather than as "cache expires and is
    refetched every 24 hours regardless of month."

    Corrupted/missing cache file handling (Requirement 3.6): if the cache
    file is missing, empty, or fails to parse as valid JSON, the entire cache
    is treated as a full miss — no error is raised. The next successful
    ``set()`` call will safely rewrite the file from scratch via atomic write
    (tmp + os.replace), matching the existing project pattern in
    ``savings_tracker.py`` and ``finops_auditor.py``.
    """

    def __init__(self, path: Path | None = None, ttl_seconds: int | None = None):
        self._path = path or COST_EXPLORER_CACHE_PATH
        self._ttl = ttl_seconds if ttl_seconds is not None else int(
            os.environ.get("JANITOR_CE_CACHE_TTL_SECONDS", str(DEFAULT_TTL_SECONDS))
        )

    def _load(self) -> dict:
        """Load the cache file contents, treating any failure as empty cache.

        Handles: FileNotFoundError (missing), json.JSONDecodeError (corrupted),
        UnicodeDecodeError (non-UTF-8 bytes), OSError (permission/IO errors),
        and non-dict parsed content — all degrade gracefully to an empty dict
        (full cache miss for all keys).
        A warning is logged for corrupted files so operators can investigate
        if needed.
        """
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            logger.warning(
                "Cost Explorer cache file is corrupted (invalid JSON), "
                "treating as empty cache: %s",
                self._path,
            )
            return {}
        except OSError:
            logger.warning(
                "Could not read Cost Explorer cache file, "
                "treating as empty cache: %s",
                self._path,
            )
            return {}
        # Valid JSON but not a dict (e.g. null, a list, a string) — treat as corrupted.
        if not isinstance(data, dict):
            logger.warning(
                "Cost Explorer cache file contains valid JSON but not a dict, "
                "treating as empty cache: %s",
                self._path,
            )
            return {}
        return data

    def get(self, key: str) -> Optional[dict]:
        """Return the cached response for ``key`` if it exists and hasn't expired.

        Returns None on cache miss, expired entry, or corrupted/missing file.
        """
        entries = self._load()
        entry = entries.get(key)
        if entry is None:
            return None
        # Validate entry shape — treat malformed entries as a miss
        if not isinstance(entry, dict) or "timestamp" not in entry or "response" not in entry:
            return None
        if time.time() - entry["timestamp"] > self._ttl:
            return None
        return entry["response"]  # type: ignore[no-any-return]

    def set(self, key: str, response: dict) -> None:
        """Write/update a cache entry with atomic write (tmp + os.replace).

        If the existing cache file is corrupted or missing, the cache is
        rebuilt from scratch containing only this new entry.
        """
        entries = self._load()
        entries[key] = {"response": response, "timestamp": time.time()}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(entries, indent=None), encoding="utf-8")
        tmp.replace(self._path)

    @staticmethod
    def make_key(account_id: str, start: str, end: str, filter_obj: dict) -> str:
        """Build a deterministic cache key from the query parameters.

        Uses SHA-256 of the JSON-serialized filter object (sorted keys) to
        produce a stable, collision-resistant key component regardless of
        dict insertion order.
        """
        filter_hash = hashlib.sha256(
            json.dumps(filter_obj, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        return f"{account_id}:{start}:{end}:{filter_hash}"


class CostExplorerClient:
    """Wraps boto3's Cost Explorer client with tag-based and service-level
    lookup, read-through cache, and calendar-month-aligned query periods.

    This client implements a two-tier fallback (the caller typically adds a
    third, pricing-heuristic tier on top):

      1. Tag-based lookup (``lookup_by_tag``) — OPPORTUNISTIC BEST-EFFORT.
         Depends on a cost-allocation tag literally named to match the
         resource's own ID. Expected to rarely fire in practice.
      2. Service/usage-type-level attribution (``lookup_by_service``) — the
         PRIMARY expected outcome whenever a Cost-Explorer-backed figure is
         obtained at all. A shared/aggregate figure prorated by ``divisor``.

    Both methods read through the ``CostExplorerCache`` before calling AWS.
    """

    def __init__(self, region: str = "us-east-1"):
        self._region = region
        self._cache = CostExplorerCache()

    # ------------------------------------------------------------------
    # Period computation
    # ------------------------------------------------------------------

    @staticmethod
    def _trailing_complete_months(months_back: int = 1) -> tuple[str, str]:
        """Return a calendar-month-aligned (start, end) period for Cost Explorer.

        CRITICAL (Requirement 1a): Cost Explorer's ``Granularity="MONTHLY"``
        requires BOTH ``TimePeriod.Start`` and ``TimePeriod.End`` to be the
        1st of a month, or it raises ``ValidationException``. Using
        ``date.today()`` as ``end`` would succeed on exactly one day per
        month (the 1st) and silently fail every other day.

        Returns:
            A tuple of ISO-format date strings (YYYY-MM-DD), both with day=01.
            - ``end``: the 1st of the current month (exclusive upper bound).
            - ``start``: the 1st of the month ``months_back`` months earlier.

        UX tradeoff (documented, not a bug): a resource created earlier in
        the current month has no CE data under this period — both
        ``lookup_by_tag`` and ``lookup_by_service`` correctly return None,
        and the caller falls through to the pricing-heuristic tier. This is
        an inherent limitation of Cost Explorer's monthly granularity.
        """
        first_of_current_month = date.today().replace(day=1)
        # Walk back months_back months, handling Dec→Jan rollover
        year = first_of_current_month.year
        month = first_of_current_month.month
        for _ in range(months_back):
            month -= 1
            if month == 0:
                month = 12
                year -= 1
        start = date(year, month, 1)
        end = first_of_current_month
        return start.isoformat(), end.isoformat()

    # ------------------------------------------------------------------
    # Public lookup methods
    # ------------------------------------------------------------------

    def lookup_by_tag(
        self, tag_key: str, resource_id: str, account_id: str
    ) -> Optional[CostLookupResult]:
        """Resource_Tagged_Lookup — Requirement 1. OPPORTUNISTIC BEST-EFFORT.

        Queries Cost Explorer filtered by a cost-allocation tag matching the
        resource's own ID. Expected to rarely find a match in a typical
        account — most accounts never activate a self-referential
        cost-allocation tag. Requirement 2's service-level tier
        (``lookup_by_service``) is the realistic primary outcome, not this.

        Args:
            tag_key: The cost-allocation tag key to filter on.
            resource_id: The resource ID value to match against the tag.
            account_id: AWS account ID, used for cache keying.

        Returns:
            A ``CostLookupResult`` with ``data_source="cost_explorer_tag"``
            if a non-zero cost is found, or None otherwise.
        """
        start, end = self._trailing_complete_months()
        filter_obj: dict = {"Tags": {"Key": tag_key, "Values": [resource_id]}}
        cache_key = CostExplorerCache.make_key(account_id, start, end, filter_obj)

        cached = self._cache.get(cache_key)
        if cached is not None:
            response = cached
        else:
            try:
                response = self._call_ce(start, end, filter_obj)
            except Exception:
                logger.warning(
                    "Cost Explorer tag lookup failed for %s/%s",
                    tag_key,
                    resource_id,
                    exc_info=True,
                )
                return None
            self._cache.set(cache_key, response)

        amount = self._extract_amount(response)
        if amount > 0:
            currency = self._extract_currency(response)
            return CostLookupResult(
                amount=round(amount, 2),
                currency=currency,
                data_source="cost_explorer_tag",
            )
        return None

    def lookup_by_service(
        self,
        service_name: str,
        usage_type_prefix: str,
        account_id: str,
        divisor: int = 1,
    ) -> Optional[CostLookupResult]:
        """Service_Level_Attribution — Requirement 2. PRIMARY expected tier.

        Queries Cost Explorer grouped by SERVICE and USAGE_TYPE, then
        prorates the matching usage-type group's total by ``divisor``
        (typically the count of same-type resources currently in scope).

        This is the realistic primary outcome whenever a Cost-Explorer-backed
        figure is obtained at all — tag-based lookup (``lookup_by_tag``) is
        an opportunistic upgrade when available.

        Args:
            service_name: AWS service name for the filter (e.g. "Amazon Elastic Compute Cloud - Compute").
            usage_type_prefix: Prefix to match against USAGE_TYPE keys
                (e.g. "EBS:VolumeUsage" for EBS volumes).
            account_id: AWS account ID, used for cache keying.
            divisor: Number of same-type resources to prorate the total across.

        Returns:
            A ``CostLookupResult`` with ``data_source="cost_explorer_service"``
            if a non-zero cost is found, or None otherwise.
        """
        start, end = self._trailing_complete_months()
        filter_obj: dict = {"Dimensions": {"Key": "SERVICE", "Values": [service_name]}}
        # Include usage_type_prefix in cache key for distinctness
        cache_filter = {**filter_obj, "usage": usage_type_prefix}
        cache_key = CostExplorerCache.make_key(account_id, start, end, cache_filter)

        cached = self._cache.get(cache_key)
        if cached is not None:
            response = cached
        else:
            try:
                response = self._call_ce(
                    start, end, filter_obj, group_by=["SERVICE", "USAGE_TYPE"]
                )
            except Exception:
                logger.warning(
                    "Cost Explorer service lookup failed for %s/%s",
                    service_name,
                    usage_type_prefix,
                    exc_info=True,
                )
                return None
            self._cache.set(cache_key, response)

        amount = self._extract_grouped_amount(response, usage_type_prefix)
        if amount > 0:
            currency = self._extract_grouped_currency(response, usage_type_prefix)
            prorated = amount / max(divisor, 1)
            return CostLookupResult(
                amount=round(prorated, 2),
                currency=currency,
                data_source="cost_explorer_service",
            )
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _call_ce(
        self,
        start: str,
        end: str,
        filter_obj: dict,
        group_by: list[str] | None = None,
    ) -> dict:
        """Call ce.get_cost_and_usage() with the given parameters."""
        from cloud_janitor.mcp_server.backends.aws_provider import _make_client

        ce = _make_client("ce", self._region)
        kwargs: dict = {
            "TimePeriod": {"Start": start, "End": end},
            "Granularity": "MONTHLY",
            "Metrics": ["UnblendedCost"],
            "Filter": filter_obj,
        }
        if group_by:
            kwargs["GroupBy"] = [
                {"Type": "DIMENSION", "Key": k} for k in group_by
            ]
        return ce.get_cost_and_usage(**kwargs)  # type: ignore[no-any-return]

    @staticmethod
    def _extract_amount(response: dict) -> float:
        """Extract total UnblendedCost from a non-grouped CE response."""
        results = response.get("ResultsByTime", [])
        if not results:
            return 0.0
        total_block = results[0].get("Total", {})
        cost_block = total_block.get("UnblendedCost", {})
        return float(cost_block.get("Amount", "0"))

    @staticmethod
    def _extract_currency(response: dict) -> str:
        """Extract currency from a non-grouped CE response."""
        results = response.get("ResultsByTime", [])
        if not results:
            return "USD"
        total_block = results[0].get("Total", {})
        cost_block = total_block.get("UnblendedCost", {})
        return cost_block.get("Unit", "USD")

    @staticmethod
    def _extract_grouped_amount(response: dict, usage_type_prefix: str) -> float:
        """Sum UnblendedCost across groups matching the usage_type_prefix."""
        results = response.get("ResultsByTime", [])
        if not results:
            return 0.0
        total = 0.0
        for group in results[0].get("Groups", []):
            # Keys is typically [SERVICE, USAGE_TYPE]
            usage_type = group["Keys"][-1] if len(group.get("Keys", [])) > 1 else ""
            if usage_type.startswith(usage_type_prefix):
                total += float(
                    group.get("Metrics", {})
                    .get("UnblendedCost", {})
                    .get("Amount", "0")
                )
        return total

    @staticmethod
    def _extract_grouped_currency(response: dict, usage_type_prefix: str) -> str:
        """Extract currency from the first matching group."""
        results = response.get("ResultsByTime", [])
        if not results:
            return "USD"
        for group in results[0].get("Groups", []):
            usage_type = group["Keys"][-1] if len(group.get("Keys", [])) > 1 else ""
            if usage_type.startswith(usage_type_prefix):
                return (
                    group.get("Metrics", {})
                    .get("UnblendedCost", {})
                    .get("Unit", "USD")
                )
        return "USD"
