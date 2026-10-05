"""Business Calendar resolution and working-hours computation interface (Phase 3B).

Defines:
- BusinessCalendarPolicyProtocol
- DefaultBusinessCalendarPolicy supporting CALENDAR_24X7 and DEFAULT_5X8 (Mon-Fri 09:00-17:00 UTC)
- SLA_BUSINESS_CALENDAR_PRODUCTION_SOURCE production deferral marker
"""

from datetime import UTC, datetime, timedelta
from typing import Protocol

SLA_BUSINESS_CALENDAR_PRODUCTION_SOURCE: str = "DEFERRED_PENDING_TENANT_POLICY_CONFIGURATION"


class BusinessCalendarPolicyProtocol(Protocol):
    """Protocol for business calendar working-hour calculations."""

    def add_business_duration(
        self,
        start_time: datetime,
        duration_seconds: int,
        calendar_ref: str = "CALENDAR_24X7",
    ) -> datetime:
        """Add business duration seconds to start_time, skipping non-working periods."""
        ...

    def get_remaining_business_duration(
        self,
        current_time: datetime,
        target_time: datetime,
        calendar_ref: str = "CALENDAR_24X7",
    ) -> int:
        """Compute remaining business seconds between current_time and target_time."""
        ...


class DefaultBusinessCalendarPolicy:
    """Deterministic business calendar calculator supporting 24x7 and 5x8 work schedules."""

    def add_business_duration(
        self,
        start_time: datetime,
        duration_seconds: int,
        calendar_ref: str = "CALENDAR_24X7",
    ) -> datetime:
        """Compute target timestamp given calendar rules."""
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=UTC)

        if duration_seconds <= 0:
            return start_time

        if calendar_ref == "CALENDAR_24X7":
            return start_time + timedelta(seconds=duration_seconds)

        # 5x8: Monday (0) - Friday (4), 09:00 - 17:00 UTC (8 business hours = 28800 seconds per day)
        curr = start_time
        remaining = duration_seconds

        while remaining > 0:
            # Advance to start of business hours if in weekend or outside 9-17
            weekday = curr.weekday()
            if weekday >= 5:  # Saturday or Sunday
                days_to_monday = 7 - weekday
                curr = datetime(curr.year, curr.month, curr.day, 9, 0, 0, tzinfo=UTC) + timedelta(
                    days=days_to_monday
                )
                continue

            if curr.hour < 9:
                curr = datetime(curr.year, curr.month, curr.day, 9, 0, 0, tzinfo=UTC)
                continue

            if curr.hour >= 17:
                # Move to next day 09:00
                curr = datetime(curr.year, curr.month, curr.day, 9, 0, 0, tzinfo=UTC) + timedelta(
                    days=1
                )
                continue

            # In working hours: calculate available seconds today until 17:00
            end_of_day = datetime(curr.year, curr.month, curr.day, 17, 0, 0, tzinfo=UTC)
            avail_sec = int((end_of_day - curr).total_seconds())

            if remaining <= avail_sec:
                curr = curr + timedelta(seconds=remaining)
                remaining = 0
            else:
                remaining -= avail_sec
                curr = datetime(curr.year, curr.month, curr.day, 9, 0, 0, tzinfo=UTC) + timedelta(
                    days=1
                )

        return curr

    def get_remaining_business_duration(
        self,
        current_time: datetime,
        target_time: datetime,
        calendar_ref: str = "CALENDAR_24X7",
    ) -> int:
        """Compute remaining business seconds between current_time and target_time."""
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=UTC)
        if target_time.tzinfo is None:
            target_time = target_time.replace(tzinfo=UTC)

        if current_time >= target_time:
            return 0

        if calendar_ref == "CALENDAR_24X7":
            return int((target_time - current_time).total_seconds())

        # 5x8: Mon-Fri 09:00 - 17:00 UTC
        curr = current_time
        business_seconds = 0
        while curr < target_time:
            weekday = curr.weekday()
            if weekday >= 5:  # Saturday or Sunday
                days_to_monday = 7 - weekday
                curr = datetime(curr.year, curr.month, curr.day, 9, 0, 0, tzinfo=UTC) + timedelta(
                    days=days_to_monday
                )
                if curr >= target_time:
                    break
                continue

            if curr.hour < 9:
                curr = datetime(curr.year, curr.month, curr.day, 9, 0, 0, tzinfo=UTC)
                if curr >= target_time:
                    break
                continue

            if curr.hour >= 17:
                curr = datetime(curr.year, curr.month, curr.day, 9, 0, 0, tzinfo=UTC) + timedelta(
                    days=1
                )
                if curr >= target_time:
                    break
                continue

            end_of_day = datetime(curr.year, curr.month, curr.day, 17, 0, 0, tzinfo=UTC)
            window_end = min(end_of_day, target_time)
            business_seconds += int((window_end - curr).total_seconds())
            if window_end == target_time:
                break
            curr = datetime(curr.year, curr.month, curr.day, 9, 0, 0, tzinfo=UTC) + timedelta(
                days=1
            )

        return business_seconds

