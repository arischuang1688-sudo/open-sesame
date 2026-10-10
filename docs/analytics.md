# Anonymous analytics: deployment and recovery

The dashboard sends events to `https://open-sesame-trigger.vercel.app/api/event`;
`api/trigger` records dispatch, join and cooldown outcomes. Both write JSON
comments to `arischuang1688-sudo/open-sesame#2`. `api/stats` aggregates those
comments for `admin.html`. Random browser visitor/session IDs are unchanged;
no names, email addresses, IPs or additional personal data are collected.

## Confirmed production incident (2026-10-10, Asia/Taipei)

- Existing production deployment was `dpl_AorE8xkW7Ub5foEyRRwsyRJwNUEB`.
- Before the patch, stats returned HTTP 200 and zero recorded events; a valid
  page-view POST returned HTTP 502 without actionable upstream details.
- After installing diagnostic version `2026-10-10.1`, a no-personal-data
  `analytics_probe` failed with GitHub HTTP 403 and
  `Resource not accessible by personal access token`.
- GitHub reported accepted permissions `issues=write; pull_requests=write`
  and 4,994 remaining requests. This is an access denial, not rate exhaustion.
- Correlation: `error_id=1d50010e-7d45-471b-9e95-4e7f25fdfbe4`,
  `github_request_id=A592:DADB1:50039B3:105ECD16:6AC9F162`.
- Issue #2 remained empty. The deployed code and read API are working, but
  production persistence is **not repaired until the token permission is fixed**.
  Historical visits and clicks cannot be reconstructed from an empty event log.

## Required owner action

Edit the existing fine-grained personal access token used by the Vercel project's
production `GITHUB_TOKEN`: retain access to `arischuang1688-sudo/open-sesame` and
its existing permissions, and enable repository **Issues: Read and write**.
Do not broaden the token to all repositories. Do not paste its value in chat,
commit it, or print it in logs. If replacing the token is necessary, enter it
in the existing Vercel production environment setting and redeploy.
Changing permissions on the same token does not require changing the stored value.

Official permission reference:
https://docs.github.com/en/rest/issues/comments#create-an-issue-comment

The current connector cannot change a user's personal access token permissions.

## Verification after the permission correction

1. POST `/api/event` with JSON `{"event":"analytics_probe"}` and the allowed
   Origin `https://arischuang1688-sudo.github.io`.
2. Require HTTP 202, `ok:true`, and a `comment_id`; confirm that exact ID and
   `analytics_probe` body in Issue #2.
3. GET `/api/stats`; require HTTP 200, the expected version, and an incremented
   `diagnostic_events`. Probes deliberately do not increment visitor/usage totals.
4. Visit the dashboard in a fresh session, then check the admin page against
   the stored `page_view`. Use a real intended manual update when verifying
   live click/dispatch/join counters; the offline tests cover all three branches
   without running the stock update workflow.

## Error contract and scope

- Failed event writes return a non-2xx response with a stable `code`, random
  `error_id`, upstream status and GitHub request ID.
- Runtime logs contain those fields, allowed GitHub error messages, required
  permissions and rate-limit metadata. They never contain tokens or event payloads.
- Trigger responses preserve the existing dispatch/join/cooldown result and add
  `analytics:{ok:...}`. Analytics failure must not retry or undo a successful dispatch.
- The browser logs failed sends and only sets its session page-view marker after
  successful persistence. It does not automatically retry writes that may have saved.
- Empty storage displays dashes and an explicit missing-records notice in admin.
  Read failures and the existing scan limit fail visibly rather than presenting
  partial/failed reads as cumulative zero totals.
- Stock selection, market data generation, ranking, cooldown and scheduling
  logic are unchanged.

Tests: `node --test tests/test_analytics.mjs` (Node 24), plus the existing
`python -m unittest discover -s tests -p 'test_*.py'`. Both run in regression
and release workflows without external requests.
