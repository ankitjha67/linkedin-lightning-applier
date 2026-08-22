# Extension E2E tests

Drives the **real** extension in a **real** browser: installs it unpacked,
configures it through its own Settings UI, opens a job posting, clicks
"Fill this page", and reports what actually happened.

This harness found four bugs that unit tests could not — including a consent
question on the live Greenhouse form being auto-answered with the notice period.

## Setup

```bash
cd tests/e2e
npm install          # installs playwright (uses your installed Chrome by default)
```

## Run

```bash
npm test                                   # headless, default live posting
npm run test:headed                        # watch it drive the browser
node extension-e2e.js --url <posting-url>  # any Greenhouse/Lever/Ashby/Workday URL
node extension-e2e.js --headed --keep-open # leave the browser open to poke at
```

Exit code is `0` only when no bugs were found, so it can gate CI.
Screenshots land in your temp dir (the path is printed at the end); override
with `--shots <dir>`.

## Testing logged-in flows

A throwaway profile can't test anything behind a login. Point the run at a
persistent profile instead — sign in once, and the session is reused every run:

```bash
node extension-e2e.js --headed --profile "C:/Users/you/lla-e2e-profile"
```

Sign into LinkedIn or your Workday tenant in that window the first time. This is
the only way to exercise account-gated ATS flows end to end.

## What it checks

| Stage | Assertion |
|---|---|
| Install | MV3 service worker registers (catches `background.js` import errors) |
| Settings | providers load, tier chips work, profile + work auth save **and round-trip** |
| Popup | stats render, autopilot toggle persists, mode footer present |
| Apply | content script receives `FILL_NOW`; identity fields (name/email) fill without an LLM |
| Apply | **a consent/privacy question is never keyword-answered** (regression guard) |
| Accounting | with auto-submit OFF, nothing is marked `applied` (fill-only must not burn the cap) |

Console errors and uncaught exceptions are captured throughout; job-board noise
(React hydration, font preloads, the site's own 401s) is filtered out so only
our failures surface.

## Environment escape hatches

- `LLA_E2E_CHROME=/path/to/chrome` — use a specific browser binary
- `LLA_E2E_NO_PROXY=1` — sandboxes/CI that export an unusable proxy
