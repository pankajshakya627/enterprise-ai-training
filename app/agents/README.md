# Phase 2: four-agent service-request workflows

See the [agent refactor report](../../C4-Documentation/AGENT_REFACTOR_REPORT.md)
for current startup/dependency ownership, the full request trace and verified code tours.

For step-by-step UI approval/rejection instructions, all 27 customers and all five
request types, see the [Service requests operator guide](../../C4-Documentation/SERVICE_REQUESTS_GUIDE.md).

## Unified chat harness

**Agent chat** is the default workspace: one message input with a retained local
conversation. Ask a policy question, request owned-account transactions, or
prepare a case or service request. Banking credentials and saved-run loading
live in the sidebar. Policy questions work without a banking credential. The
legacy **Policy chat** and **Service requests** workspaces remain available.

| Request | Route and result |
| --- | --- |
| “Maximum loan amount for nexa home loan” | Orchestrator → policy MCP → grounded answer and citations |
| “eligibiliby for the Autoloan” | Resolve the stated auto-loan product → policy MCP → eligibility explanation |
| “Get at most five transactions from an owned account for 1–14 November 2026” | Ask for customer ID → check authorized accounts → select the only account or ask which owned account → banking MCP transaction page |
| “Prepare a LOW-priority LOAN_ENQUIRY case for NXC-0001 about home-loan documents. Obtain approval before submitting.” | Extract case details → verify policy and authorized customer → exact proposal with policy evidence → distinct reviewer approval → fresh validation → banking MCP case submission |

The existing LangGraph workflow is the bounded harness: the model interprets
intent and explicit values; application code controls permitted tools, identity,
validation, approval, work limits and recovery. A tool catalog does not grant
permission to call every operation. General customer profile/KYC chat and case
note/escalation chat remain unavailable; prohibited credit decisions remain
forbidden. All five existing service-request categories retain their workflows.

Transaction dates and policy lookup dates are separate. The requested limit is
preserved, ownership is checked before a transaction call, and returned rows
must match the selected account, date interval and maximum count. Multiple
owned accounts trigger a clarification with authorized choices. No subsequent
page is fetched automatically. Transaction records do not enter an LLM prompt.

Case category, priority, customer and summary must be supplied explicitly; a
missing priority is never guessed. The reviewer inspects both the exact proposal
and retained policy evidence. A different authorized identity approves its hash;
a statement such as “obtain approval” does not approve it. Revalidation, leases,
expiry and retries reuse the existing saved-run path and stable idempotency key.
Legacy saved service-request hashes keep their original serialization.

Missing or conflicting inputs return `needs_clarification`, `missing_fields`
and `suggestions`. A follow-up refines only the pending request. **New chat**
or a credential change clears pending customer text. The combined request is
limited to 2,000 characters; UI history stays in the browser session. Named
banking workflows use durable MongoDB runs. Standalone policy requests use
`POST /agent`; authenticated chat uses `/agent/runs` and the existing approval,
load and retry endpoints. No credential or MCP address is accepted from a message.

`AGENT_RETRIEVAL_TRANSPORT=mcp` uses the existing local `retrieve_policy` tool;
`BANKING_TRANSPORT=mcp` uses the existing banking adapter and hosted OAuth
configuration. No new integration dependency or permission expansion is needed.
The ten `evaluation/datasets/agent_chat.jsonl` cases exercise deterministic
routing contracts; they are not live model-quality scores. `make check-full`
checks the complete test suite, type/lint/floor gates, coverage and security.

## Integrated flow — 27 October 2026

All five service-request types below use the same four roles:

```text
Orchestrator → HTTP Retriever → Validator → durable approval interrupt
    → fresh policy/current-facts revalidation → Action → END
```

Policy questions still use Orchestrator → Retriever → grounded answer → END.
Missing scope, forbidden requests, failed validation, and rejected or expired
approvals perform no banking write. The Retriever still calls the existing
`POST /retrieve` through HTTP and preserves the complete response. Answer
generation uses its retained Runnable; it never retrieves a second time.

For an action, the initial HTTP request retrieves once and pauses. The approval
HTTP request freshly retrieves and revalidates without rerunning classification.
A changed proposal or policy-evidence digest blocks execution and requires a
new run/approval. Unconfirmed Action retries reuse the checkpointed operation
without re-running validation after a possibly committed write.

| Endpoint | Behavior |
| --- | --- |
| `POST /agent` | Existing independent policy answer or validation-only proposal |
| `POST /agent/runs` | Start an owner-bound durable run; returns `201` and run status |
| `GET /agent/runs/{run_id}` | Owner or customer-scoped reviewer reads a safe snapshot |
| `POST /agent/runs/{run_id}/approval` | A different authorized reviewer approves/rejects the exact proposal hash |
| `POST /agent/runs/{run_id}/retry` | Reconcile an unconfirmed approved Action with the persisted key/payload |

Configure `AGENT_PRINCIPALS` as JSON with distinct secret tokens and trusted
customer scopes. Replace both placeholders; use secret management and TLS
before deployment. A principal cannot approve its own run, even if it has the
reviewer role. A request body cannot set an owner, reviewer or customer entitlement.
The legacy shared training token can still call `/agent`, but cannot own durable runs.

```dotenv
AGENT_PRINCIPALS=[{"principal_id":"advisor","token":"<owner-token-at-least-32-characters>","customer_ids":["NXC-0010"]},{"principal_id":"reviewer","token":"<reviewer-token-at-least-32-characters>","customer_ids":["NXC-0010"],"can_approve":true}]
AGENT_ALLOW_DIRECT_SUBMIT=false
AGENT_APPROVAL_TTL_SECONDS=900
MONGODB_AGENT_RETENTION_SECONDS=604800
```

Use the callback or loan JSON examples below with `/agent/runs`. Save its
`run_id` and `proposal_hash`. The reviewer posts JSON
`{"decision":"approve","proposal_hash":"<returned-hash>"}` or `reject` to
the approval endpoint using the reviewer's bearer token. Approvals cannot edit
the payload. A successful run returns `status: submitted`, with the bank's
`RECEIVED` receipt under `response.submission` and the retained evidence.
Receipt acceptance is not completion of the underlying customer operation.

MongoDB stores workflow state in dedicated run, checkpoint and pending-write
collections; policy retrieval remains owned by the RAG API. Database calls use
five-second deadlines, majority reads and journaled majority writes. State is
persisted synchronously before the Action node. An atomic lease serializes run
mutations across workers. The operation key and bank destination/caller digest
are saved before the write; retries cannot change them.

The approval window defaults to 15 minutes. An ambiguous POST can be retried
through the same run for less than 23 hours after its first attempt, below the
supplied bank's 24-hour idempotency retention. Afterwards, reconcile with the
bank instead of blindly retrying or generating a fresh key. No automatic retry
or exactly-once guarantee is claimed. Run/checkpoint TTL retention defaults to
seven days, with a minimum of two days; set it to your approved data-retention
policy before production. TTL removal is asynchronous. Old unavailable runs
must not be interpreted as proof that a bank operation never occurred.

The scoped API-key identities are an explicit trust boundary, not a claim of
OIDC/SSO integration. Production gateway identity, rate limits, tenant operations,
general customer/case-note chat and AWS rollout remain
separate work. The [gap analysis](../../.planning/phase2-four-agent-integration.md)
distinguishes these from the implemented Phase 2 path.

The current Streamlit **Service requests** workspace calls these APIs using
`AGENT_API_BASE_URL` (default port 8001). Enter an owner credential to prepare a
request; a different reviewer loads the saved run, inspects its evidence and
confirms an approve/reject decision. Credentials are passed on individual HTTP
requests, never stored on the shared cached client. Changing credentials clears
the loaded proposal. An uncertain submission is recovered through the same run.

Input screening blocks instruction overrides, PAN-like identifiers, long numeric
identifiers and secret-key patterns before calling a model or retrieval tool.
Durable-run requests rejected by this screen return a safe 422 before their
request text is written to checkpoints or the run ledger.
This conservative screen is not general PII detection or a complete injection
defense. Action authorization, exact evidence and deterministic rules remain
mandatory even when the model classifies a request incorrectly. Free-text notes
are excluded from routing prompts and retained only for the fixed bank payload.

`AGENT_MODEL_MAX_CALLS` defaults to three, `AGENT_MODEL_MAX_OUTPUT_TOKENS` to
2048 and `AGENT_MODEL_TOKEN_BUDGET` to 64000. A per-workflow callback reserves
estimated input/output work, including one possible SDK retry, before model
calls. Budget errors follow the existing safe unavailable/error path. These
estimates bound model work; they are not dollar billing or distributed tenant
accounting. Counts cover Agent Service chat calls; downstream embedding/reranking
and tenant billing remain separate. The existing HTTP, graph-step and mutation
deadlines still apply.

The [Phase 1/2 development map](../../C4-Documentation/phase1-phase2-development-map.md)
includes every session, verified profiles, release gates and future MCP/production
extension points without adding unused framework abstractions.

Fast tests use fake models and HTTP/ASGI boundaries plus mongomock. With an
isolated MongoDB instance, set `NEXA_TEST_MONGODB_URI` and run
`pytest tests/test_agent_runs.py tests/test_agent_persistence.py -q` to exercise
the same cases with real persistence and a fresh-process checkpoint read.
The supplied bank's 22 independent OpenAPI/fault-injection tests run with
`pytest nexa_synthetic_data/api/mock_server/test_mock_api.py -q`.

The sections below retain the earlier standalone Validator lesson and its
worked policy examples; its no-write boundary applies to `/agent` and the
Validator, while the integrated run's separate Action node can submit.

## Explicit service-request submission

Compatibility only: this endpoint is **disabled by default**. It requires
`AGENT_ALLOW_DIRECT_SUBMIT=true`; leave that setting false for approved workflows.

`POST /agent` retains the validation-only behavior described below.
`POST /agent/submit` authorizes submission of the supplied action details after
the same retrieval and deterministic validation. It requires the existing
validator bearer token, a server-authorized customer, explicit `customer_id`,
`as_of`, `action: "createServiceRequest"` and `action_details`, plus an
`Idempotency-Key` header of 16–64 ASCII letters, digits, underscores or hyphens.
Opt-in also needs `product_id: "NEXA-HL"`; opt-in and interest certificates need
a selected loan. Use the worked JSON below with this endpoint and header.

The API posts the exact validated proposal to `/v1/service-requests`. A successful
response has `status: "submitted"`, the bank's `service_request` receipt and the
retrieved policy `evidence`. `RECEIVED` means the bank accepted a request; it does
not mean KYC is complete, a certificate has been issued, or an opt-in is effective.
Blocked or insufficient-evidence responses never write. The standalone Validator
still returns approval-required proposals; explicitly calling `/agent/submit`
authorizes this immediate submission without a separate persisted approval flow.

Keep the same key **and unchanged body** when retrying an unconfirmed submission.
A bank timeout or invalid response returns 503 and may follow a committed write.
No automatic retries are performed. The supplied mock retains idempotency keys
for 24 hours; after expiry, replay protection is no longer guaranteed. A new key
means a new request. Bank replay, payload conflicts and commit-then-timeout recovery
are covered by tests against the actual supplied mock through ASGI. This endpoint
adds no new model, UI, persistent approval store or production authentication.

The sections below describe the validation-only path unless stated otherwise.

The earlier standalone Validator lesson followed the user's selected Phase 2 Sessions 1, 2 and 3
decks in `../PPTs/`, and syllabus PDF pages 4–5. Its next implemented agent was
the Session 6 Validator, with the narrow read-only foundations it requires.
The user explicitly chose this ahead of the Session 4 reliability lesson.

## What to teach

| Carry-forward topic | Concrete code |
|---|---|
| Session 1: state, nodes, conditional routing | `orchestrator.py`: policy questions finish with a grounded answer; supported service requests finish with validation |
| Session 2: reuse policy retrieval | `services/agent_retrieval.py`: one HTTP `/retrieve`; retain full amendments, quotes, dates and citations |
| Session 3: strict tool input/output and state | `models/validation.py`: service-request details, trusted access scope, selected loan facts, evidence and proposal |
| Session 6: deterministic Validator | `services/validation.py`: circular applicability, loan ownership/version/date, existing opt-in and server-issued authorization |
| Guardrails | Authorization before any policy/customer retrieval; fixed policy query; no raw customer profile in prompts/results; fixed operation allowlist |
| Observability and evaluation | `validator_opt_in` Datadog workflow; decision-only application log; ten local golden cases and HTTP-mocked tests |

The Validator is application logic, not a second model asked to grant permission.
The classifier may select a route, but cannot add customers to its allowed scope.

## Current paths

```text
Policy question → Orchestrator → HTTP Retriever → grounded answer → END

Service-request proposal → Orchestrator → scope/configuration gate
  → HTTP Retriever → Validator → allowed / blocked / insufficient evidence → END
                           └→ read authorized customer loan over HTTP only

Missing inputs → clarification → END
Forbidden request → refusal → END
Unconnected action/integration → unavailable → END
```

The same Validator node accepts `createServiceRequest` with the five request
types already defined in the supplied banking contract. It never calls a banking
POST endpoint. All paths require server-issued customer access and explicit
policy lookup date; missing details or required loan IDs clarify before reads.

## Other service-request use cases

`STATEMENT_COPY`, `CALLBACK`, `RE_KYC_REMINDER` and `INTEREST_CERTIFICATE` require
the full published permission in `POL-OPS-001` v1.0, effective 2025-01-01, clause
5.1. The Validator checks the evidence identity/date/wording before one authorized
accounts GET. A selected loan must be unique and belong to the customer; its
product must match any supplied product. `INTEREST_CERTIFICATE` requires a loan;
the other three may be customer-level requests with no loan. Unknown request
types and extra action-detail fields are rejected by the model.

For these bank-wide operations the fixed retrieval query has `product_id=null`,
so an optional loan-product filter does not hide the operations policy. The
original product stays in agent state for loan validation. Jurisdiction, lookup
date and top_k still cross the existing retrieval contract unchanged. Prepayment
keeps its original product-scoped query and circular rule. Customer-specific
query text and notes never enter either policy query.

This validates a permitted proposal, not document issuance, callback completion,
KYC completion or a determination that re-KYC is due. The API contract and policy
do not specify additional eligibility thresholds for these requests. Case
creation, case notes and escalation still need their own implementation.

With the same local authorization setup, a customer-level example is:

```json
{
  "query": "Prepare a callback service request.",
  "as_of": "2026-10-01",
  "customer_id": "NXC-0010",
  "action": "createServiceRequest",
  "action_details": {"request_type": "CALLBACK"}
}
```

## Authoritative worked example

`CIR-2026-07`, effective 2026-07-01, clause 5.2 permits existing POL-HL-V3
borrowers sanctioned before that date to request the revised minimum. The
mock API identifies that loan version as `NHL-2025-10`. The request must not
already have opted-in status. The Validator does not invent an additional KYC,
income, floating-rate, loan-amount or credit-approval requirement.

Synthetic customer `NXC-0010`, loan `NXL-HL-0010`, is the positive example:
POL-HL-V3, version `NHL-2025-10`, sanctioned 2025-12-12, not opted in. Counterexamples
include `NXC-0001` (older policy), `NXC-0016` (later sanction/version), `NXC-0023`
(already opted in), and a loan belonging to another customer.

The policy date and sanction date are separate. Policy evidence is validated
against `as_of`; banking facts come from the current accounts response and do
not establish historical customer state. Missing facts or inconsistent responses
never produce an allowed proposal. Policy wording must match the complete
clause 5.2; changed policy requires explicit rule review rather than model guessing.

## Local demo setup

Keep the existing RAG service on 8000 and Agent Service on 8001. From
`nexa_synthetic_data/api/mock_server`, run the supplied mock with:

```sh
uv run --project ../../.. --no-sync uvicorn server:app --host 127.0.0.1 --port 8080
```

Configure local `.env` values (never commit actual tokens):

```dotenv
BANKING_BASE_URL=http://127.0.0.1:8080/
BANKING_API_TOKEN=<nonempty synthetic mock caller token>
AGENT_VALIDATION_TOKEN=<separate random token, at least 32 characters>
AGENT_ALLOWED_CUSTOMER_IDS=["NXC-0010"]
```

Restart the Agent Service after changing these cached settings.

Send `Authorization: Bearer <AGENT_VALIDATION_TOKEN>` to `/agent` with:

```json
{
  "query": "Check my prepayment opt-in eligibility and prepare a service request.",
  "as_of": "2026-10-01",
  "product_id": "NEXA-HL",
  "customer_id": "NXC-0010",
  "action": "createServiceRequest",
  "action_details": {
    "request_type": "OPT_IN_CIR_2026_07_PART_PREPAY",
    "loan_account_id": "NXL-HL-0010"
  }
}
```

The permitted result has outer status `validated`, inner `validation.decision`
`allowed`, evidence copied from the actual retained circular and an exact
`ServiceRequestCreate`-compatible proposal. `requires_approval` is always true.
It does not return a service-request ID or claim that the opt-in has occurred.

The local token authenticates one server-configured training scope. The banking
mock accepts any nonempty token and is not an authorization service. Replace
this training token with verified per-user claims before production. Keep the
two tokens distinct. An empty banking configuration disables the integration;
an absent/wrong caller token or customer outside the allowlist blocks the path
before either retrieval or customer reads. Policy-only requests remain public.

## Tests and trainee walkthrough

```sh
uv run --locked --no-sync pytest tests/test_validation.py tests/test_agent.py -q
```

1. Trace the positive request from explicit inputs to the proposal.
2. Identify which facts come from the circular and which come from the customer API.
3. Remove the date or product and show clarification without retrieval.
4. Remove authorization or change the permitted customer and show zero downstream calls.
5. Try older/later loans, an existing opt-in, missing circular text and a malformed bank response.
6. Verify one policy retrieval, at most one banking GET, and no banking writes.
7. Explain why `validated` is not approval, execution, or a credit decision.

The ten `evaluation/datasets/validator.jsonl` cases run through the deterministic
Validator with HTTP mocks and actual local circular text. They are not live
model-routing scores. Another ten `evaluation/datasets/service_requests.jsonl`
cases use the actual operations policy and HTTP mocks. All five request types
also have read-only checks against the actual mock accounts route through ASGI.
Existing answer/citation tests remain in place. The general path emits a
`validator_service_request` workflow; prepayment retains `validator_opt_in`.

`CONSTRAINTS.md` records blocking baseline coverage and source/secret security
checks. `make check-task` writes coverage once and checks application/diff
baselines; `make check-full` also scans external Semgrep rules. Existing security
findings remain visible in a frozen fingerprint baseline; new findings block.

## Adding another validation to the same node

1. Identify the authoritative policy clause, effective dates and existing API
   operation before defining an eligibility rule.
2. Add typed details and required-scope checks before retrieval. A case operation
   needs its own payload; do not reuse service-request fields for it.
3. Add the operation's deterministic checks behind the existing Validator node.
   Reuse server-issued access, retained policy evidence and validated HTTP reads;
   keep the exact proposal approval-required and perform no banking write.
4. Add positive and negative golden cases, HTTP call-count/error tests and
   ownership checks. Run `make check-task` and `make check-full`; new workflows
   must meet the same coverage and security contract.

## Later sessions

Session 4 retry/backoff and idempotency concepts remain separate from this
read-only Validator. The supplied banking mock already contains replay,
same-key/different-body, and commit-then-timeout examples. Its standalone test
module additionally requires `openapi_spec_validator`, now included in the dev
dependencies and exercised separately; the Validator tests do not depend on it.

The integrated run path above now supplies the Session 7 Action and Session 8
durable approval/resume foundations for these service requests. The standalone
Validator remains read-only. Checkpoints alone do not guarantee exactly-once
effects. Automatic retries, MCP, a new Agent UI and deployment are not added.

## Corrections to the older decks

- Agent Service, HTTP adapter and graph state now exist; old "proposed" labels
  for those components describe the deck's earlier snapshot.
- `RetrieveRequest.as_of` is required; `AgentRequest.as_of` remains optional
  so that the orchestrator can ask for it.
- The actual response is `RetrieveResponse(results, amendments, timings)` with
  nested `Citation`, not `SourceChunk`, `source_id` or `timings_ms`.
- Durable runs now have MongoDB-backed checkpoints; independent `/agent` calls
  remain stateless. Older decks predate the run/approval integration.
- The syllabus's four roles are Orchestrator, Retriever, Validator and Action.
  Customer reads are a tool used by the Validator here, not a fifth agent.

### API compatibility

The current UI uses `/v2/agent` and `/v2/agent/runs` (including saved-run read,
approval and retry). These expose suggestions, transactions and case receipts.
The unversioned endpoints retain their earlier response field shape for strict
clients. Use the v2 endpoints for new chat capabilities. Both versions share
the same run store, ownership checks and approval workflow.
