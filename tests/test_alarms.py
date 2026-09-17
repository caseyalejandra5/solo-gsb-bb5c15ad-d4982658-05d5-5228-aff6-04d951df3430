"""Key tests for the /alarms endpoint.

Run from the repository root:  python -m pytest tests/ -v
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

CAL_HEAD = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//alarms-api//EN\r\n"
CAL_TAIL = "END:VCALENDAR\r\n"


def cal(*events: str) -> str:
    return CAL_HEAD + "".join(events) + CAL_TAIL


def event(*lines: str) -> str:
    return "BEGIN:VEVENT\r\n" + "".join(line + "\r\n" for line in lines) + "END:VEVENT\r\n"


def valarm(*lines: str) -> str:
    return "BEGIN:VALARM\r\n" + "\r\n".join(lines) + "\r\nEND:VALARM"


def alarms(ics: str, window_start="2026-01-01T00:00:00Z", window_end="2026-02-01T00:00:00Z"):
    return client.post(
        "/alarms",
        json={"ics": ics, "window_start": window_start, "window_end": window_end},
    )


def triggers(response) -> list[str]:
    return [a["trigger"] for a in response.json()["alarms"]]


# ---------------------------------------------------------------------------
# TRIGGER forms: absolute and relative to DTSTART / DTEND
# ---------------------------------------------------------------------------

def test_absolute_trigger_fires_at_its_instant():
    ics = cal(event(
        "UID:evt-1",
        "DTSTART:20260105T090000Z",
        "DTEND:20260105T100000Z",
        "SUMMARY:standup",
        valarm(
            "ACTION:DISPLAY",
            "TRIGGER;VALUE=DATE-TIME:20260105T084500Z",
            "DESCRIPTION:Morning reminder",
        ),
    ))
    resp = alarms(ics)
    assert resp.status_code == 200
    assert resp.json()["count"] == 1
    alarm = resp.json()["alarms"][0]
    assert alarm["uid"] == "evt-1"
    assert alarm["recurrence_id"] is None
    assert alarm["summary"] == "standup"
    assert alarm["trigger"] == "2026-01-05T08:45:00Z"
    assert alarm["description"] == "Morning reminder"


def test_naive_absolute_trigger_is_interpreted_as_utc():
    ics = cal(event(
        "UID:evt-1",
        "DTSTART:20260105T090000Z",
        valarm("ACTION:DISPLAY", "TRIGGER;VALUE=DATE-TIME:20260105T084500"),
    ))
    resp = alarms(ics)
    assert resp.status_code == 200
    assert triggers(resp) == ["2026-01-05T08:45:00Z"]
    assert resp.json()["alarms"][0]["description"] is None


def test_relative_trigger_defaults_to_dtstart():
    ics = cal(event(
        "UID:evt-1",
        "DTSTART:20260105T090000Z",
        "DTEND:20260105T100000Z",
        valarm("ACTION:DISPLAY", "TRIGGER:-PT15M"),
    ))
    resp = alarms(ics)
    assert triggers(resp) == ["2026-01-05T08:45:00Z"]


def test_relative_trigger_positive_duration_after_start():
    ics = cal(event(
        "UID:evt-1",
        "DTSTART:20260105T090000Z",
        "DTEND:20260105T100000Z",
        valarm("ACTION:DISPLAY", "TRIGGER:PT10M"),
    ))
    resp = alarms(ics)
    assert triggers(resp) == ["2026-01-05T09:10:00Z"]


def test_relative_trigger_related_to_end_negative():
    ics = cal(event(
        "UID:evt-1",
        "DTSTART:20260105T090000Z",
        "DTEND:20260105T100000Z",
        valarm("ACTION:DISPLAY", "TRIGGER;RELATED=END:-PT10M"),
    ))
    resp = alarms(ics)
    assert triggers(resp) == ["2026-01-05T09:50:00Z"]


def test_relative_trigger_related_to_end_positive():
    ics = cal(event(
        "UID:evt-1",
        "DTSTART:20260105T090000Z",
        "DTEND:20260105T100000Z",
        valarm("ACTION:DISPLAY", "TRIGGER;RELATED=END:PT5M"),
    ))
    resp = alarms(ics)
    assert triggers(resp) == ["2026-01-05T10:05:00Z"]


def test_related_end_on_zero_duration_event_anchors_at_start():
    ics = cal(event(
        "UID:evt-1",
        "DTSTART:20260105T090000Z",  # no DTEND: zero duration
        valarm("ACTION:DISPLAY", "TRIGGER;RELATED=END:-PT10M"),
    ))
    resp = alarms(ics)
    assert triggers(resp) == ["2026-01-05T08:50:00Z"]


# ---------------------------------------------------------------------------
# Master VALARMs vs. override VALARMs
# ---------------------------------------------------------------------------

def test_master_valarm_fires_for_every_recurrence():
    ics = cal(event(
        "UID:evt-1",
        "DTSTART:20260105T090000Z",
        "DTEND:20260105T100000Z",
        "RRULE:FREQ=DAILY;COUNT=3",
        valarm("ACTION:DISPLAY", "TRIGGER:-PT15M", "DESCRIPTION:daily"),
    ))
    resp = alarms(ics)
    assert resp.status_code == 200
    assert triggers(resp) == [
        "2026-01-05T08:45:00Z",
        "2026-01-06T08:45:00Z",
        "2026-01-07T08:45:00Z",
    ]
    got = resp.json()["alarms"]
    assert [a["recurrence_id"] for a in got] == [
        "2026-01-05T09:00:00Z",
        "2026-01-06T09:00:00Z",
        "2026-01-07T09:00:00Z",
    ]
    assert all(a["description"] == "daily" for a in got)


def test_override_uses_its_own_valarm():
    master = event(
        "UID:evt-1",
        "DTSTART:20260105T090000Z",
        "DTEND:20260105T100000Z",
        "RRULE:FREQ=DAILY;COUNT=2",  # Jan 5 and Jan 6
        "SUMMARY:weekly sync",
        valarm("ACTION:DISPLAY", "TRIGGER:-PT15M", "DESCRIPTION:master"),
    )
    override = event(
        "UID:evt-1",
        "RECURRENCE-ID:20260106T090000Z",
        "DTSTART:20260106T100000Z",  # moved one hour later
        "DTEND:20260106T110000Z",
        "SUMMARY:weekly sync (moved)",
        valarm("ACTION:DISPLAY", "TRIGGER:-PT30M", "DESCRIPTION:override"),
    )
    resp = alarms(cal(master, override))
    assert resp.status_code == 200
    got = resp.json()["alarms"]
    assert [(a["trigger"], a["description"]) for a in got] == [
        ("2026-01-05T08:45:00Z", "master"),
        ("2026-01-06T09:30:00Z", "override"),  # relative to the moved start
    ]
    # The override instance keeps its identity and its own summary.
    assert got[1]["recurrence_id"] == "2026-01-06T09:00:00Z"
    assert got[1]["summary"] == "weekly sync (moved)"


def test_override_without_valarm_silences_that_instance():
    master = event(
        "UID:evt-1",
        "DTSTART:20260105T090000Z",
        "DTEND:20260105T100000Z",
        "RRULE:FREQ=DAILY;COUNT=2",
        valarm("ACTION:DISPLAY", "TRIGGER:-PT15M"),
    )
    override = event(
        "UID:evt-1",
        "RECURRENCE-ID:20260106T090000Z",
        "DTSTART:20260106T100000Z",
        "DTEND:20260106T110000Z",
    )
    resp = alarms(cal(master, override))
    assert triggers(resp) == ["2026-01-05T08:45:00Z"]


def test_cancelled_override_produces_no_alarm():
    master = event(
        "UID:evt-1",
        "DTSTART:20260105T090000Z",
        "DTEND:20260105T100000Z",
        "RRULE:FREQ=DAILY;COUNT=2",
        valarm("ACTION:DISPLAY", "TRIGGER:-PT15M"),
    )
    override = event(
        "UID:evt-1",
        "RECURRENCE-ID:20260106T090000Z",
        "DTSTART:20260106T090000Z",
        "DTEND:20260106T100000Z",
        "STATUS:CANCELLED",
    )
    resp = alarms(cal(master, override))
    assert triggers(resp) == ["2026-01-05T08:45:00Z"]


# ---------------------------------------------------------------------------
# REPEAT / DURATION
# ---------------------------------------------------------------------------

def test_repeat_and_duration_generate_extra_reminders():
    ics = cal(event(
        "UID:evt-1",
        "DTSTART:20260105T090000Z",
        "DTEND:20260105T100000Z",
        valarm(
            "ACTION:DISPLAY",
            "TRIGGER:-PT15M",
            "REPEAT:2",
            "DURATION:PT10M",
        ),
    ))
    resp = alarms(ics)
    assert resp.status_code == 200
    assert triggers(resp) == [
        "2026-01-05T08:45:00Z",
        "2026-01-05T08:55:00Z",
        "2026-01-05T09:05:00Z",
    ]


def test_repeat_reminders_are_clipped_by_the_window():
    ics = cal(event(
        "UID:evt-1",
        "DTSTART:20260105T090000Z",
        "DTEND:20260105T100000Z",
        valarm(
            "ACTION:DISPLAY",
            "TRIGGER:-PT15M",
            "REPEAT:10",
            "DURATION:P1D",
        ),
    ))
    resp = alarms(ics, "2026-01-05T00:00:00Z", "2026-01-08T00:00:00Z")
    assert triggers(resp) == [
        "2026-01-05T08:45:00Z",
        "2026-01-06T08:45:00Z",
        "2026-01-07T08:45:00Z",
    ]


# ---------------------------------------------------------------------------
# Window filtering and ordering
# ---------------------------------------------------------------------------

def test_window_is_half_open_for_triggers():
    ics = cal(event(
        "UID:evt-1",
        "DTSTART:20260105T090000Z",
        "DTEND:20260105T100000Z",
        valarm("ACTION:DISPLAY", "TRIGGER;VALUE=DATE-TIME:20260101T000000Z"),
        valarm("ACTION:DISPLAY", "TRIGGER;VALUE=DATE-TIME:20260201T000000Z"),
    ))
    resp = alarms(ics)
    # Trigger at window_start is included, at window_end excluded.
    assert triggers(resp) == ["2026-01-01T00:00:00Z"]


def test_trigger_outside_window_is_dropped():
    ics = cal(event(
        "UID:evt-1",
        "DTSTART:20260105T090000Z",
        "DTEND:20260105T100000Z",
        valarm("ACTION:DISPLAY", "TRIGGER:-P10D"),  # fires in December
    ))
    resp = alarms(ics)
    assert resp.status_code == 200
    assert resp.json()["count"] == 0
    assert resp.json()["alarms"] == []


def test_event_outside_window_produces_no_alarms():
    # The trigger would fire inside the window, but the event itself does
    # not overlap it, so it is never expanded.
    ics = cal(event(
        "UID:evt-1",
        "DTSTART:20260210T090000Z",
        "DTEND:20260210T100000Z",
        valarm("ACTION:DISPLAY", "TRIGGER:-P20D"),
    ))
    resp = alarms(ics)
    assert resp.json()["count"] == 0


def test_alarms_sorted_by_trigger_then_identity():
    ics = cal(
        event(
            "UID:b-event",
            "DTSTART:20260105T090000Z",
            "DTEND:20260105T100000Z",
            valarm("ACTION:DISPLAY", "TRIGGER:-PT15M"),
        ),
        event(
            "UID:a-event",
            "DTSTART:20260105T090000Z",
            "DTEND:20260105T100000Z",
            valarm("ACTION:DISPLAY", "TRIGGER:-PT15M"),
        ),
        event(
            "UID:c-event",
            "DTSTART:20260105T090000Z",
            "DTEND:20260105T100000Z",
            valarm("ACTION:DISPLAY", "TRIGGER:-PT30M"),
        ),
    )
    resp = alarms(ics)
    got = [(a["trigger"], a["uid"]) for a in resp.json()["alarms"]]
    assert got == [
        ("2026-01-05T08:30:00Z", "c-event"),
        ("2026-01-05T08:45:00Z", "a-event"),
        ("2026-01-05T08:45:00Z", "b-event"),
    ]


def test_same_trigger_sorted_by_recurrence_id():
    # An absolute trigger fires at the same instant for every occurrence.
    ics = cal(event(
        "UID:evt-1",
        "DTSTART:20260105T090000Z",
        "DTEND:20260105T100000Z",
        "RRULE:FREQ=DAILY;COUNT=2",
        valarm("ACTION:DISPLAY", "TRIGGER;VALUE=DATE-TIME:20260110T090000Z"),
    ))
    resp = alarms(ics)
    got = [(a["trigger"], a["recurrence_id"]) for a in resp.json()["alarms"]]
    assert got == [
        ("2026-01-10T09:00:00Z", "2026-01-05T09:00:00Z"),
        ("2026-01-10T09:00:00Z", "2026-01-06T09:00:00Z"),
    ]


# ---------------------------------------------------------------------------
# Error handling: 422 for invalid or unsupported alarms
# ---------------------------------------------------------------------------

def test_unsupported_action_returns_422():
    ics = cal(event(
        "UID:evt-1",
        "DTSTART:20260105T090000Z",
        valarm("ACTION:AUDIO", "TRIGGER:-PT15M"),
    ))
    resp = alarms(ics)
    assert resp.status_code == 422
    assert "unsupported VALARM ACTION" in resp.json()["detail"]
    assert "AUDIO" in resp.json()["detail"]


def test_missing_action_returns_422():
    ics = cal(event(
        "UID:evt-1",
        "DTSTART:20260105T090000Z",
        valarm("TRIGGER:-PT15M"),
    ))
    resp = alarms(ics)
    assert resp.status_code == 422
    assert "ACTION" in resp.json()["detail"]


def test_missing_trigger_returns_422():
    ics = cal(event(
        "UID:evt-1",
        "DTSTART:20260105T090000Z",
        valarm("ACTION:DISPLAY", "DESCRIPTION:no trigger"),
    ))
    resp = alarms(ics)
    assert resp.status_code == 422
    assert "TRIGGER" in resp.json()["detail"]


def test_trigger_value_date_returns_422():
    ics = cal(event(
        "UID:evt-1",
        "DTSTART:20260105T090000Z",
        valarm("ACTION:DISPLAY", "TRIGGER;VALUE=DATE:20260105"),
    ))
    resp = alarms(ics)
    assert resp.status_code == 422
    assert "unsupported TRIGGER value type" in resp.json()["detail"]


def test_unknown_related_returns_422():
    ics = cal(event(
        "UID:evt-1",
        "DTSTART:20260105T090000Z",
        valarm("ACTION:DISPLAY", "TRIGGER;RELATED=FOO:-PT5M"),
    ))
    resp = alarms(ics)
    assert resp.status_code == 422
    assert "RELATED" in resp.json()["detail"]


def test_repeat_without_duration_returns_422():
    ics = cal(event(
        "UID:evt-1",
        "DTSTART:20260105T090000Z",
        valarm("ACTION:DISPLAY", "TRIGGER:-PT15M", "REPEAT:2"),
    ))
    resp = alarms(ics)
    assert resp.status_code == 422
    assert "REPEAT and DURATION" in resp.json()["detail"]


def test_duration_without_repeat_returns_422():
    ics = cal(event(
        "UID:evt-1",
        "DTSTART:20260105T090000Z",
        valarm("ACTION:DISPLAY", "TRIGGER:-PT15M", "DURATION:PT10M"),
    ))
    resp = alarms(ics)
    assert resp.status_code == 422
    assert "REPEAT and DURATION" in resp.json()["detail"]


def test_negative_repeat_returns_422():
    ics = cal(event(
        "UID:evt-1",
        "DTSTART:20260105T090000Z",
        valarm("ACTION:DISPLAY", "TRIGGER:-PT15M", "REPEAT:-1", "DURATION:PT10M"),
    ))
    resp = alarms(ics)
    assert resp.status_code == 422
    assert "REPEAT" in resp.json()["detail"]


def test_excessive_repeat_returns_422():
    ics = cal(event(
        "UID:evt-1",
        "DTSTART:20260105T090000Z",
        valarm(
            "ACTION:DISPLAY",
            "TRIGGER:-PT15M",
            "REPEAT:999999999",
            "DURATION:PT1S",
        ),
    ))
    resp = alarms(ics)
    assert resp.status_code == 422
    assert "REPEAT" in resp.json()["detail"]


def test_invalid_window_returns_422():
    ics = cal(event("UID:evt-1", "DTSTART:20260105T090000Z"))
    resp = alarms(ics, "2026-02-01T00:00:00Z", "2026-01-01T00:00:00Z")
    assert resp.status_code == 422
    assert "window_start" in resp.json()["detail"]


def test_unparseable_ics_returns_422():
    resp = alarms("this is not an iCalendar document")
    assert resp.status_code == 422
    assert "invalid iCalendar" in resp.json()["detail"]
