# Setup & Launch

Zero-to-running, with automatic dependency + Chrome-driver handling. Works on
Windows, macOS, and Linux. Commands below use `python`; on macOS/Linux use
`python3`.

## 1. Get the code

```bash
git clone https://github.com/ankitjha67/linkedin-lightning-applier.git
cd linkedin-lightning-applier
```

## 2. Virtual environment

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate
```

## 3. Environment doctor — auto-detect & install everything

```bash
python cli.py doctor --fix
```

This is the important step. It:

- checks your **Python** version (needs ≥ 3.10),
- **auto-detects your installed Google Chrome** version (so the driver pins to
  it — no more "ChromeDriver only supports Chrome version NNN"),
- lists every required/optional package and **installs the missing ones**
  (`--fix`), one at a time so one failure never blocks the rest, refreshing
  pip/setuptools/wheel first,
- reports optional tools (LaTeX engine, `pdftotext`, Ollama/LM Studio).

Add `--upgrade` to also bring the browser stack (`undetected-chromedriver`,
`selenium`) up to date — do this if Chrome just auto-updated and the bot stops
launching:

```bash
python cli.py doctor --upgrade
```

Prefer pinned installs instead? `pip install -e ".[all]"` works too.

**Prerequisites the doctor can't install for you:** Google Chrome itself, and —
only if you want them — a LaTeX distribution (MiKTeX/TeX Live, for `lla docs`
PDFs) and a local LLM server (Ollama or LM Studio).

## 4. Configuration (both files stay local, never committed)

```bash
# Windows
copy config.example.yaml config.yaml
copy .env.example .env
# macOS/Linux
cp config.example.yaml config.yaml && cp .env.example .env
```

Edit **`config.yaml`**:
- `linkedin.email` / `linkedin.password`
- `personal.*` (name, contact, city, country)
- `work_authorization.citizenship` + any `visas` you hold
- `ai.cv_text` — paste a real CV summary (drives tailoring quality)
- `resume.default_resume_path` — path to your CV PDF
- `search.search_terms` / `search.search_locations`
- Leave `browser.chrome_version: null` — it auto-detects.

Edit **`.env`**: paste your `OPENROUTER_API_KEY` (and any other provider keys).

## 5. Verify before launch

```bash
python cli.py doctor            # should end with "READY"
python cli.py validate-config   # should say "Config is VALID"
python cli.py test-llm          # should print a reply from your model
```

## 6. Launch

```bash
python main.py --once           # one full cycle, then exit — do this first
python main.py                  # continuous mode (Ctrl+C to stop)
```

First run opens Chrome and logs into LinkedIn — complete any captcha/OTP
manually once; the session persists after that. If Chrome auto-updates later and
launch fails, the bot self-heals (re-pins the driver, and with
`browser.auto_update_driver: true` upgrades the driver package and retries); if
it still can't, run `python cli.py doctor --upgrade`.

## 7. Daily automation (Windows Task Scheduler)

```powershell
schtasks /create /tn "LLA Daily" /sc daily /st 09:00 ^
  /tr ".venv\Scripts\python.exe main.py --once -c config.yaml"
```

(macOS/Linux: use the included `setup_cron.sh`.)

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Missing dependencies" on startup | `python cli.py doctor --fix` |
| "ChromeDriver only supports Chrome version N" | auto-healed; if it persists, `python cli.py doctor --upgrade` |
| "Chrome NOT FOUND" in doctor | install Google Chrome |
| `test-llm` fails | check `.env` key / `ai.provider` / `ai.model` |
| `lla docs` writes `.tex` but no PDF | install MiKTeX/TeX Live (`moderncv`) |
