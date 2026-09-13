# Testing with Claude for Chrome

The automated harness (`extension-e2e.js`) runs in a throwaway browser with no
logins. That covers everything up to the point where a site asks *who you are*.

Claude for Chrome runs in **your** browser, with **your** sessions — so it can
test the parts nothing else can: LinkedIn Easy Apply while logged in, a Workday
tenant where you already have an account, and the actual Submit click.

## Division of labour (be realistic)

| Step | Who |
|---|---|
| Load the unpacked extension | **You** — `chrome://` pages are off-limits to extensions, so Claude can't do this |
| Configure it in Settings | You or Claude (the options page is a normal page) |
| Navigate postings, fill, inspect, report | **Claude** |
| Click Submit on a real application | **You**, after Claude reports it's correct |

## One-time setup (yours, ~1 minute)

1. `chrome://extensions` → enable **Developer mode** → **Load unpacked** →
   select `E:\Python\linkedin-lightning-applier\browser_extension`
2. Click the ⚡ icon → **Settings** → fill in your profile, work authorization
   (citizenship + any visas), CV summary, resume PDF, and an LLM provider →
   **Save**
3. Leave **auto-submit OFF** for testing.
4. Open a job posting you'd genuinely consider, in a normal tab.

## Humanised behaviour (paste this FIRST, before any task below)

Claude for Chrome acts in your real browser, on your real account. How fast it
moves matters as much as what it does — both for your account and for the sites
on the other end. Paste this block ahead of whichever prompt you are running.

Regenerate it any time with `lla pacing` (or `--profile careful` / `fast`); it
is generated from `human_pacing.py`, so it always matches what the Selenium bot
does. A test fails if this copy drifts.

<!-- BEGIN GENERATED PACING: regenerate with `lla pacing --write` -->
**Pace yourself like a person — this is not optional**

You are acting in my real browser, signed into my real account, applying to
real employers. Speed is what gives automation away, and it is also what makes
automation rude. Slower and fewer is the whole idea — do not try to be quick.

- **Read before you act.** Spend time on a posting proportional to its length
  — roughly 220 words a minute, so a 600-word description is about 164
  seconds. Never click Apply on a page you have just opened. If you would not
  have finished reading it, you are early.
- **Type, do not paste.** Enter free-text answers a character at a time,
  around 35-65 words a minute, with a beat between words and a longer one
  after each sentence. A sentence like "I'm drawn to this role because the
  team owns the risk models end to end, which is what I did at my last job."
  should take roughly 35 seconds, not an instant. Short fields — name, email,
  phone — can be quick; nobody composes those.
- **Leave gaps between jobs.** 3-8 seconds between postings, 5-15 seconds
  between searches. Vary them: identical gaps are as distinctive as no gaps.
- **Scroll to what you click.** Bring an element into view, let the page
  settle, then click it. Do not click something that was never on screen.
- **One tab, one thing at a time.** Never open several postings in parallel or
  act on a background tab. Doing two things at once is the clearest possible
  signal that nobody is at the keyboard.
- **Take breaks.** After 5-8 applications, stop for 5-15 minutes. Keep it
  under 12 an hour. When my daily cap is reached, stop for the day — do not go
  looking for more.
- **Keep human hours.** Work inside the hours a person would be awake and job
  hunting. Applying at 4am every night is not disguised by anything above it.

**Stop immediately and tell me if you see any of these**

  - captcha
  - recaptcha
  - hcaptcha
  - are you a robot
  - verify you are human
  - security check
  - checkpoint/challenge
  - unusual activity
  - suspicious activity

Do not wait and retry, do not reload, do not try another route, and do not
attempt to solve a CAPTCHA. The site is asking whether a person is here. Hand
it back to me and stop — that is the line between automating my own job search
and something I did not ask for.

**Never fake mistakes.** Do not invent typos or wrong answers to look human.
This is a real application to a real employer, and a plausible-looking wrong
answer is the worst outcome there is.
<!-- END GENERATED PACING -->

## The prompt — paste this into Claude for Chrome

> You are testing a browser extension called **Lightning Applier** that
> auto-fills job application forms. It is already installed and configured. Your
> job is to find bugs, not to praise it. Work through the checklist below on the
> posting in the current tab and report findings precisely.
>
> **Rules — read first**
> - **Never click "Submit application", "Send application" or any final submit
>   button.** Stop before it and tell me instead. This is a real application to a
>   real employer.
> - Don't invent data. If a field is wrong, quote the exact label and the exact
>   value that was filled.
> - If something is the job board's own bug (a React error that was already in
>   the console before the extension ran), say so — don't attribute it to us.
>
> **Checklist**
> 1. Describe the form before anything happens: how many text fields, dropdowns,
>    file inputs, and what the required (*) fields are.
> 2. Open the extension popup and click **"Fill this page"**. Wait ~15 seconds
>    for it to finish.
> 3. Go field by field. For **each** field, report: the label, the value filled
>    (or "empty"), and whether the value is *correct, wrong, or acceptably
>    blank*. Be strict — a plausible-looking wrong answer is the worst outcome.
> 4. **Regression checks — these are known past bugs, confirm they stay fixed:**
>    - Any consent / privacy / GDPR question (e.g. "Keeping your data safe…")
>      must be **left blank or answered sensibly** — it must NOT contain a notice
>      period, salary, or city.
>    - "Are you authorised to work in <country>?" must reflect my actual
>      citizenship and visas, not a blanket Yes.
>    - "Do you require visa sponsorship?" must be the **inverse** of the
>      authorisation answer for the same country.
>    - The resume must be attached (the page should show the filename).
> 5. Check the popup again: with auto-submit OFF, the **"Applied today" counter
>    must not have increased** — filling is not applying.
> 6. Report any red errors in the DevTools console that appeared **after** the
>    fill and mention the extension (`filler.js`, `background.js`).
>
> **Output format**
> - A table: Field | Filled value | Verdict (✅ correct / ❌ wrong / ⬜ blank-ok)
> - Then: `BUGS FOUND:` a numbered list, each with the field, what happened, and
>   what should have happened. Say "none" if there are none.
> - Then: `SAFE TO SUBMIT: yes/no` — and if yes, remind me that *I* have to click
>   it.

## Follow-up prompts worth running

**Logged-in LinkedIn Easy Apply** (the automated harness cannot reach this):

> Go to a LinkedIn job with an Easy Apply button while I'm logged in. Click Easy
> Apply, then run the extension's "Fill this page" on each step of the modal.
> Report every question and the value filled, and stop before the final Review /
> Submit step. Flag anything that looks auto-filled but wrong.

**Workday tenant with an existing account:**

> Open <your Workday posting URL>. I already have an account with this tenant.
> Sign in, start the application, and run "Fill this page" on each wizard page.
> Report per page what filled and what didn't, especially the custom dropdowns
> and the self-identification section. Do not submit.

**Cross-board sweep:**

> Here are 5 job URLs across Greenhouse, Lever, Ashby, Workable and SmartRecruiters.
> For each: open it, run "Fill this page", and report the fill rate (fields
> filled / fields present) plus anything filled incorrectly. Summarise as a table
> at the end, ranked worst-to-best, so I know which ATS handler needs work.

## Reporting back

Paste Claude's `BUGS FOUND:` list back into the Claude Code session working on
this repo. Each finding wants: the ATS, the exact field label, the wrong value,
and the expected value — that's enough to write a failing test and fix it, the
same way the "30 days in a GDPR field" bug was found and fixed.
