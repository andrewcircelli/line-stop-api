"""Validation for a floor event. Pure Python, no AWS, so it can be tested locally.

validate_event returns a list of reasons the event is bad. An empty list means it's valid.
The API turns a non-empty list into a 400 with those reasons. Nothing is stored unless
this list is empty.
"""

REQUIRED_FIELDS = ("site_id", "station_id", "event_type", "severity")
OPTIONAL_FIELDS = ("note",)
EVENT_TYPES = ("defect", "stoppage")
MAX_NOTE_LENGTH = 500


def validate_event(body):
    if not isinstance(body, dict):
        return ["body must be a JSON object"]

    missing = [f"missing field: {field}" for field in REQUIRED_FIELDS if field not in body]
    if missing:
        return missing

    errors = []

    unknown = sorted(set(body) - set(REQUIRED_FIELDS) - set(OPTIONAL_FIELDS))
    if unknown:
        errors.append(f"unknown fields: {', '.join(unknown)}")

    for field in ("site_id", "station_id"):
        value = body[field]
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{field} must be a non-empty string")

    if body["event_type"] not in EVENT_TYPES:
        errors.append(f"event_type must be one of: {', '.join(EVENT_TYPES)}")

    severity = body["severity"]
    # In Python, True and False count as ints, so check for bool first.
    if isinstance(severity, bool) or not isinstance(severity, int) or not 1 <= severity <= 5:
        errors.append("severity must be a whole number from 1 to 5")

    if "note" in body:
        note = body["note"]
        if not isinstance(note, str) or len(note) > MAX_NOTE_LENGTH:
            errors.append(f"note must be a string of at most {MAX_NOTE_LENGTH} characters")

    return errors
