"""VALARM reminder expansion for VEVENT instances.

Expands the VEVENTs of an iCalendar text inside a UTC query window (via
:func:`app.expander.expand_with_sources`) and computes the reminder times
of every VALARM attached to each instance's source event: the master
event's VALARMs fire for every instance of its recurrence set, while an
override (RECURRENCE-ID) event uses its own VALARMs only — an override
without VALARMs silences the reminders of that instance.

Supported alarms (anything else raises :class:`AlarmError`, surfaced to
the client as HTTP 422):

* ``ACTION:DISPLAY`` only;
* absolute ``TRIGGER`` (a DATE-TIME value; floating values are read as
  UTC) and relative ``TRIGGER`` durations anchored at DTSTART (the
  default, or ``RELATED=START``) or DTEND (``RELATED=END``), positive or
  negative;
* ``REPEAT`` + ``DURATION`` — which RFC 5545 requires to occur together —
  add ``REPEAT`` extra reminders, each spaced ``DURATION`` after the
  previous one.

Only reminders whose trigger time falls inside the half-open window
[window_start, window_end) are returned, sorted by trigger time and event
identity (UID, then RECURRENCE-ID).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .expander import ExpansionError, Instance, expand_with_sources

UTC = dt.timezone.utc

# Safety bound: a single VALARM may not generate more repetitions than this.
MAX_ALARM_REPETITIONS = 10_000


class AlarmError(ExpansionError):
    """Invalid or unsupported VALARM; surfaced to the client as HTTP 422."""


@dataclass
class Alarm:
    uid: str
    recurrence_id: dt.datetime | None  # aware UTC; None for non-recurring events
    summary: str | None
    trigger: dt.datetime  # aware UTC
    description: str | None


def expand_alarms(
    ics_text: str, window_start: dt.datetime, window_end: dt.datetime
) -> list[Alarm]:
    """Expand all VALARM reminders firing inside [window_start, window_end).

    Sorted by (trigger, uid, recurrence_id). ``ExpansionError`` (including
    ``AlarmError``) propagates to the caller, which maps it to HTTP 422.
    """
    alarms: list[Alarm] = []
    for instance, event in expand_with_sources(ics_text, window_start, window_end):
        for component in event.subcomponents:
            if component.name == "VALARM":
                alarms.extend(_expand_valarm(instance, component))

    alarms = [a for a in alarms if window_start <= a.trigger < window_end]
    no_rid = dt.datetime.min.replace(tzinfo=UTC)
    alarms.sort(key=lambda a: (a.trigger, a.uid, a.recurrence_id or no_rid))
    return alarms


# ---------------------------------------------------------------------------
# Single VALARM expansion
# ---------------------------------------------------------------------------

def _expand_valarm(instance: Instance, valarm) -> list[Alarm]:
    """All reminders one VALARM produces for one expanded instance."""
    action = valarm.get("ACTION")
    if action is None:
        raise AlarmError("VALARM is missing the required ACTION property")
    if isinstance(action, list):
        raise AlarmError("VALARM ACTION must not occur more than once")
    if str(action).upper() != "DISPLAY":
        raise AlarmError(f"unsupported VALARM ACTION: {action!s}")

    trigger_prop = valarm.get("TRIGGER")
    if trigger_prop is None:
        raise AlarmError("VALARM is missing the required TRIGGER property")
    if isinstance(trigger_prop, list):
        raise AlarmError("VALARM TRIGGER must not occur more than once")
    base = _trigger_time(instance, trigger_prop)

    repeat, interval = _repeat_and_duration(valarm)

    description = valarm.get("DESCRIPTION")
    return [
        Alarm(
            uid=instance.uid,
            recurrence_id=instance.recurrence_id,
            summary=instance.summary,
            trigger=base + interval * count,
            description=str(description) if description is not None else None,
        )
        for count in range(repeat + 1)
    ]


def _trigger_time(instance: Instance, trigger_prop) -> dt.datetime:
    """Absolute UTC trigger time of one TRIGGER property on one instance."""
    value = trigger_prop.dt
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:  # floating time: interpreted as UTC
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, dt.timedelta):
        related = str(trigger_prop.params.get("RELATED", "START")).upper()
        if related == "START":
            return instance.start + value
        if related == "END":
            return instance.end + value
        raise AlarmError(f"unsupported TRIGGER RELATED={related}")
    raise AlarmError(f"unsupported TRIGGER value type: {type(value).__name__}")


def _repeat_and_duration(valarm) -> tuple[int, dt.timedelta]:
    """Validated (REPEAT, DURATION) pair; (0, zero) when both are absent."""
    repeat_prop = valarm.get("REPEAT")
    duration_prop = valarm.get("DURATION")
    if (repeat_prop is None) != (duration_prop is None):
        raise AlarmError("VALARM REPEAT and DURATION must occur together")
    if repeat_prop is None:
        return 0, dt.timedelta(0)
    if isinstance(repeat_prop, list) or isinstance(duration_prop, list):
        raise AlarmError(
            "VALARM REPEAT and DURATION must not occur more than once"
        )
    try:
        repeat = int(repeat_prop)
    except (TypeError, ValueError) as exc:
        raise AlarmError(f"invalid VALARM REPEAT: {repeat_prop!r}") from exc
    if repeat < 0:
        raise AlarmError("VALARM REPEAT must not be negative")
    if repeat > MAX_ALARM_REPETITIONS:
        raise AlarmError(
            f"VALARM REPEAT exceeds the limit of {MAX_ALARM_REPETITIONS} "
            "repetitions"
        )
    interval = duration_prop.dt
    if not isinstance(interval, dt.timedelta):
        raise AlarmError("VALARM DURATION must be a duration value")
    if interval <= dt.timedelta(0):
        raise AlarmError("VALARM DURATION must be a positive duration")
    return repeat, interval
