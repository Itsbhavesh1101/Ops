"""Time discretization and integer tick conversion utilities.

Part of Phase 4A Generic Scheduling Core.
Translates timezone-aware datetimes to integer solver ticks relative to the horizon start.
"""

from datetime import datetime, timedelta


class TimeDiscretizationError(Exception):
    """Raised when timestamp conversion fails boundary or timezone invariants."""


def datetime_to_tick(
    dt: datetime,
    horizon_start: datetime,
    horizon_end: datetime,
    granularity_minutes: int,
) -> int:
    """Convert a timezone-aware datetime to an integer solver tick.

    Raises TimeDiscretizationError if dt is naive or outside [horizon_start, horizon_end].
    """
    if dt.tzinfo is None:
        raise TimeDiscretizationError(f"Timestamp '{dt}' must be timezone-aware.")
    if horizon_start.tzinfo is None or horizon_end.tzinfo is None:
        raise TimeDiscretizationError("Horizon bounds must be timezone-aware.")

    if dt < horizon_start:
        raise TimeDiscretizationError(
            f"Timestamp '{dt}' is earlier than horizon start '{horizon_start}'."
        )
    if dt > horizon_end:
        raise TimeDiscretizationError(
            f"Timestamp '{dt}' exceeds horizon end '{horizon_end}'."
        )

    delta_seconds = (dt - horizon_start).total_seconds()
    granularity_seconds = granularity_minutes * 60
    return int(round(delta_seconds / granularity_seconds))


def tick_to_datetime(
    tick: int,
    horizon_start: datetime,
    granularity_minutes: int,
) -> datetime:
    """Convert an integer solver tick back to a timezone-aware datetime."""
    if tick < 0:
        raise TimeDiscretizationError(f"Tick ({tick}) cannot be negative.")
    return horizon_start + timedelta(minutes=tick * granularity_minutes)


def minutes_to_ticks(duration_minutes: int, granularity_minutes: int) -> int:
    """Convert a duration in minutes to an integer tick count."""
    if duration_minutes < 0:
        raise TimeDiscretizationError(f"Duration ({duration_minutes}) cannot be negative.")
    ticks = int(round(duration_minutes / granularity_minutes))
    return max(ticks, 1) if duration_minutes > 0 else 0


def ticks_to_minutes(ticks: int, granularity_minutes: int) -> int:
    """Convert tick count back to minutes."""
    if ticks < 0:
        raise TimeDiscretizationError(f"Ticks ({ticks}) cannot be negative.")
    return ticks * granularity_minutes
