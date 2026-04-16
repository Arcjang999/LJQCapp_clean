# Demo QC Data Generator

`scripts/generate_demo_qc_data.py` builds a deterministic demo dataset for manual device/browser testing without changing any core business logic or database schema.

## What it creates

- `[DEMO] LJ 建靶演示`
  - `DEMO-LJ-BUILD-202604`
  - 19 building-stage records with one obvious outlier.
- `[DEMO] LJ 正式期 202603`
  - `DEMO-LJ-FORMAL-202603`
  - 20 building points in February 2026 plus 50 March 2026 formal records.
- `[DEMO] ZS 建靶演示`
  - `DEMO-ZS-BUILD-202604`
  - 19 two-level building runs with one clearly abnormal run.
- `[DEMO] ZS 正式期 202603`
  - `DEMO-ZS-FORMAL-202603`
  - 20 building runs in February 2026 plus 50 March 2026 formal runs.
- `[DEMO] Instant 演示`
  - `DEMO-INSTANT-BUILD-202604`
  - 19 effective instant-method records with one suspicious outlier and `Ct` input type.

## Usage

Run from the repository root:

```powershell
python scripts/generate_demo_qc_data.py
```

Write into a specific database file:

```powershell
python scripts/generate_demo_qc_data.py --db .\data\demo_manual_test.db
```

Append another copy of the same demo datasets:

```powershell
python scripts/generate_demo_qc_data.py --db .\data\demo_manual_test.db --on-conflict append
```

Safely write into the database that the current app configuration is using:

```powershell
python scripts/generate_demo_qc_data.py --use-current-app-db
```

Safely write into the current app database and replace only matching demo datasets:

```powershell
python scripts/generate_demo_qc_data.py --use-current-app-db --on-conflict replace-demo-only
```

Use a different deterministic seed:

```powershell
python scripts/generate_demo_qc_data.py --seed 20260301
```

## Default output location

If `--db` is omitted, the script writes to:

`data/demo_qc_data.db`

This default path is intentionally separate from the runtime application database so the script does not silently pollute production or real testing data.

## Conflict handling

- `append`: create a new demo project with a numeric suffix like `#2`.
- `skip`: if an exact demo project name already exists, reuse it and do not create a duplicate.
- `replace-demo-only`: delete only matching demo projects for that dataset and recreate them. Other non-demo data is kept.
- `replace`: compatibility alias for `replace-demo-only`.

## Viewing the data in the app

- The safest path is to generate into the isolated default DB and then point the app to that file in the settings page.
- If you want the data to appear in a specific test database immediately, pass that database path with `--db`.
- To write directly into the database the app is currently configured to use, run `--use-current-app-db`. This mode creates an automatic timestamped backup in the same directory before writing.
- Avoid pointing `--db` at a production database unless you explicitly want to add demo records there.
