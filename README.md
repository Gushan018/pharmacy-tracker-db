# Pharmacy Tracker DB

Interactive inventory & transaction management dashboard for a pharmacy,
built as a single-file Streamlit application backed by PostgreSQL.

Every database object shown in the UI (tables, indexes, functions, procedures,
triggers, roles and grants) is discovered **live** from `pg_catalog`, so the
dashboard always reflects the real state of the database rather than hardcoded
values.

## Tech stack

| Layer | Technology |
|---|---|
| Front end | Streamlit |
| Language | Python 3 |
| Database | PostgreSQL 18 (`pharmacy_tracker_db`, localhost:5432) |
| Driver | `psycopg2` |
| Data frames | pandas |

## Features

- **Role-based login** (Admin / Pharmacist / Cashier) mirroring the real
  database grants.
- **Before vs After index benchmark** — runs each query twice inside a
  transaction, forcing a sequential scan and then an index scan via
  `SET LOCAL enable_indexscan / enable_bitmapscan`, and reports the real
  speed-up ratio plus the `EXPLAIN (ANALYZE, BUFFERS)` plan for both runs.
- **Live schema explorer** — tables, columns, constraints, indexes, triggers,
  functions, procedures and view definitions.
- **Security inspector** — role memberships, table/column grants, RLS status
  and function privileges.
- **DML playground** — insert, update and delete with role-based permission
  checks, plus sale / return / write-off transaction calls.

## Requirements

- Python 3.10+
- PostgreSQL 18 with a `pharmacy_tracker_db` database
- `psycopg2` and `streamlit` installed

## Setup

```bash
pip install -r requirements.txt
```

Set the database password as an environment variable (the app **never**
stores it in source code):

```powershell
# PowerShell
$env:PGPASSWORD = "your_pgadmin_password"
```

```bash
# bash / cmd
export PGPASSWORD=your_pgadmin_password
```

If `PGPASSWORD` is not set the app shows a friendly error and stops.

## Run

```bash
streamlit run app.py
```

Then open <http://localhost:8501>.

## Demo logins

The dashboard ships with throwaway sample logins so it can be demonstrated
without seeding the `users` table first. **These are not real accounts.**

| Username | Password | Role |
|---|---|---|
| `admin` | `admin123` | Admin |
| `pharmacist` | `pharm123` | Pharmacist |
| `cashier` | `cash123` | Cashier |

Logins are checked against the `pharmacy_users` table first, then the real
`users` table, and finally these demo credentials.

## Database

This repository contains the application code only — **it does not create the
database.** You need a running PostgreSQL 18 instance with
`pharmacy_tracker_db` already created and populated.

`Pharmacy_DB_Consolidated_Coding_Sheet.sql` is included as a **reference**
document covering indexing, PL/pgSQL programming, security and transaction
control. Its DDL is reconstructed from the project reports, so verify it
against your own schema before running anything against a real database.

## Project layout

```
app.py                                  Streamlit dashboard (single file)
requirements.txt                        Python dependencies
Pharmacy_DB_Consolidated_Coding_Sheet.sql   SQL reference sheet
```
