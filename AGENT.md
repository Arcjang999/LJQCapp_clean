# AGENTS.md

## Project overview
This repository contains a Streamlit QC application.

Current reality of the project:
- Single-level LJ / Westgard workflow is the stable baseline.
- Multi-level zscore workflow is under active development and is NOT yet as stable as single-level LJ.
- Realtime / immediate method is future work.
- Stability and reversibility are more important than elegance or broad refactoring.

The assistant must treat this repository as a high-risk application where broad rewrites are unacceptable unless explicitly requested.

---

## Top-level development policy
Always prefer:
1. preserving working behavior
2. making the smallest possible change
3. isolating new logic
4. avoiding wide diffs
5. keeping changes easy to review and easy to revert

Do NOT optimize for elegance if it increases change scope.

Do NOT replace large working sections just because a new structure looks cleaner.

---

## Stable baseline protection
The single-level LJ / Westgard workflow is the protected baseline.

Unless the user explicitly asks otherwise:
- Do not break or redesign single-level LJ workflows
- Do not change single-level plotting behavior
- Do not change single-level export behavior
- Do not change single-level maintenance behavior
- Do not change single-level page structure just to align it with multi-level workflows

If working on multi-level features, preserve the single-level baseline exactly.

---

## High-risk files
These files are high risk and must be edited conservatively:
- `app.py`
- `database.py`

### Rules for `app.py`
- Do NOT rewrite large sections of `app.py`
- Do NOT reorganize the whole page structure unless explicitly requested
- Do NOT delete existing working blocks unless the user explicitly asks for removal
- Do NOT move top-level helpers unless required
- Do NOT convert local UI tasks into broad page rewrites
- Prefer patch-style edits to small local sections
- If a UI change affects multiple large areas, stop and plan first

### Rules for `database.py`
- Do NOT change schema unless the task explicitly requires it
- If schema changes are required, make the smallest migration possible
- Preserve backward compatibility for existing single-level LJ data
- Avoid broad migration rewrites
- Avoid changing existing stable read/write paths unless necessary

---

## Absolute prohibitions
Unless the user explicitly asks for it, do NOT:
- Rewrite the whole `app.py`
- Replace an entire workflow for a small feature request
- Delete working code because it "looks old" or "seems redundant"
- Perform unrelated cleanup
- Perform broad refactors
- Rename large groups of functions for style reasons
- Reorganize file layout as a side effect of a small task
- Mix feature work, refactor, and cleanup in one change
- Submit giant diffs for small UI/display tasks
- Rewrite stable single-level code while working on multi-level features

If you think a task requires such a broad change, STOP and propose a plan first.

---

## Change-size rules
Keep changes small and reviewable.

Rules:
- Only modify the files needed for the requested task
- Only modify the functions needed for the requested task
- Do not replace a whole function if a local edit is enough
- Do not replace a whole file if a small patch is enough
- Do not introduce sweeping structural changes during feature work

### Large-change threshold
If a change would:
- touch multiple large sections of `app.py`
- delete/replace existing page structures
- combine UI changes with logic changes
- or create a large diff for a narrowly scoped request

then DO NOT edit immediately.

Instead:
1. inspect the existing code
2. provide a short implementation plan
3. name the files/functions that would change
4. wait for user approval before editing

---

## Planning rules
For any task involving:
- `app.py`
- `database.py`
- Streamlit widget/session state
- page routing
- multi-level maintenance
- chart/view switching

you must:
1. inspect current implementation first
2. reuse existing stable patterns where possible
3. prefer consistency with stable single-level LJ patterns
4. give a short implementation plan if the task is risky
5. implement one milestone at a time

### Milestone rule
For multi-step work:
- Do not implement the entire feature in one pass
- Split into small milestones
- Finish one milestone
- Verify it
- Then continue

---

## Streamlit state rules
This project is sensitive to Streamlit widget/session state issues.

Never treat a widget key as a freely writable business state variable.

Rules:
- Do NOT directly modify `st.session_state[widget_key]` after a widget with that key has been instantiated
- Separate widget keys from business-state keys
- Use pre-widget synchronization patterns
- Use rerun-safe state handling
- Reuse already stable selector/state-sync patterns from the codebase
- Do not invent a new widget-state pattern if a working one already exists in the repository

Before editing stateful UI:
- inspect current stable state-sync helpers first
- prefer copying an existing safe pattern over inventing a new one

---

## Multi-level development rules
Multi-level zscore development must be isolated and incremental.

Preferred approach:
- Add isolated helper functions
- Add separate UI sections or separate module files
- Keep multi-level logic clearly separated from single-level logic
- Avoid turning shared code into deep nested `if/else`
- Avoid broad rewrites of the main page
- Prefer routing / module separation over growing one giant `app.py`

If possible:
- keep `app.py` as a thin routing layer
- move methodology-specific page rendering into dedicated modules

---

## Methodology separation rule
Treat these as distinct methodologies:
- `lj_single`
- `zscore_multi`
- `realtime`

Do NOT force them into one giant shared page structure unless explicitly requested.

Preferred direction:
- a shared entry/navigation layer
- separate rendering modules for each methodology
- isolated feature development within each methodology

For multi-level UI tasks:
- prefer editing the multi-level module/section only
- do not reshape the single-level section

---

## Deletion / replacement policy
If any existing code is removed or replaced, you must explicitly report:
1. what was removed
2. why it was safe to remove
3. what replaces it
4. whether behavior changed

Never silently replace large working sections.

Never claim a broad rewrite is “minimal”.

---

## Before editing
Before making changes, always:
1. identify the smallest scope needed
2. identify the exact files/functions required
3. inspect whether a similar stable pattern already exists
4. prefer extension over replacement
5. prefer isolation over merging unrelated logic

---

## After editing
After making changes, always report:
1. short implementation summary
2. files changed
3. functions changed
4. whether any existing code was deleted or replaced
5. whether any user-visible behavior changed
6. what was tested
7. what was NOT tested

Be specific and honest.

---

## Testing expectations
Run the smallest relevant verification first.

Examples:
- `python -m py_compile ...` for syntax
- focused smoke tests for changed area only
- narrow manual checks for the changed UI region

Do NOT imply broad validation if only narrow checks were done.

Be explicit:
- what was tested
- what was not tested
- what remains risky

---

## Git / safety workflow
Prefer safe, reversible development.

Recommended workflow:
- create a checkpoint before risky edits
- make one focused change at a time
- keep diffs easy to review
- keep diffs easy to revert

If a task is risky:
- stop after the first safe milestone
- let the user review before continuing

---

## Preferred reporting style
For coding tasks:
- be concise
- be explicit about scope
- do not oversell
- do not claim structural improvements unless they were explicitly requested
- do not claim cleanup as a benefit if it increased risk
- clearly separate “feature added” from “code reorganized”

---

## Priority order
When tradeoffs appear, prioritize in this order:
1. preserve working behavior
2. keep changes small
3. keep changes reversible
4. maintain clarity
5. improve UX
6. improve elegance

Stable and reviewable is better than clever and broad.

## QC domain rules
- Z-score in this app follows the QC workflow, not a generic manual calculator model.
- Raw QC results are entered first.
- Target mean and target SD are derived from target establishment.
- Target-establishment status is batch-level, not level-level.
- Level 1 and Level 2 are the primary working levels; Level 3 is reserved.