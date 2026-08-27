# Contributing

Thanks for looking at this. The project is a job-search bot that handles real
credentials and acts on real employers' systems, which shapes most of what
follows.

## Getting set up

```bash
git clone https://github.com/ankitjha67/linkedin-lightning-applier
cd linkedin-lightning-applier
pip install -e ".[all,dev]"
cp config.example.yaml config.yaml     # gitignored — put your real values here
python cli.py doctor                   # checks dependencies and the Chrome driver
```

## Before every commit

```bash
python tests/run_tests.py            # the whole suite
python tools/security_guards.py      # no secrets escaping into git
python tools/lint_skills.py          # .claude/ skills and commands still valid
python -m ruff check $(cat .ruff-paths | tr '\n' ' ')
```

CI runs all four on every push, across Python 3.10, 3.11 and 3.12. `.ruff-paths`
lists the modules held to the lint standard; older files carry pre-existing
style debt and are excluded rather than mass-reformatted. **A new module should
be added to `.ruff-paths`** — the list only shrinks by accident.

## Tests

Every change needs tests, and the suite is the reason it is safe to move
quickly here. A few habits that have paid off:

- **Test what breaks, not what is easy to assert.** The valuable tests in this
  repo are the ones pinning behaviour that was wrong once: that a polite
  rejection is not read as an interview invitation, that `parse_when("")`
  returns `None` rather than "now", that a skills list is not detected in the
  sentence "go to the store".
- **Prefer the real thing over a mock.** `tests/test_verify_pdf.py` builds
  actual PDFs and reads them back. `tests/test_outcomes.py` uses a real SQLite
  database in a temporary directory. Mocks test that the mock works.
- **Guard against drift with a test.** Where a fact is necessarily repeated —
  the positive-outcome set spread across ten SQL queries, the placeholder names
  shared between the template engine and its checker — a test asserts the
  copies agree. Both of those have already caught a real bug.
- Name tests as sentences: `test_a_terminal_outcome_closes_the_application`.
  When one fails, the name should tell you what broke.

## Things that need care

**Secrets.** Read `SECURITY.md` before touching anything that loads config,
writes to `data/`, or handles a password. Never commit `config.yaml` or `.env`.

**Other people's servers.** New network fetching goes through
`tools/robots_check.py`. If a site has not granted permission, we do not fetch —
including when permission could not be confirmed.

**The outcomes table.** Eight modules learn from `response_tracking`. Writing a
wrong outcome to it is worse than writing none, because it silently degrades
every prediction downstream. Anything that writes there should be able to say
where the information came from.

**Destructive operations.** Reset, overwrite and delete paths default to a dry
run, back up first, and require explicit confirmation. Keep it that way.

## Style

Match the file you are editing. Beyond that:

- Comments explain *why*, not what. If a line needs a comment to say what it
  does, the line is usually the problem.
- Docstrings on modules and non-obvious functions. Say what the thing is for
  and what would go wrong without it.
- Errors should say what to do next, not just what failed.
- Fail closed on anything involving permission, credentials, or data loss.

## Adding a CLI command

1. Write the logic in a module, not in `cli.py` — it stays testable and
   reusable from the MCP server.
2. Add `cmd_<name>` to `cli.py`, register it in `COMMAND_MAP`, and add its
   parser in `build_parser()`.
3. Return an `int` for a non-zero exit code; returning `None` exits 0.
4. Add it to the examples in the CLI epilog and to the README.
5. If it is useful to an assistant, add a thin wrapper in `tools_layer.py` and
   expose it in `mcp_server.py`.

## Pull requests

Explain what changed and why it was worth changing. If you fixed a bug, say
what the wrong behaviour was — that sentence usually belongs in a test name
too. Keep unrelated changes in separate commits.
