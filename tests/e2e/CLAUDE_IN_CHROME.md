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
