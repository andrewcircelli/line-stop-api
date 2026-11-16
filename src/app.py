"""Line-stop API: one Lambda behind an HTTP API, reading and writing one DynamoDB table.

Routes:
  POST /events            validate a floor event, then store it
  GET  /events?site_id=X  list one site's events, newest first
"""

import base64
import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

from validation import validate_event

# Created once when the container starts (the cold start), then reused by every
# warm invocation that lands on the same container.
TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])


def handler(event, context):
    # HTTP API sends "routeKey" as "METHOD /path", e.g. "POST /events".
    route = event["routeKey"]
    if route == "POST /events":
        return create_event(event)
    if route == "GET /events":
        return list_events(event)
    return respond(404, {"error": f"no route for {route}"})


def create_event(event):
    try:
        body = json.loads(read_body(event))
    except json.JSONDecodeError:
        return reject("body is not valid JSON")

    errors = validate_event(body)
    if errors:
        return reject("; ".join(errors))

    item = {
        "site_id": body["site_id"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "event_id": str(uuid.uuid4()),
        "station_id": body["station_id"],
        "event_type": body["event_type"],
        "severity": body["severity"],
        "note": body.get("note", ""),
    }
    TABLE.put_item(Item=item)
    log("stored", item)
    return respond(201, item)


def list_events(event):
    params = event.get("queryStringParameters") or {}
    site_id = params.get("site_id")
    if not site_id:
        return reject("site_id query parameter is required")

    # Query reads one partition (one site). It never scans the whole table.
    result = TABLE.query(
        KeyConditionExpression=Key("site_id").eq(site_id),
        ScanIndexForward=False,  # sort key descending = newest first
        Limit=100,
    )
    events = [from_dynamo(item) for item in result["Items"]]
    return respond(200, {"site_id": site_id, "count": len(events), "events": events})


# --- helpers ---------------------------------------------------------------

def read_body(event):
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    return body


def from_dynamo(item):
    # DynamoDB returns every number as a Decimal, which json.dumps can't handle.
    # The only number we store is severity, a whole number, so int() is safe.
    return {key: int(value) if isinstance(value, Decimal) else value
            for key, value in item.items()}


def reject(reason):
    log("rejected", {"reason": reason})
    return respond(400, {"error": reason})


def respond(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def log(action, detail):
    # One JSON line per decision. print() goes to CloudWatch Logs.
    print(json.dumps({"action": action, **detail}))
