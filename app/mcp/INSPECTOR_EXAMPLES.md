# Banking MCP Inspector examples

This guide covers all seven case categories, all seven escalation reasons and
all five service-request types in the current schemas. The complete 22-example
[catalog](banking_inspector_examples.json) also includes account reads, case
reads and note addition. Each catalog entry has a `tool` and an `arguments`
object. Paste only `arguments` into Inspector's **Edit as JSON** editor.

## Connection and authorization

From the repository root, run the existing bank and MCP server in separate terminals:

```sh
make run-bank
```

```sh
make run-banking-mcp
```

Connect Inspector using Streamable HTTP at `http://127.0.0.1:9002/mcp`, with
`BANKING_MCP_TOKEN` as the connection bearer token. Inspector reads require this
server-side configuration, followed by an MCP server restart:

```dotenv
BANKING_MCP_CUSTOMER_IDS=["NXC-0010"]
```

An ordinary token-only connection is read-only. A write payload is executable
only with an application-issued or trusted-operator-issued exact grant for that
tool, complete validated request and operation key. The previous verification's
temporary grants were deleted. They cannot be reused with these examples.

Use the existing **Service requests** owner/reviewer workflow for service-request
submission. The five service JSON examples below show its MCP call contract;
copying them into a token-only Inspector connection will be rejected.

For authorized local synthetic **case** testing, the last section shows how to
issue a temporary exact grant using the existing signing helper. It adds no
server permission bypass and does not issue service-request grants.

## Execution rules

1. Select the named tool, enable **Edit as JSON**, and paste the whole JSON block.
   In form mode, the `request` field contains the inner object instead.
2. Choose customer IDs, returned case IDs, payload and operation key **before**
   the matching grant is issued. Do not add `approved`, `grant` or permission fields.
3. Operation keys must contain 16–64 letters, digits, underscores or hyphens.
   The examples use distinct valid keys. Use a fresh key for a new operation;
   reuse the same key and unchanged payload only for a retry.
4. Replace `NXCASE-000001` in dependent examples with the actual `case_id` returned
   by your create call. A mock restart clears its cases. Do not reuse the old test
   case blindly: it was already escalated.
5. A case can be escalated once. Use a fresh case for each different escalation
   example. Replaying the same escalation keeps its original key.
6. Verify live loan ownership with `list_accounts`. `NXL-HL-0010` exists in the
   checked-in `NXC-0010` synthetic fixture; current runtime eligibility can differ.

## Read accounts before a loan-specific service request

### Customer accounts

Tool: `list_accounts`

```json
{
  "request": {
    "customer_id": "NXC-0010"
  }
}
```

## Case workflow: create → note → escalate → read

### Create a complaint case

Tool: `create_case`

Save the returned `case_id` for the next three examples.

```json
{
  "request": {
    "payload": {
      "customer_id": "NXC-0010",
      "category": "COMPLAINT",
      "priority": "NORMAL",
      "summary": "Customer reports a delay receiving a requested statement copy.",
      "related_application_id": null,
      "channel": "ASSISTANT"
    },
    "idempotency_key": "inspector-case-20261113-complaint"
  }
}
```

### Add a case note

Tool: `add_case_note`

```json
{
  "request": {
    "customer_id": "NXC-0010",
    "case_id": "NXCASE-000001",
    "payload": {
      "text": "Customer confirmed the statement-copy issue. Please investigate and arrange follow-up."
    },
    "idempotency_key": "inspector-note-20261113-001"
  }
}
```

### Escalate a complaint

Tool: `escalate_case`

The mock returns a 24-hour SLA for complaint escalation; suspected-fraud escalation uses 4 hours.

```json
{
  "request": {
    "customer_id": "NXC-0010",
    "case_id": "NXCASE-000001",
    "payload": {
      "reason": "COMPLAINT",
      "summary": "The synthetic customer complaint requires review by the complaints team.",
      "preferred_contact": "CALLBACK"
    },
    "idempotency_key": "inspector-esc-20261113-complaint"
  }
}
```

### Read the final case

Tool: `get_case`

Confirm the retained note, escalation and current status.

```json
{
  "request": {
    "customer_id": "NXC-0010",
    "case_id": "NXCASE-000001"
  }
}
```

## Every supported case category

All categories use `create_case`. Full payloads are in the catalog under these names.

| Category | Catalog example |
| --- | --- |
| `LOAN_ENQUIRY` | `create_case_loan_enquiry` |
| `KYC_ISSUE` | `create_case_kyc_issue` |
| `FEE_WAIVER_REQUEST` | `create_case_fee_waiver_request` |
| `COMPLAINT` | `create_case_complaint` |
| `SUSPECTED_FRAUD` | `create_case_suspected_fraud` |
| `DOCUMENT_ANOMALY` | `create_case_document_anomaly` |
| `OTHER` | `create_case_other` |

Case priority accepts `LOW`, `NORMAL` or `HIGH`. `channel` is `ASSISTANT`.
`related_application_id` is optional and defaults to `null`.

## Every supported escalation reason

All reasons use `escalate_case`; create a fresh case for each distinct scenario.

| Reason | Catalog example |
| --- | --- |
| `COMPLAINT` | `escalate_case_complaint` |
| `FEE_WAIVER_OR_SPECIAL_RATE` | `escalate_case_fee_waiver_or_special_rate` |
| `SUSPECTED_FRAUD` | `escalate_case_suspected_fraud` |
| `HARDSHIP` | `escalate_case_hardship` |
| `KYC_DISCREPANCY` | `escalate_case_kyc_discrepancy` |
| `UPLOAD_WITH_EMBEDDED_INSTRUCTIONS` | `escalate_case_upload_with_embedded_instructions` |
| `OUT_OF_SCOPE_REQUEST` | `escalate_case_out_of_scope_request` |

`preferred_contact` accepts `CALLBACK`, `BRANCH_VISIT` or `EMAIL`.

## All five service-request types

Every example uses `create_service_request` and requires its exact application-issued
write grant. A successful receipt has `status: RECEIVED`; it acknowledges the
request and does not mean the underlying customer service has been completed.

### Statement copy

Tool: `create_service_request`

```json
{
  "request": {
    "payload": {
      "request_type": "STATEMENT_COPY",
      "loan_account_id": null,
      "notes": "Please provide a copy of my account statement.",
      "customer_id": "NXC-0010"
    },
    "idempotency_key": "inspector-sr-20261113-statement_copy"
  }
}
```

### Callback

Tool: `create_service_request`

```json
{
  "request": {
    "payload": {
      "request_type": "CALLBACK",
      "loan_account_id": null,
      "notes": "Please arrange a callback about my service enquiry.",
      "customer_id": "NXC-0010"
    },
    "idempotency_key": "inspector-sr-20261113-callback"
  }
}
```

### Re-KYC reminder

Tool: `create_service_request`

```json
{
  "request": {
    "payload": {
      "request_type": "RE_KYC_REMINDER",
      "loan_account_id": null,
      "notes": "Please arrange a reminder about the re-KYC process.",
      "customer_id": "NXC-0010"
    },
    "idempotency_key": "inspector-sr-20261113-re_kyc_reminder"
  }
}
```

### Interest certificate

Tool: `create_service_request`

A current loan belonging to this customer is required.

```json
{
  "request": {
    "payload": {
      "request_type": "INTEREST_CERTIFICATE",
      "loan_account_id": "NXL-HL-0010",
      "notes": "Please provide an interest certificate for the selected loan.",
      "customer_id": "NXC-0010"
    },
    "idempotency_key": "inspector-sr-20261113-interest_certificate"
  }
}
```

### Circular part-prepayment opt-in

Tool: `create_service_request`

Requires an eligible owned NexaHome loan and a customer who has not already opted in. The application additionally checks dated circular evidence and current loan facts. A receipt records a request; it does not itself update a loan or confirm completion.

```json
{
  "request": {
    "payload": {
      "request_type": "OPT_IN_CIR_2026_07_PART_PREPAY",
      "loan_account_id": "NXL-HL-0010",
      "notes": "Please register my request to opt in under CIR-2026-07 for the selected loan.",
      "customer_id": "NXC-0010"
    },
    "idempotency_key": "inspector-sr-20261113-opt_in_cir_2026_07_part_prepay"
  }
}
```

## Authorized local case testing: prepare one exact grant

Use this only for the synthetic local case tools explicitly selected for testing.
For ordinary service requests, use the owner/reviewer workflow described above.

First save one complete chosen case JSON block to
`/tmp/nexa-inspector-request.json`. Set its actual returned case ID, if applicable,
and final operation key before running this command. Set `tool` to one of
`create_case`, `add_case_note` or `escalate_case` to match that JSON:

```sh
uv run --locked --no-sync python - <<'PY'
import json
import tempfile
from pathlib import Path
from app.core.config import AgentSettings
from app.mcp.banking_http import signed_headers
from app.models.banking_mcp import CaseWrite, NoteWrite, EscalationWrite, write_grant
from app.models.validation import ValidationAccess

settings = AgentSettings()
tool = "create_case"
models = {"create_case": CaseWrite, "add_case_note": NoteWrite, "escalate_case": EscalationWrite}
raw = json.loads(Path("/tmp/nexa-inspector-request.json").read_text())
request = models[tool].model_validate(raw["request"]).model_dump(mode="json")
customer_id = request.get("customer_id", request.get("payload", {}).get("customer_id"))
access = ValidationAccess(customer_ids=settings.banking_mcp_customer_ids,
                          principal_id="inspector-case-operator")
if customer_id not in access.customer_ids:
    raise ValueError("Configure the permitted Inspector customer scope first")
headers = signed_headers(settings, access, write_grant(tool, request))
config = {"mcpServers": {"banking-exact-case": {
    "type": "http", "url": str(settings.banking_mcp_url), "headers": headers
}}}
with tempfile.NamedTemporaryFile("w", prefix="nexa-inspector-case-",
                                 suffix=".json", dir="/tmp", delete=False) as file:
    json.dump(config, file)
    print(file.name)
PY
```

The command prints only the private config file's path. Launch the official
Inspector using that printed path:

```sh
npx -y @modelcontextprotocol/inspector@2.13.0 --web --config "/tmp/REPLACE-WITH-PRINTED-CONFIG-PATH.json"
```

Connect `banking-exact-case`, choose the same tool, and submit the same complete
JSON arguments. The grant expires after 330 seconds. Generate a fresh config and
relaunch Inspector if it expires or you change the tool, arguments or key.
Each new case operation needs its own matching grant. Keep an unchanged operation
key when recovering an uncertain result; do not issue a new write blindly.

The generated config contains a connection token and short-lived signed context;
keep it private and delete that specific file after closing Inspector. The
signing key and backend banking credential stay in the trusted Python process.

## Expected results and errors

| Observation | Meaning |
| --- | --- |
| `structuredContent.status_code: 201` | The backend accepted the write. |
| `headers.idempotent-replayed: true` | The unchanged operation replayed its original result. |
| `isError: true`, exact grant required | The connection has no matching write capability. |
| `Customer access is not authorized` | This session is not scoped to the customer. |
| `Case access is not authorized` | The case belongs to a different customer. |
| HTTP 403, `MCP request context rejected` | The signed context is invalid, expired or bound to a different backend. |
| Envelope status 422 | For example, the same key was reused with changed payload, or loan validation failed. |
| Envelope status 404 | The case does not exist. |
| Envelope status 409 | For example, a new escalation was attempted on an already escalated case. |

Read the response envelope's `status_code` as well as `isError`; a safely returned
banking HTTP error can be an ordinary MCP result with a 4xx status in its envelope.
