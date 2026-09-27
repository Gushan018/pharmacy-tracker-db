# Pharmacy Tracker DB

An interactive inventory and transaction management dashboard for modern pharmacy operations, built as a single-file **Streamlit** application backed by **PostgreSQL 18**.

Unlike static dashboards, every database object displayed in the user interface—including tables, indexes, functions, procedures, triggers, roles, and grants—is dynamically discovered live from `pg_catalog`. This ensures the application always reflects the real-time state and schema of your PostgreSQL database.

---

## 🌟 Key Features

* **Role-Based Access Control (RBAC):** Supports Admin, Pharmacist, and Cashier logins that mirror actual database grants and privileges.
* **Before vs. After Index Benchmarking:**
* Runs performance queries twice within a transaction block.
* Dynamically toggles sequential and index scans using `SET LOCAL enable_indexscan / enable_bitmapscan`.
* Generates execution speed-up ratios along with full `EXPLAIN (ANALYZE, BUFFERS)` execution plans.


* **Live Schema Explorer:** Full inspection of tables, columns, constraints, B-tree indexes, triggers, PL/pgSQL functions, procedures, and view definitions.
* **Security Inspector:** Real-time visibility into database role memberships, table/column grants, Row-Level Security (RLS) status, and function execution privileges.
* **DML & Transaction Playground:** Perform safe Insert, Update, and Delete operations with role-based checks, plus transactional calls for sales, returns, and inventory write-offs.

---

## 🛠️ Tech Stack

| Layer | Technology |
| --- | --- |
| **Front End** | [Streamlit](https://streamlit.io/?utm_source=gemini) |
| **Language** | Python 3.10+ |
| **Database** | PostgreSQL 18 (`pharmacy_tracker_db` @ `localhost:5432`) |
| **Database Driver** | `psycopg2` |
| **Data Processing** | `pandas` |

---

## 📂 Project Layout

```text
.
├── app.py                                  # Main Streamlit dashboard application (Single-file)
├── requirements.txt                        # Python package dependencies
└── Pharmacy_DB_Consolidated_Coding_Sheet.sql   # SQL reference sheet (DDL, Functions, Triggers, RLS)

```

---

## ⚙️ Prerequisites & Installation

### Requirements

* **Python:** `3.10` or higher
* **Database:** PostgreSQL 18 with a running `pharmacy_tracker_db` database instance

### 1. Clone & Install Dependencies

Clone this repository and install the required Python packages:

```bash
git clone https://github.com/YOUR_USERNAME/pharmacy-tracker-db.git
cd pharmacy-tracker-db
pip install -r requirements.txt

```

### 2. Configure Database Password

For security, the application reads your PostgreSQL password from an environment variable rather than hardcoding credentials into source code.

* **PowerShell (Windows):**
```powershell
$env:PGPASSWORD = "your_pgadmin_password"

```


* **Bash / Linux / macOS:**
```bash
export PGPASSWORD="your_pgadmin_password"

```


* **Command Prompt (CMD):**
```cmd
set PGPASSWORD=your_pgadmin_password

```



> ⚠️ **Note:** If `PGPASSWORD` is not set, the application will display an error message and exit safely.

---

## 🚀 Running the Application

Execute the Streamlit application using:

```bash
streamlit run app.py

```

Once started, open your browser and navigate to `http://localhost:8501`.

---

## 🔑 Demo Credentials

The dashboard includes built-in throwaway sample accounts for testing and demonstration purposes without requiring pre-seeded user rows:

| Username | Password | Assigned Role |
| --- | --- | --- |
| `admin` | `admin123` | **Admin** |
| `pharmacist` | `pharm123` | **Pharmacist** |
| `cashier` | `cash123` | **Cashier** |

> **Authentication Hierarchy:** The application checks user credentials against the `pharmacy_users` table first, falls back to the `users` table, and finally verifies against the demo credentials above.

---

## 🗄️ Database Setup & Reference

* This repository contains the application code and reference scripts. It does not automatically create or seed the PostgreSQL database.
* Ensure you have a running PostgreSQL 18 instance with `pharmacy_tracker_db` created prior to launching the dashboard.
* **`Pharmacy_DB_Consolidated_Coding_Sheet.sql`** is provided as a reference document covering indexing strategies, PL/pgSQL procedures/functions, database security, and transaction control. Verify its DDL against your specific database setup before executing scripts in a production environment.
