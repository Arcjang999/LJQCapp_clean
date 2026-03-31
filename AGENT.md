# AGENTS.md

## Project overview
This repository is a QC application for laboratory quality control workflows.
Current stack is mainly:
- Streamlit
- SQLite
- pandas
- matplotlib

The app currently has a stable LJ single-level workflow and is gradually expanding toward other methodologies such as Z-score and instant-method related features.

## Core working principles
1. Prefer small, reversible changes
2. Do not perform large refactors unless explicitly requested
3. Preserve the current stable LJ workflow as the highest priority
4. Reuse existing functions and page structure whenever possible
5. Keep widget keys and business state separate to avoid Streamlit session_state conflicts
6. Any database schema change must be backward compatible with old databases
7. If a requested feature would require major architecture changes, explain that clearly before expanding scope

## Coding expectations
- Read the current flow before editing
- Prefer targeted edits over rewriting large blocks
- Avoid cosmetic UI restyling unless explicitly requested
- Keep naming consistent with the existing codebase
- Preserve current user-visible behavior unless the task explicitly changes it
- Add safe fallbacks for missing old data whenever new fields are introduced

## Database expectations
- Schema changes must include migration/upgrade logic
- Old data must remain readable
- Avoid creating orphan records
- Prefer the minimum-risk design for lifecycle actions such as delete/archive/disable

## UI expectations
- Do not redesign the whole page for a local feature
- Keep new controls close to the existing related workflow
- Use clear validation messages for user input
- Avoid introducing complicated multi-step interactions for a first version

## Reporting and outputs
When implementing exports or report generation:
- Use persisted data rather than temporary page state whenever possible
- Use safe file names
- Provide reasonable fallback text for missing fields
- First version should favor a simple, stable template over a complex layout system

## Required response format for feature tasks
When replying after implementation, use this structure:
1. Requirement understanding
2. Implementation approach
3. Files changed
4. Database changes (if any)
5. Manual test steps
6. Risks / follow-up suggestions

## Do not do these unless explicitly requested
- Do not refactor the whole app structure
- Do not change unrelated methodologies
- Do not replace stable logic with a “cleaner” redesign
- Do not introduce a permission system
- Do not add a full workflow engine, rule engine, or reporting center unless requested