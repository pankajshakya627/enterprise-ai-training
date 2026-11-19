# NexaAssist application field guide

A static explanatory application and article for the reviewed NexaAssist source snapshot. It includes an application overview, 22 source-backed architecture diagrams, five interactive workflow walkthroughs, setup/authentication guidance and explicit current limits. It makes no model or banking requests.

## Read locally

In the application checkout, open `docs/architecture/index.html` or `docs/architecture/blog.html`. In the guide-only deployment branch, the corresponding files are `public/index.html` and `public/blog.html`. Both work from a local file URL; the article also works without JavaScript. No package install or build is required to view the site.

## Source and accuracy

Reviewed on 19 November 2026:

- Application: `feat/prepayment-validator @ bc87167`, including reviewed uncommitted work.
- Companion banking service: `codex/claude-user-approved-writes @ 3fb1a35`, including reviewed uncommitted work.
- All 76 implementation-file hashes from the original guide remain unchanged at review time.

`public/architecture-guide.md` contains the detailed map and Mermaid blocks. `public/verification.json` records the source hashes, SVG hashes, checks and limits. Implementation references are local source paths, not links to unpublished source files. This deployment contains no application source, customer datasets, environment files or credentials.

Source review is not deployed-image certification. OIDC, per-user document ACLs, immutable corpus generations, independent long-retention audits, distributed admission control, production telemetry redaction and controlled release bundles are not represented as completed work.

## Verify

The test dependencies are temporary tools, not site runtime dependencies:

```bash
npm install --prefix /tmp/nexa-guide-qa --ignore-scripts --no-audit --no-fund \
  jsdom@31.0.1 axe-core@4.14.1
# Guide-only deployment branch:
NODE_PATH=/tmp/nexa-guide-qa/node_modules node verify.cjs public
# Application checkout:
NODE_PATH=/tmp/nexa-guide-qa/node_modules node docs/architecture/verify.cjs docs/architecture
```

The checks exercise page routes, all 22 diagram selections, search/empty states, zoom state, Mermaid export data, all 22 workflow stages, previous/next controls, deep-link bounds, skip-link focus, static links and SVG hashes. Axe runs against the DOM; incomplete checks remain reported. This does not certify rendered WCAG compliance, real keyboard traversal, responsive layout or browser downloads.

Relevant existing application tests passed: **479**, covering answer validation, staff login, case notes, transactions, banking MCP and durable runs. These mocked tests are not live external-service evidence. The wider ECC documentation audit reports **137 integrity issues**, largely historical snapshots and legacy links; it is not a passing repository-wide documentation audit.

## Deploy to Render

Use a **new Static Site**, separate from the banking API. Connect the guide-only `codex/architecture-guide` branch in `pankajshakya627/enterprise-ai-training`.

| Setting | Value |
| --- | --- |
| Name | `nexaassist-guide` |
| Branch | `codex/architecture-guide` |
| Build command | `true` |
| Publish directory | `public` |
| Environment | `SKIP_INSTALL_DEPS=true` only |
| Automatic deployment | Off |

The root `render.yaml` contains the equivalent Blueprint. No database, model, staff, bank or OAuth credentials are needed. Render's [static-site documentation](https://render.com/docs/static-sites) and [Blueprint specification](https://render.com/docs/blueprint-spec) define these settings. Do not repurpose the existing banking service.

## UI design contract

- Job: explain current behavior and make service boundaries and request lifecycles inspectable.
- Reference: the existing project's cyan accent and monospace code treatment; a light reading surface for long-form documentation.
- Navigation: an overview, an architecture explorer, read-only workflow walkthroughs, operations, current limits and a separate static article.
- States: selected page/diagram/step, zero search results, unavailable diagram data, disabled workflow boundaries and no-JavaScript article access.
- Accessibility: native links/forms/buttons/details, labelled landmarks, ordered headings, skip links, focus handling, reduced motion and computed text-token contrast above 4.5:1. DOM scanning is supporting evidence, not full certification.
- Responsive target widths: 320, 768, 1024 and 1440 pixels; diagrams scroll within their own figure. Real rendering at those widths remains pending because the user requested their existing Chrome and the connector is unavailable.
- Keep framework and runtime dependencies out of this static site. Do not add live banking controls to educational examples.

## Remaining verification

Before calling the deployment complete, verify the current UI in the user's existing Chrome, then confirm a successful Render deployment and its public HTTPS URL. Those steps are pending. No alternative browser session was started for this site.
