# Security

This project holds more sensitive material than most tools of its size. Before
anything else, know what is on your disk once you have run it:

| What | Where | Why it matters |
|---|---|---|
| LinkedIn password, API keys | `config.yaml`, `.env` | full access to your account |
| Real ATS account passwords | `data/ats_accounts.xlsx` | working logins at employers |
| Everywhere you have applied | `data/state.db` | a complete record of your job hunt |
| Your CV, diplomas, references | `documents/` | identity documents |
| A live browser session | `chrome-lla-profile/` | logged-in cookies |

All of it is kept out of git by `.gitignore` alone. One careless edit and it is
public and permanent — a pushed credential must be treated as compromised even
if the commit is deleted, because it was public while it was there.

## Before you commit

```bash
python tools/security_guards.py
```

This runs in CI on every push, and checks that:

1. `.gitignore` still carries every personal-data rule, and nothing re-includes
   them with a `!negation`;
2. no secret-bearing file is tracked right now;
3. `.claude/settings.json` grants no pre-approved permissions or hooks —
   those auto-approve commands for everyone who clones the repo;
4. no obvious hardcoded key is in tracked source.

The guards make dangerous changes loud, not impossible. A change that genuinely
needs one must update the allowlist in `tools/security_guards.py` **in the same
diff**, so the decision is explicit and reviewable rather than silent.

## Credentials

Read secrets from the environment, never from source:

```bash
export OPENROUTER_API_KEY=...
export GEMINI_API_KEY=...
```

`config.yaml` and `.env` are gitignored and must stay that way. `config.example.yaml`
is the template — it must never contain a real value.

ATS account passwords are generated locally by `credential_vault.py`, stored in
`data/ats_accounts.xlsx` with file mode `0600`, and never transmitted anywhere
except to the ATS you are registering with.

## What this bot does to other people's systems

It logs into sites as you, fills forms as you, and — if you enable it —
registers accounts on ATS platforms on your behalf. That is your identity and
your responsibility.

- **`robots.txt` is honoured.** `tools/robots_check.py` implements RFC 9309 and
  fails closed: if permission cannot be confirmed, the answer is no. Run
  `lla robots <url>` to see what a site permits. It reports; it never overrides
  a site that has said no.
- **Automated account registration is off unless you turn it on.** Some ATS
  tenants prohibit it in their terms. Accepting those terms is a decision only
  you can make, for each platform — this project will not make it for you.
- **Rate limits and human pacing exist for a reason.** Do not remove them to go
  faster; that is the difference between using a tool and being an abuse
  incident.

See `TERMS_OF_USE.md` and `DISCLAIMER.md` for the full position.

## Reporting a vulnerability

Open a GitHub issue for anything that does not itself expose a secret. For
something sensitive — a way to exfiltrate credentials, or a flaw that would
affect other users of this code — please report it privately through GitHub's
security advisory page for this repository rather than in a public issue.

Please include what you did, what happened, and what you expected. A proof of
concept helps enormously.

## If you have already committed a secret

1. Treat it as compromised. Rotate it now — revoke the API key, change the
   password. Removing the commit does not un-publish it.
2. Then clean the history (`git filter-repo`, or GitHub support for a fork
   network), and force-push.
3. Run `python tools/security_guards.py` to confirm nothing else is tracked.

Rotating first matters more than cleaning the history. History cleanup without
rotation leaves a live credential in every clone and cache that already has it.
