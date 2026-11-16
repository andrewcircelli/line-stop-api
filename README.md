# line-stop-api

A "stop the line" intake API in a manufacturing shape. A workstation on the floor reports an
event, either a defect or a stoppage. The API checks it, rejects anything malformed with a
reason, and stores the good ones per site. Each site's events can then be read on their own.

Built and deployed on my own AWS account as a learning build. It is not a production service.

## Architecture in five lines

1. **API Gateway (HTTP API)** gets `POST /events` and `GET /events` and passes each request to the function.
2. **One Lambda (Python 3.12)** validates the event and returns a 400 with the reason if it's bad.
3. **One DynamoDB table** stores events, keyed by `site_id` (partition key) and `created_at` (sort key).
4. **IAM**: the function may only `PutItem` and `Query` on that one table, plus write its own logs.
5. **CloudWatch Logs**: one JSON line per decision (`stored` or `rejected`), kept for 7 days.

One deployment serves every site. Adding a site means sending a new `site_id`, not deploying again.

## The event

```json
{ "site_id": "site-a", "station_id": "weld-3", "event_type": "defect", "severity": 4, "note": "burr on edge" }
```

| Field | Rule |
|---|---|
| `site_id`, `station_id` | required, non-empty string |
| `event_type` | required, `defect` or `stoppage` |
| `severity` | required, whole number 1–5 |
| `note` | optional, string up to 500 characters |
| anything else | rejected as an unknown field |

The server adds `created_at` (UTC, ISO 8601) and `event_id` (UUID). Clients never set them.

## Files

| File | What it is |
|---|---|
| `template.yaml` | The SAM template: table, function, IAM policy, routes, log group |
| `src/app.py` | The Lambda handler: routing, store, list |
| `src/validation.py` | The validation rules, pure Python with no AWS |
| `tests/test_validation.py` | Local tests for the validation rules |

## Deploy

Prereqs: an AWS account, AWS CLI configured (`aws sts get-caller-identity` works), SAM CLI, Python 3.12.

The commands use `us-east-2`. Swap in your own region.

```bash
# 1. Local tests (no AWS)
python3.12 -m unittest discover tests

# 2. Build: copies src/ into .aws-sam/build and writes a plain CloudFormation template there
sam build

# 3. Deploy
sam deploy \
  --stack-name line-stop-api \
  --region us-east-2 \
  --resolve-s3 \
  --capabilities CAPABILITY_IAM
```

What `sam deploy` does underneath:
- **Zips** the built code and **uploads** it to an S3 bucket. `--resolve-s3` makes SAM create and
  reuse a small managed bucket, in its own stack called `aws-sam-cli-managed-default`.
- **Expands the template.** The `Transform: AWS::Serverless-2016-10-31` line makes CloudFormation
  turn `AWS::Serverless::Function` into the plain resources: the Lambda function, its IAM role,
  the HTTP API with its routes and stage, and the permission letting the API invoke the function.
- **Creates a change set** (CloudFormation's diff of what will be created or changed) and executes it.
- `CAPABILITY_IAM` is you acknowledging that the stack creates an IAM role.

Roughly the same thing without SAM, which is what SAM wraps:

```bash
aws cloudformation package --template-file .aws-sam/build/template.yaml --s3-bucket <bucket> --output-template-file packaged.yaml
aws cloudformation deploy --template-file packaged.yaml --stack-name line-stop-api --capabilities CAPABILITY_IAM
```

Get the URL:

```bash
aws cloudformation describe-stacks --stack-name line-stop-api --region us-east-2 \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text
```

## Try it

```bash
API=<ApiUrl from above>

# Good event: 201 with the stored item
curl -s -X POST "$API/events" -H "Content-Type: application/json" \
  -d '{"site_id":"site-a","station_id":"weld-3","event_type":"defect","severity":4}'

# Missing field: 400 with the reason, and nothing stored
curl -s -X POST "$API/events" -H "Content-Type: application/json" \
  -d '{"site_id":"site-a","station_id":"weld-3","event_type":"defect"}'
# {"error": "missing field: severity"}

# One site's events, newest first
curl -s "$API/events?site_id=site-a"

# Logs
sam logs --stack-name line-stop-api --region us-east-2 --tail
```

## Tear down

```bash
sam delete --stack-name line-stop-api --region us-east-2
```

This deletes the CloudFormation stack, so the table and its data, the function, the role, the API
and the log group all go with it. The managed S3 bucket stack (`aws-sam-cli-managed-default`) stays.
To remove that too, empty the bucket in the S3 console, then run
`aws cloudformation delete-stack --stack-name aws-sam-cli-managed-default --region us-east-2`.

## Cost

- A **$1 AWS Budgets alert** was set up on the account before any resource was created. It emails
  on actual spend over $1 and on a forecast of going over $1.
- **The alert ignores credits** (`IncludeCredit: false`). The account is on AWS's Free plan with
  $100 of credits. A budget that counts credits would read $0 and never fire, so this one
  measures usage before credits are applied.
- On-demand DynamoDB, no VPC, no NAT gateway: nothing here bills by the hour.
- **Estimate for 1,000 events a day** (about 30k writes and 30k reads a month, us-east-1 list
  prices as of Nov 2026; Ohio is normally the same, not separately checked):
  API Gateway ~$0.06 + Lambda ~$0.03 + DynamoDB ~$0.03 ≈
  **$0.11 a month** before any free allowance. Lambda (1M requests, 400k GB-s) and DynamoDB
  (25 GB storage) have always-free monthly allowances that cover this volume.
  - API Gateway HTTP API: $1.00 per million requests × 60k
  - Lambda: $0.20 per million requests, plus 128 MB × ~100 ms × 60k = 750 GB-s × $0.0000166667 (x86 rate; Arm is lower)
  - DynamoDB on-demand: $0.625 per million writes, $0.125 per million read units

## What I saw when I ran it (deployed Nov 16, 2026)

| Request | Result |
|---|---|
| Good event | 201, stored with `created_at` and `event_id` |
| Missing `severity` | 400 `missing field: severity` |
| `event_type: lunch`, `severity: 9` | 400, both reasons in one message |
| Body `hello` | 400 `body is not valid JSON` |
| `GET /events` with no `site_id` | 400 `site_id query parameter is required` |
| `GET /events?site_id=site-a` | 200, the 2 good site-a events only, newest first. None of the rejects were stored. |

From the CloudWatch `REPORT` lines:
- **Cold start:** 353 ms to start the container and run the code outside the handler, plus 268 ms
  for the first request, which opens the first DynamoDB connection.
- **Warm:** about 30 ms for a stored event, and about 1.5 ms for a rejection, because a
  rejection never touches the database.
- **Memory:** 92 MB used of the 128 MB set.

## How I'd know it's working

- **Right:** bad input is rejected with a reason, and a test proves it (the 400 curl above, and
  `tests/test_validation.py`). Nothing is stored unless validation passed.
- **Cost:** the $1 budget alert, plus the estimate above. About $0.11 a month at 1,000 events a day.
- **Change:** for a plant manager, the number that matters is the time from a stop-the-line event
  to a supervisor's decision. Every event stores `created_at`. ⛔ *Not built yet:* `approved_at`
  comes with the approval workflow below.

## Supervisor approval (stretch) — ⛔ not built

Planned: an event with severity 4 or higher starts a Step Functions workflow that pauses on a
task token until a supervisor approves through an endpoint. That's the same pattern as a human
sign-off: the workflow waits for a person, then continues.

## What I'd change for production

- **Auth.** Right now anyone with the URL can post. I'd add a JWT authorizer on the HTTP API, or
  IAM auth for station devices, and only supervisors could use the approve endpoint.
- **Idempotency.** A station that retries after a timeout would store the event twice. I'd have the
  client send an idempotency key and use a conditional write so a repeat doesn't create a second row.
- **Sort key collisions.** Two events from the same site in the same microsecond would collide on
  `created_at`. I'd make the sort key `created_at#event_id`, or write with a condition that fails loudly.
- **Retries and a dead-letter queue.** If anything after intake goes async, failed messages go to
  a DLQ that a person works, rather than disappearing.
- **Paging.** `GET /events` returns at most 100 events. I'd return DynamoDB's `LastEvaluatedKey` as a cursor.
- **Alarms** on 5xx and throttles, not just logs. A dev and a prod stage, deployed from CI.
