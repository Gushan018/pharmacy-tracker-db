"""
=============================================================================
 PHARMACY TRACKER DB  -  INTERACTIVE VIVA PRESENTATION DASHBOARD
=============================================================================
 Tech stack : Streamlit + psycopg2   (single-file Python web application)
 Target DB  : pharmacy_tracker_db    (PostgreSQL 18, localhost:5432)

 SETUP & RUN
 -----------
 1) Install the required Python packages (run once):
        pip install streamlit psycopg2-binary

    2) Provide the database password via the PGPASSWORD environment variable
       (keeps the secret out of source code - required for this repo):
            $env:PGPASSWORD = "your_pgadmin_password"     # PowerShell
            export PGPASSWORD=your_pgadmin_password       # bash / cmd

       The app shows a friendly error and stops if PGPASSWORD is not set.

 3) Launch the dashboard:
        streamlit run app.py

 NOTE
 ----
 Every database object (15 tables, 4 indexes, 10 functions, 4 procedures,
 4 triggers, RBAC roles & grants) is discovered LIVE from pg_catalog, so the
 dashboard always reflects the actual state of pharmacy_tracker_db.
=============================================================================
"""

import os
import re

import pandas as pd
import psycopg2
import streamlit as st
from psycopg2 import sql
from psycopg2.errors import DatabaseError

# ---------------------------------------------------------------------------
# DATABASE CONFIGURATION
# ---------------------------------------------------------------------------
DB_CONFIG = {
    "dbname": "pharmacy_tracker_db",
    "user": "postgres",
    "password": os.environ.get("PGPASSWORD", ""),
    "host": "localhost",
    "port": 5432,
}

# UI-level RBAC matrix (mirrors the real role grants in the database).
ROLE_PERMISSIONS = {
    "Admin": {
        "insert": True, "update": True, "delete": True,
        "sale": True, "return": True, "writeoff": True,
        "procedures": True, "functions": True, "explain": True,
    },
    "Pharmacist": {
        "insert": True, "update": True, "delete": False,
        "sale": True, "return": True, "writeoff": False,
        "procedures": False, "functions": True, "explain": True,
    },
    "Cashier": {
        "insert": False, "update": False, "delete": False,
        "sale": True, "return": False, "writeoff": False,
        "procedures": False, "functions": False, "explain": False,
    },
}

# Preset DEMO credentials for quick testing during the viva.
# These are throwaway sample logins, not real accounts - they exist so the
# dashboard can be shown without seeding the users table.
DEMO_USERS = {
    "admin": ("admin123", "Admin"),
    "pharmacist": ("pharm123", "Pharmacist"),
    "cashier": ("cash123", "Cashier"),
}

# Maps user_role values in the real `users` table to the app-level roles.
DB_ROLE_MAP = {
    "MANAGER": "Admin",
    "PHARMACIST": "Pharmacist",
    "CASHIER": "Cashier",
}

st.set_page_config(
    page_title="Pharmacy Tracker Viva Dashboard",
    page_icon=":hospital:",
    layout="wide",
)


# ---------------------------------------------------------------------------
# DATABASE HELPERS
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner="Connecting to pharmacy_tracker_db ...")
def get_conn():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.set_session(autocommit=False)
    return conn


def db_error_msg(err):
    parts = []
    if getattr(err.diag, "message_primary", None):
        parts.append(err.diag.message_primary)
    if getattr(err.diag, "message_detail", None):
        parts.append(err.diag.message_detail)
    if getattr(err.diag, "message_hint", None):
        parts.append(err.diag.message_hint)
    return " | ".join(parts) if parts else str(err)


def run_query_df(sql_text, params=None):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql_text, params or ())
            columns = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchall()
        conn.commit()
        return pd.DataFrame(rows, columns=columns)
    except DatabaseError as e:
        conn.rollback()
        raise e


def call_scalar(sql_text, params):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql_text, params)
            row = cur.fetchone()
        conn.commit()
        notices = list(conn.notices)
        conn.notices.clear()
        return True, (row[0] if row else None), notices
    except DatabaseError as e:
        conn.rollback()
        return False, None, [db_error_msg(e)]


def execute_dml(sql_text, params):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql_text, params)
        conn.commit()
        notices = list(conn.notices)
        conn.notices.clear()
        return True, notices
    except DatabaseError as e:
        conn.rollback()
        return False, [db_error_msg(e)]


def show_db_error(err):
    st.error(db_error_msg(err))


# ---------------------------------------------------------------------------
# SHARED UI HELPERS
# ---------------------------------------------------------------------------
def list_tables():
    df = run_query_df(
        """
        SELECT t.table_name,
               GREATEST(c.reltuples::bigint, 0) AS row_estimate
        FROM information_schema.tables t
        JOIN pg_class c ON c.relname = t.table_name
        JOIN pg_namespace n ON n.oid = c.relnamespace
                           AND n.nspname = t.table_schema
        WHERE t.table_schema = 'public'
          AND t.table_type = 'BASE TABLE'
        ORDER BY t.table_name
        """
    )
    return df["table_name"].tolist()


def get_pk_columns(table):
    df = run_query_df(
        """
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        WHERE tc.table_schema = 'public'
          AND tc.table_name = %s
          AND tc.constraint_type = 'PRIMARY KEY'
        ORDER BY kcu.ordinal_position
        """,
        (table,),
    )
    return df["column_name"].tolist()


def get_editable_columns(table):
    df = run_query_df(
        """
        SELECT column_name, data_type, is_nullable, column_default, is_identity
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
        """,
        (table,),
    )
    editable = []
    for _, row in df.iterrows():
        if row["is_identity"] == "YES":
            continue
        if row["column_default"] is not None:
            continue
        if "timestamp" in (row["data_type"] or ""):
            continue
        editable.append(row)
    return editable


def load_batches(medicine_filter="", limit=400):
    df = run_query_df(
        """
        SELECT b.batch_id, m.medicine_name, b.batch_number,
               b.expiry_date, b.stock_quantity,
               b.cost_price, b.selling_price
        FROM batches b
        JOIN medicines m ON m.medicine_id = b.medicine_id
        WHERE (%s = '' OR m.medicine_name ILIKE '%%' || %s || '%%')
        ORDER BY b.batch_id
        LIMIT %s
        """,
        (medicine_filter, medicine_filter, limit),
    )
    df["label"] = df.apply(
        lambda r: (
            f"Batch {r.batch_id} | {r.medicine_name} ({r.batch_number}) | "
            f"Stock: {r.stock_quantity} | Rs {r.selling_price} | Exp {r.expiry_date}"
        ),
        axis=1,
    )
    return df


def batch_selector(key, default_filter=""):
    col1, col2 = st.columns([3, 1])
    with col1:
        medicine_filter = st.text_input(
            "Filter batches by medicine name (optional)", value=default_filter, key=key + "_filter"
        )
    df = load_batches(medicine_filter)
    if df.empty:
        st.warning("No batches match the current filter.")
        return None, None
    with col2:
        manual_id = st.number_input("or type Batch ID", min_value=1, step=1, key=key + "_manual")
    options = df["batch_id"].tolist()
    label_map = dict(zip(df["batch_id"], df["label"]))
    selected = st.selectbox(
        "Select batch", options,
        format_func=lambda b: label_map.get(b, str(b)),
        key=key + "_sel",
    )
    if manual_id and manual_id not in options:
        row = run_query_df(
            """
            SELECT b.batch_id, m.medicine_name, b.batch_number,
                   b.expiry_date, b.stock_quantity,
                   b.cost_price, b.selling_price
            FROM batches b JOIN medicines m ON m.medicine_id = b.medicine_id
            WHERE b.batch_id = %s
            """,
            (manual_id,),
        )
        if not row.empty:
            selected = int(row.iloc[0]["batch_id"])
            return selected, row.iloc[0]
    if selected in options:
        return selected, df.loc[df["batch_id"] == selected].iloc[0]
    return None, None


def role_banner(role):
    if role == "Admin":
        st.sidebar.success("Full access: view / insert / update / delete + all functions & procedures.")
    elif role == "Pharmacist":
        st.sidebar.info("Restricted: view + restock, sales, returns, read functions. Delete / write-off / admin procedures disabled.")
    else:
        st.sidebar.warning("Cashier: view medicines & batches, run point-of-sale sales only.")


def perm(role, key):
    return ROLE_PERMISSIONS[role][key]


# ---------------------------------------------------------------------------
# AUTHENTICATION HELPERS
# ---------------------------------------------------------------------------
def init_auth_state():
    if "logged_in" not in st.session_state:
        st.session_state["logged_in"] = False
        st.session_state["username"] = None
        st.session_state["role"] = None


def authenticate(username, password):
    """Cascade login: pharmacy_users -> real users table -> preset demo roles."""
    username = (username or "").strip()
    password = password or ""
    if not username or not password:
        return False, None, None

    conn = get_conn()

    # 1. pharmacy_users table (optional; skipped if it does not exist)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT username, role FROM pharmacy_users "
                "WHERE username = %s AND password_hash = %s",
                (username, password),
            )
            row = cur.fetchone()
        conn.commit()
        if row:
            return True, row[0], row[1]
    except DatabaseError:
        conn.rollback()

    # 2. real 'users' table (email or full_name + stored password_hash)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT full_name, user_role FROM users "
                "WHERE (email = %s OR full_name = %s) AND password_hash = %s",
                (username, username, password),
            )
            row = cur.fetchone()
        conn.commit()
        if row:
            role = DB_ROLE_MAP.get(row[1])
            if role:
                return True, row[0], role
    except DatabaseError:
        conn.rollback()

    # 3. preset demo roles for quick testing
    if username in DEMO_USERS:
        demo_pass, demo_role = DEMO_USERS[username]
        if password == demo_pass:
            return True, username, demo_role

    return False, None, None


def render_login():
    st.title(":hospital: Pharmacy Tracker DB")
    st.markdown("### Please sign in to access the dashboard")

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        with st.form("login_form", clear_on_submit=False):
            username = st.text_input("Username", key="login_username")
            password = st.text_input("Password", type="password", key="login_password")
            submitted = st.form_submit_button("Login", width="stretch")

        if submitted:
            ok, name, role = authenticate(username, password)
            if ok:
                st.session_state["logged_in"] = True
                st.session_state["username"] = name
                st.session_state["role"] = role
                st.rerun()
            else:
                st.error("Invalid username or password.")

        with st.expander("Demo credentials (viva only)"):
            st.code(
                "admin      / admin123      -> Admin\n"
                "pharmacist / pharm123      -> Pharmacist\n"
                "cashier    / cash123       -> Cashier",
                language="text",
            )
            st.caption(
                "Database users also work, e.g. `manager@pharmacy.com` / `hash123` "
                "(stored password_hash in the `users` table)."
            )


def render_logout_sidebar(username, role):
    st.sidebar.markdown(
        f"👤 **Logged in as:** {username}  \n"
        f"**Role:** **{role}**"
    )
    if st.sidebar.button("🔒 Logout", width="stretch", type="secondary"):
        st.session_state["logged_in"] = False
        st.session_state["username"] = None
        st.session_state["role"] = None
        st.rerun()
    role_banner(role)


# ===========================================================================
# TAB 1 - TABLES INSPECTOR & INVENTORY MANAGEMENT (TRIGGERS / CONSTRAINTS DEMO)
# ===========================================================================
def render_tab_tables(role):
    st.header("1 | Tables Inspector & Inventory Management")
    st.caption("Live trigger / constraint demo: update or delete a batch and watch the database enforce "
               "its rules (CHECK constraints + AFTER/BEFORE triggers + audit logging).")

    tables = list_tables()
    if not tables:
        st.error("No tables found in schema 'public'.")
        return

    left, right = st.columns([2, 1])
    with left:
        table = st.selectbox("Inspect table", tables)
    with right:
        st.metric("Tables in schema", len(tables))

    st.subheader("Data preview")
    try:
        df = run_query_df(
            sql.SQL("SELECT * FROM {} LIMIT 200").format(sql.Identifier("public", table))
        )
    except DatabaseError as e:
        st.error(db_error_msg(e))
        return
    st.dataframe(df, width="stretch")
    st.caption(f"{len(df)} rows shown (max 200) across {len(df.columns)} columns.")

    st.divider()

    # ---- Admin: generic insert + delete -----------------------------------
    if perm(role, "insert"):
        st.subheader("Insert a new row (Admin)")
        cols = get_editable_columns(table)
        if not cols:
            st.info("This table has no user-editable columns (identity / auto-managed).")
        else:
            with st.form("generic_insert"):
                values = {}
                for col in cols:
                    name = col["column_name"]
                    dtype = col["data_type"]
                    required = col["is_nullable"] == "NO"
                    key = f"ins_{table}_{name}"
                    if "integer" in dtype or dtype in ("smallint", "bigint"):
                        values[name] = st.number_input(
                            f"{name}{' *' if required else ''}", step=1,
                            value=1 if required else 0, key=key)
                    elif "numeric" in dtype or "decimal" in dtype or "real" in dtype or "double" in dtype:
                        values[name] = st.number_input(
                            f"{name}{' *' if required else ''}",
                            value=1.0 if required else 0.0, key=key)
                    elif "boolean" in dtype:
                        values[name] = st.selectbox(f"{name}", [True, False], key=key)
                    elif "date" in dtype:
                        values[name] = st.date_input(f"{name}{' *' if required else ''}", key=key)
                    else:
                        values[name] = st.text_input(
                            f"{name}{' *' if required else ''}", key=key) or None
                submitted = st.form_submit_button("Insert row")
            if submitted:
                cols_names = list(values.keys())
                stmt = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                    sql.Identifier("public", table),
                    sql.SQL(", ").join(map(sql.Identifier, cols_names)),
                    sql.SQL(", ").join(sql.Placeholder() * len(cols_names)),
                )
                ok, notices = execute_dml(stmt, list(values.values()))
                if ok:
                    st.success(f"Inserted a new row into '{table}'.")
                    for n in notices:
                        st.caption(f"NOTICE: {n}")
                else:
                    st.error(notices[0])

    if perm(role, "delete"):
        pks = get_pk_columns(table)
        if pks:
            st.subheader(f"Delete a row by primary key ({', '.join(pks)})")
            st.caption("Tip: deleting a batch with active stock triggers `trg_prevent_deleting_active_batch`.")
            with st.form("generic_delete"):
                pk_val = st.text_input(f"Value of {pks[0]}", key=f"del_{table}")
                submitted = st.form_submit_button("Delete row")
            if submitted and pk_val not in ("", None):
                stmt = sql.SQL("DELETE FROM {} WHERE {} = %s").format(
                    sql.Identifier("public", table), sql.Identifier(pks[0]))
                ok, notices = execute_dml(stmt, (pk_val,))
                if ok:
                    st.success(f"Row deleted from '{table}'.")
                else:
                    st.error(notices[0])

    st.divider()

    # ---- Batch stock update (Trigger Catching demo) ------------------------
    st.subheader("Update batch stock quantity (fires `trg_auto_audit_log`)")
    st.caption("Enter a NEGATIVE value to deliberately trigger the database "
               "`batches_stock_quantity_check` constraint and watch the error be "
               "handled gracefully below.")
    batch_id, batch_row = batch_selector("stock_update")
    if batch_row is not None:
        with st.form("stock_update"):
            new_stock = st.number_input(
                "New stock quantity (try a negative number for the trigger demo)",
                step=1, value=int(batch_row["stock_quantity"]), key="new_stock")
            submitted = st.form_submit_button(
                "UPDATE batches SET stock_quantity = ?")
        if submitted:
            conn = get_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE batches SET stock_quantity = %s WHERE batch_id = %s",
                        (new_stock, batch_id),
                    )
                conn.commit()
                notices = list(conn.notices)
                conn.notices.clear()
                st.success(f"Stock for batch {batch_id} updated to {new_stock}.")
                for n in notices:
                    st.caption(f"NOTICE: {n}")
                st.info("Audit trail written by `trg_auto_audit_log`:")
                st.dataframe(
                    run_query_df(
                        "SELECT * FROM inventory_logs WHERE batch_id = %s ORDER BY log_id DESC LIMIT 5",
                        (batch_id,),
                    ),
                    width="stretch",
                )
            except DatabaseError as e:
                conn.rollback()
                st.error("Database rejected the update:")
                show_db_error(e)


# ===========================================================================
# TAB 2 - POINT OF SALE / TRANSACTIONS (FUNCTIONS, PROCEDURES & TRIGGERS)
# ===========================================================================
def render_tab_pos(role):
    st.header("2 | Point of Sale & Transactions")
    st.caption("Drives the stored function `process_medicine_sale(...)`, the return function, "
               "the direct trigger path (order_items insert), admin procedures, and read functions.")

    tab_sale, tab_return, tab_trigger, tab_proc, tab_func = st.tabs(
        ["Process Sale", "Process Return", "Trigger Path (order_items)", "Admin Procedures", "Read Functions"]
    )

    # --- SALE ----------------------------------------------------------------
    with tab_sale:
        st.subheader("process_medicine_sale(batch_id, qty, customer_id, pharmacist_id)")
        batch_id, batch_row = batch_selector("sale_batch")
        if batch_row is not None:
            st.metric("Selling price", f"Rs {batch_row['selling_price']}")
            st.metric("Available stock", f"{batch_row['stock_quantity']} units")
            st.metric("Expiry date", f"{batch_row['expiry_date']}")

        cust = run_query_df(
            """
            SELECT c.customer_id, u.full_name
            FROM customers c JOIN users u ON u.user_id = c.customer_id
            ORDER BY c.customer_id
            """
        )
        cust_opt = [None] + cust["customer_id"].tolist()
        cust_label = {c: n for c, n in zip(cust["customer_id"], cust["full_name"])}
        customer_id = st.selectbox(
            "Customer (optional)", cust_opt,
            format_func=lambda c: f"{c} - {cust_label.get(c, '')}" if c else "— None —",
            key="sale_customer",
        )

        pharm = run_query_df(
            """
            SELECT p.pharmacist_id, u.full_name
            FROM pharmacists p JOIN users u ON u.user_id = p.pharmacist_id
            ORDER BY p.pharmacist_id
            """
        )
        pharm_opt = [None] + pharm["pharmacist_id"].tolist()
        pharm_label = {p: n for p, n in zip(pharm["pharmacist_id"], pharm["full_name"])}
        pharmacist_id = st.selectbox(
            "Pharmacist (optional)", pharm_opt,
            format_func=lambda p: f"{p} - {pharm_label.get(p, '')}" if p else "— None —",
            key="sale_pharmacist",
        )

        qty = st.number_input("Quantity to sell", min_value=1, step=1, value=1, key="sale_qty")
        if st.button("Process Sale", disabled=batch_row is None):
            if batch_row is None:
                st.warning("Select a batch first.")
            else:
                ok, order_id, notices = call_scalar(
                    "SELECT process_medicine_sale(%s, %s, %s, %s)",
                    (batch_id, qty, customer_id, pharmacist_id),
                )
                if ok:
                    st.success(f"Sale processed! New Order ID: **{order_id}**")
                    for n in notices:
                        st.caption(f"NOTICE: {n}")
                    st.info("Updated batch row:")
                    st.dataframe(
                        run_query_df("SELECT * FROM batches WHERE batch_id = %s", (batch_id,)),
                        width="stretch",
                    )
                else:
                    st.error("Sale transaction failed (rolled back):")
                    st.error(notices[0])

    # --- RETURN ---------------------------------------------------------------
    with tab_return:
        st.subheader("process_medicine_return(order_id, batch_id, qty, reason, is_damaged)")
        orders = run_query_df(
            "SELECT order_id, order_date, total_amount FROM sales_orders ORDER BY order_id DESC LIMIT 300"
        )
        order_opt = orders["order_id"].tolist()
        order_id = st.selectbox(
            "Order ID", order_opt,
            format_func=lambda o: f"{o} | {orders.loc[orders['order_id'] == o].iloc[0]['order_date']} | Rs {orders.loc[orders['order_id'] == o].iloc[0]['total_amount']}",
            key="ret_order",
        )
        rbatch_id, rbatch_row = batch_selector("ret_batch")
        rqty = st.number_input("Return quantity", min_value=1, step=1, value=1, key="ret_qty")
        reason = st.text_input("Return reason", value="Customer refund request", key="ret_reason")
        is_damaged = st.checkbox("Items damaged (do NOT restock)", value=False, key="ret_damaged")
        if st.button("Process Return", disabled=rbatch_row is None):
            ok, _, notices = call_scalar(
                "SELECT process_medicine_return(%s, %s, %s, %s, %s)",
                (order_id, rbatch_id, rqty, reason, is_damaged),
            )
            if ok:
                st.success("Return processed successfully.")
                for n in notices:
                    st.caption(f"NOTICE: {n}")
            else:
                st.error("Return transaction failed:")
                st.error(notices[0])

    # --- DIRECT TRIGGER PATH ---------------------------------------------------
    with tab_trigger:
        st.subheader("Direct INSERT into order_items (fires triggers)")
        st.caption("`trg_check_expiry_before_sale` (BEFORE INSERT) validates expiry, then "
                   "`trg_deduct_stock_on_sale` (AFTER INSERT) deducts stock automatically.")
        o2 = run_query_df(
            "SELECT order_id, order_date FROM sales_orders ORDER BY order_id DESC LIMIT 300"
        )
        oid = st.selectbox("Existing Order ID", o2["order_id"].tolist(), key="trig_order")
        tid, trow = batch_selector("trig_batch")
        tqty = st.number_input("Quantity", min_value=1, step=1, value=1, key="trig_qty")
        if st.button("Insert order_items row (run triggers)", disabled=trow is None):
            stmt = "INSERT INTO order_items (order_id, batch_id, quantity, unit_price) VALUES (%s, %s, %s, %s)"
            ok, notices = execute_dml(stmt, (oid, tid, tqty, float(trow["selling_price"])))
            if ok:
                st.success("Row inserted. Stock was deducted by `trg_deduct_stock_on_sale`.")
                for n in notices:
                    st.caption(f"NOTICE: {n}")
                st.dataframe(
                    run_query_df("SELECT * FROM batches WHERE batch_id = %s", (tid,)),
                    width="stretch",
                )
            else:
                st.error("Trigger blocked the insert:")
                st.error(notices[0])

    # --- ADMIN PROCEDURES --------------------------------------------------------
    with tab_proc:
        if not perm(role, "procedures"):
            st.warning("Admin role only - procedures modify data (shipments, pricing, write-offs).")
        else:
            st.subheader("Stored Procedures (CALL)")
            p1, p2 = st.columns(2)
            with p1:
                st.markdown("**receive_medicine_shipment(...)**")
                meds = run_query_df(
                    "SELECT medicine_id, medicine_name FROM medicines ORDER BY medicine_id"
                )
                med = st.selectbox(
                    "Medicine", meds["medicine_id"].tolist(),
                    format_func=lambda m: f"{m} - {meds.loc[meds['medicine_id'] == m].iloc[0]['medicine_name']}",
                    key="ship_med",
                )
                batch_no = st.text_input("Batch number", value="LOT-NEW-001", key="ship_bno")
                man_d = st.date_input("Manufacture date", key="ship_man")
                exp_d = st.date_input("Expiry date", key="ship_exp")
                sh_qty = st.number_input("Quantity", min_value=1, step=1, value=100, key="ship_qty")
                cost = st.number_input("Cost price (Rs)", min_value=0.01, value=40.0, key="ship_cost")
                if st.button("CALL receive_medicine_shipment"):
                    ok, notices = execute_dml(
                        "CALL receive_medicine_shipment(%s, %s, %s, %s, %s, %s)",
                        (med, batch_no, man_d, exp_d, sh_qty, cost),
                    )
                    if ok:
                        st.success("Shipment received; new batch + inventory log created. Selling price auto-set to cost + 30%.")
                        for n in notices:
                            st.caption(f"NOTICE: {n}")
                    else:
                        st.error(notices[0])
            with p2:
                st.markdown("**proc_clearance_discount_near_expiry(days, %)  &  proc_mark_and_log_expired_waste()**")
                days = st.number_input("Days threshold", min_value=1, step=1, value=60, key="clr_days")
                disc = st.number_input("Discount percentage", min_value=0.0, max_value=90.0, value=20.0, key="clr_disc")
                if st.button("CALL proc_clearance_discount_near_expiry"):
                    ok, notices = execute_dml(
                        "CALL proc_clearance_discount_near_expiry(%s, %s)", (days, disc))
                    if ok:
                        st.success("Clearance discount applied to near-expiry batches (never below cost).")
                        for n in notices:
                            st.caption(f"NOTICE: {n}")
                    else:
                        st.error(notices[0])
                if st.button("CALL proc_mark_and_log_expired_waste"):
                    ok, notices = execute_dml("CALL proc_mark_and_log_expired_waste()")
                    if ok:
                        st.success("Expired inventory swept: logged as WASTE and stock zeroed.")
                        for n in notices:
                            st.caption(f"NOTICE: {n}")
                    else:
                        st.error(notices[0])

            st.subheader("Return procedure & write-off")
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**proc_process_customer_return(order, batch, qty, reason)**")
                orr = run_query_df(
                    "SELECT order_id, order_date FROM sales_orders ORDER BY order_id DESC LIMIT 300")
                por = st.selectbox("Order ID", orr["order_id"].tolist(), key="pr_ord")
                pbr, _ = batch_selector("pr_batch")
                pqty = st.number_input("Return qty", min_value=1, step=1, value=1, key="pr_qty")
                preas = st.text_input("Reason", value="Customer return", key="pr_reason")
                if st.button("CALL proc_process_customer_return"):
                    ok, notices = execute_dml(
                        "CALL proc_process_customer_return(%s, %s, %s, %s)",
                        (por, pbr, pqty, preas))
                    if ok:
                        st.success("Return procedure completed - stock restored + return_logs entry.")
                        for n in notices:
                            st.caption(f"NOTICE: {n}")
                    else:
                        st.error(notices[0])
            with c2:
                st.markdown("**process_batch_writeoff(batch_id, reason)**  (Admin)")
                wb, _ = batch_selector("wo_batch")
                wreason = st.text_input("Write-off reason", value="Expired batch disposal", key="wo_reason")
                if st.button("Run process_batch_writeoff"):
                    ok, _, notices = call_scalar(
                        "SELECT process_batch_writeoff(%s, %s)", (wb, wreason))
                    if ok:
                        st.success("Batch written off (stock zeroed, row locked via FOR UPDATE).")
                        for n in notices:
                            st.caption(f"NOTICE: {n}")
                    else:
                        st.error(notices[0])

    # --- READ FUNCTIONS ---------------------------------------------------------
    with tab_func:
        if not perm(role, "functions"):
            st.warning("Available to Admin and Pharmacist roles.")
        else:
            st.subheader("PL/pgSQL Functions (read-only)")
            f1, f2, f3 = st.columns(3)

            with f1:
                st.markdown("**func_get_medicine_total_stock(id)**")
                meds2 = run_query_df(
                    "SELECT medicine_id, medicine_name FROM medicines ORDER BY medicine_id")
                mid = st.selectbox(
                    "Medicine", meds2["medicine_id"].tolist(),
                    format_func=lambda m: f"{m} - {meds2.loc[meds2['medicine_id'] == m].iloc[0]['medicine_name']}",
                    key="fn_med",
                )
                if st.button("Run total stock"):
                    ok, val, _ = call_scalar(
                        "SELECT func_get_medicine_total_stock(%s)", (mid,))
                    if ok:
                        st.metric("Total stock (unexpired batches)", int(val))
                    else:
                        st.error("Function failed.")

            with f2:
                st.markdown("**func_count_low_stock_medicines(threshold)**")
                thr = st.number_input("Stock threshold", min_value=1, step=1, value=50, key="fn_thr")
                if st.button("Run low-stock count"):
                    ok, val, _ = call_scalar(
                        "SELECT func_count_low_stock_medicines(%s)", (thr,))
                    if ok:
                        st.metric("Low-stock medicines", int(val))
                    else:
                        st.error("Function failed.")

            with f3:
                st.markdown("**func_calculate_pharmacist_revenue(id, start, end)**")
                pharm2 = run_query_df(
                    "SELECT p.pharmacist_id, u.full_name FROM pharmacists p "
                    "JOIN users u ON u.user_id = p.pharmacist_id ORDER BY p.pharmacist_id")
                if pharm2.empty:
                    st.info("No pharmacists registered.")
                else:
                    pid = st.selectbox(
                        "Pharmacist", pharm2["pharmacist_id"].tolist(),
                        format_func=lambda p: f"{p} - {pharm2.loc[pharm2['pharmacist_id'] == p].iloc[0]['full_name']}",
                        key="fn_ph",
                    )
                    d1 = st.date_input("Start date", key="fn_d1")
                    d2 = st.date_input("End date", key="fn_d2")
                    if st.button("Run revenue"):
                        ok, val, _ = call_scalar(
                            "SELECT func_calculate_pharmacist_revenue(%s, %s, %s)",
                            (pid, d1, d2))
                        if ok:
                            st.metric("Revenue (Rs)", float(val or 0.0))
                        else:
                            st.error("Function failed.")


# ===========================================================================
# TAB 3 - QUERY EXECUTION & EXPLAIN ANALYZE BENCHMARK
# ===========================================================================
REPORTS = {
    "Medicine point lookup (idx_medicines_name)": {
        "sql": "SELECT * FROM medicines WHERE medicine_name = %s",
        "params": ["medicine_name"],
    },
    "Batch expiry rotations (idx_batches_med_expiry)": {
        "sql": "SELECT batch_id, medicine_id, batch_number, expiry_date, stock_quantity "
               "FROM batches WHERE medicine_id = %s ORDER BY expiry_date ASC",
        "params": ["medicine_id"],
    },
    "High-frequency join batches<->order_items (idx_order_items_batch)": {
        "sql": "SELECT b.batch_number, oi.quantity, oi.unit_price "
               "FROM order_items oi JOIN batches b ON b.batch_id = oi.batch_id "
               "WHERE oi.batch_id = %s",
        "params": ["batch_id"],
    },
    "Audit trail lookup (idx_logs_batch_type)": {
        "sql": "SELECT * FROM inventory_logs WHERE batch_id = %s AND log_type = 'RESTOCK'",
        "params": ["batch_id"],
    },
    "Low stock report (business query)": {
        "sql": "SELECT m.medicine_name, b.batch_number, b.expiry_date, b.stock_quantity "
               "FROM medicines m JOIN batches b ON b.medicine_id = m.medicine_id "
               "WHERE b.stock_quantity < %s ORDER BY b.stock_quantity ASC",
        "params": ["threshold"],
    },
    "Expiring soon report (business query)": {
        "sql": "SELECT m.medicine_name, b.batch_number, b.expiry_date, b.stock_quantity "
               "FROM medicines m JOIN batches b ON b.medicine_id = m.medicine_id "
               "WHERE b.expiry_date BETWEEN CURRENT_DATE AND CURRENT_DATE + %s "
               "ORDER BY b.expiry_date ASC",
        "params": ["days"],
    },
}


def _format_query(sql_text, params):
    """Return a fully-formatted SQL string with `%s` placeholders replaced by
    safely-quoted literal values (uses psycopg2's Literal, so quotes inside
    strings are escaped correctly)."""
    if not params:
        return sql_text.strip()
    parts = sql_text.split("%s")
    if len(parts) == 1:
        return sql_text.strip()
    conn = get_conn()
    out = [parts[0]]
    for i, val in enumerate(params):
        out.append(sql.Literal(val).as_string(conn))
        out.append(parts[i + 1])
    return "".join(out).strip()


def _parse_explain(plan_lines):
    """Parse an EXPLAIN (ANALYZE, BUFFERS) output into key numbers and the scan
    type used. Returns (exec_time_ms, plan_time_ms, total_cost, scan_type, index)."""
    text = "\n".join(plan_lines)

    def _num(pattern):
        m = re.search(pattern, text)
        return float(m.group(1)) if m else None

    exec_time = _num(r"Execution Time:\s*([\d.]+)\s*ms")
    plan_time = _num(r"Planning Time:\s*([\d.]+)\s*ms")
    total_cost = _num(r"cost=\d+\.?\d*\.\.(\d+\.?\d*)")

    if re.search(r"Index (?:Only )?Scan", text):
        scan_type = "Index Scan"
    elif re.search(r"Seq Scan", text):
        scan_type = "Seq Scan"
    else:
        first = text.splitlines()[0] if text else ""
        m = re.match(r"^(\w+(?:\s+\w+)*?)\s+on\b", first)
        scan_type = m.group(1) if m else (first.split()[0] if first else "n/a")

    m = re.search(r"Index (?:Only )?Scan using (\w+)", text)
    index_used = m.group(1) if m else None
    return exec_time, plan_time, total_cost, scan_type, index_used


def render_tab_explain(role):
    st.header("3 | Query Execution & Index Performance Benchmark")
    st.caption("Runs the same query twice in one transaction: once with the indexes "
               "disabled (`Seq Scan` forced) and once with them enabled (`Index Scan`), "
               "then compares the two Execution Times side-by-side.")
    if not perm(role, "explain"):
        st.warning("EXPLAIN ANALYZE is available to Admin and Pharmacist roles.")
        return

    choice = st.selectbox("Report query", list(REPORTS.keys()) + ["Custom SQL"])
    query = None
    params = []

    if choice == "Custom SQL":
        query = st.text_area(
            "Write a SELECT query (parameters allowed as %s)",
            "SELECT * FROM batches WHERE stock_quantity < 50 LIMIT 50",
            height=120,
        )
        st.caption("Only SELECT statements are permitted (EXPLAIN ANALYZE on DML would modify data).")
    else:
        spec = REPORTS[choice]
        query = spec["sql"]
        st.code(spec["sql"], language="sql")
        st.caption("Parameter values:")
        for p in spec["params"]:
            if p == "medicine_name":
                names = run_query_df("SELECT DISTINCT medicine_name FROM medicines ORDER BY 1 LIMIT 50")
                if not names.empty:
                    v = st.selectbox("medicine_name", names["medicine_name"].tolist(), key=f"p_{p}")
                    params.append(v)
            elif p == "medicine_id":
                meds = run_query_df("SELECT medicine_id, medicine_name FROM medicines ORDER BY 1")
                v = st.selectbox(
                    "medicine_id", meds["medicine_id"].tolist(),
                    format_func=lambda m: f"{m} - {meds.loc[meds['medicine_id'] == m].iloc[0]['medicine_name']}",
                    key=f"p_{p}")
                params.append(v)
            elif p == "batch_id":
                v = st.number_input("batch_id", min_value=1, step=1, value=7500, key=f"p_{p}")
                params.append(v)
            elif p == "threshold":
                v = st.number_input("stock threshold", min_value=1, step=1, value=50, key=f"p_{p}")
                params.append(v)
            elif p == "days":
                v = st.number_input("days to expiry", min_value=1, step=1, value=90, key=f"p_{p}")
                params.append(v)

    if st.button("Run EXPLAIN ANALYZE", type="primary"):
        if query is None:
            st.warning("Build the query first.")
        elif not re.match(r"^\s*(SELECT|WITH)", query, re.IGNORECASE) or re.search(
            r"\b(INSERT|UPDATE|DELETE|MERGE|TRUNCATE|DROP|ALTER|CREATE|CALL|DO)\b",
            query, re.IGNORECASE,
        ):
            st.error("Only read-only SELECT statements are allowed here.")
        elif query.count("%s") != len(params):
            st.error(f"The query has {query.count('%s')} `%s` placeholder(s) but only "
                     f"{len(params)} parameter value(s) were provided.")
        else:
            formatted = _format_query(query, params)
            explain_sql = f"EXPLAIN (ANALYZE, BUFFERS) {formatted.rstrip(';')}"
            conn = get_conn()
            try:
                with conn.cursor() as cur:
                    # Run 1 - WITHOUT index (sequential scan forced)
                    cur.execute("SET LOCAL enable_indexscan = off")
                    cur.execute("SET LOCAL enable_bitmapscan = off")
                    cur.execute(explain_sql)
                    plan_before = [r[0] for r in cur.fetchall()]

                    # Run 2 - WITH index (index scan enabled)
                    cur.execute("SET LOCAL enable_indexscan = on")
                    cur.execute("SET LOCAL enable_bitmapscan = on")
                    cur.execute(explain_sql)
                    plan_after = [r[0] for r in cur.fetchall()]
                conn.rollback()

                b_time, b_plan_time, b_cost, b_scan, _ = _parse_explain(plan_before)
                a_time, a_plan_time, a_cost, a_scan, a_index = _parse_explain(plan_after)

                speedup = None
                if b_time and a_time and a_time > 0:
                    speedup = b_time / a_time
                used_index = a_scan == "Index Scan" or a_index is not None

                st.subheader("Before vs After - Index Benchmark")
                c1, c2 = st.columns(2)
                with c1:
                    st.markdown("#### 🔴 Before - Seq Scan (index disabled)")
                    st.metric("Execution Time", f"{b_time:.3f} ms" if b_time else "n/a")
                    st.caption(f"Scan type: **{b_scan}**")
                    st.caption(f"Total cost: {b_cost:.2f}" if b_cost else "Total cost: n/a")
                with c2:
                    st.markdown("#### 🟢 After - Index Scan (index enabled)")
                    st.metric("Execution Time", f"{a_time:.3f} ms" if a_time else "n/a")
                    st.caption(f"Scan type: **{a_scan}**"
                               + (f" using **{a_index}**" if a_index else ""))
                    st.caption(f"Total cost: {a_cost:.2f}" if a_cost else "Total cost: n/a")
                    if used_index and speedup is not None and speedup >= 1.05:
                        st.success(f"⚡ {speedup:.1f}x Faster!")
                    elif used_index and speedup is not None and speedup >= 1.0:
                        st.info("≈ same speed (index in use, but times are close)")
                    else:
                        st.warning("No speedup - the query did not use an index "
                                   "(e.g. filtering a non-indexed column).")

                st.caption("Benchmark method: Run 1 sets `enable_indexscan = off` and "
                           "`enable_bitmapscan = off` so PostgreSQL is forced to `Seq Scan`; "
                           "Run 2 re-enables them. Both runs happen in the same transaction "
                           "and `SET LOCAL` is reverted automatically on rollback.")

                e1, e2 = st.columns(2)
                with e1:
                    with st.expander("Run 1 - EXPLAIN (ANALYZE, BUFFERS) - Seq Scan forced"):
                        st.code("\n".join(plan_before), language="text")
                with e2:
                    with st.expander("Run 2 - EXPLAIN (ANALYZE, BUFFERS) - Index Scan enabled"):
                        st.code("\n".join(plan_after), language="text")

                m1, m2, m3 = st.columns(3)
                with m1:
                    st.metric("Planning Time", f"{a_plan_time:.3f} ms" if a_plan_time else "n/a")
                with m2:
                    st.metric("Execution Time (indexed)", f"{a_time:.3f} ms" if a_time else "n/a")
                with m3:
                    df = run_query_df(query, tuple(params))
                    st.metric("Rows returned", len(df))

                st.subheader("Result set")
                st.dataframe(df, width="stretch")
            except DatabaseError as e:
                conn.rollback()
                show_db_error(e)


# ===========================================================================
# TAB 4 - SCHEMA OVERVIEW (LIVE pg_catalog)
# ===========================================================================
def render_tab_schema():
    st.header("4 | Schema Overview (live from pg_catalog)")
    st.caption("Proves the full architecture: tables, indexes, functions, procedures, triggers, FKs and RBAC grants.")

    s1, s2 = st.columns(2)
    with s1:
        st.subheader("Tables")
        st.dataframe(
            run_query_df(
                "SELECT t.table_name, GREATEST(c.reltuples::bigint, 0) AS est_rows "
                "FROM information_schema.tables t "
                "JOIN pg_class c ON c.relname = t.table_name "
                "JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = t.table_schema "
                "WHERE t.table_schema='public' AND t.table_type='BASE TABLE' ORDER BY t.table_name"
            ),
            width="stretch",
        )
    with s2:
        st.subheader("Indexes")
        st.caption("Includes the 4 hand-tuned indexes (idx_*) plus those backing PRIMARY KEY / UNIQUE constraints.")
        st.dataframe(
            run_query_df(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname='public' ORDER BY indexname"
            ),
            width="stretch",
        )

    f1, f2 = st.columns(2)
    with f1:
        st.subheader("Functions")
        st.dataframe(
            run_query_df(
                "SELECT p.proname AS function, "
                "pg_get_function_identity_arguments(p.oid) AS args, "
                "pg_get_function_result(p.oid) AS returns "
                "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname='public' AND p.prokind='f' ORDER BY p.proname"
            ),
            width="stretch",
        )
    with f2:
        st.subheader("Procedures")
        st.dataframe(
            run_query_df(
                "SELECT p.proname AS procedure, "
                "pg_get_function_identity_arguments(p.oid) AS args "
                "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname='public' AND p.prokind='p' ORDER BY p.proname"
            ),
            width="stretch",
        )

    st.subheader("Triggers")
    st.dataframe(
        run_query_df(
            "SELECT c.relname AS table_name, tg.tgname AS trigger_name, "
            "pg_get_triggerdef(tg.oid) AS definition "
            "FROM pg_trigger tg JOIN pg_class c ON c.oid = tg.tgrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname='public' AND NOT tg.tgisinternal "
            "ORDER BY c.relname, tg.tgname"
        ),
        width="stretch",
    )

    st.subheader("Foreign keys")
    st.dataframe(
        run_query_df(
            "SELECT tc.table_name, kcu.column_name, ccu.table_name AS references_table, "
            "ccu.column_name AS references_column "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "  ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema "
            "JOIN information_schema.constraint_column_usage ccu "
            "  ON tc.constraint_name = ccu.constraint_name "
            "WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_schema='public' "
            "ORDER BY tc.table_name, kcu.column_name"
        ),
        width="stretch",
    )

    st.subheader("Role-based access control (grants)")
    g1, g2 = st.columns(2)
    with g1:
        st.markdown("**Table / sequence grants**")
        st.dataframe(
            run_query_df(
                "SELECT c.relname AS object, pg_get_userbyid(a.grantee) AS grantee, "
                "a.privilege_type "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner))) a "
                "WHERE n.nspname='public' AND c.relkind IN ('r','S','p') AND a.grantee <> 0 "
                "ORDER BY c.relname, grantee"
            ),
            width="stretch",
        )
    with g2:
        st.markdown("**Function grants**")
        st.dataframe(
            run_query_df(
                "SELECT p.proname AS function, pg_get_userbyid(a.grantee) AS grantee, "
                "a.privilege_type "
                "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) a "
                "WHERE n.nspname='public' AND p.prokind IN ('f','p') AND a.grantee <> 0 "
                "ORDER BY p.proname, grantee"
            ),
            width="stretch",
        )


# ===========================================================================
# MAIN
# ===========================================================================
def main():
    init_auth_state()

    if not DB_CONFIG["password"]:
        st.error("No database password found. Set the `PGPASSWORD` environment "
                 "variable, then restart the app.")
        st.stop()

    try:
        get_conn()
    except Exception as e:
        st.error(f"Could not connect to the database:\n\n`{e}`")
        st.info("Make sure PostgreSQL is running on localhost:5432 and the password is correct.")
        st.stop()

    if not st.session_state["logged_in"]:
        render_login()
        st.stop()

    username = st.session_state["username"]
    role = st.session_state["role"]

    st.sidebar.header("User Session")
    render_logout_sidebar(username, role)
    st.sidebar.divider()
    st.sidebar.caption(
        "The permission matrix is enforced by the authenticated role and mirrors "
        "the real `admin_role` / `pharmacist_role` / `cashier_role` grants in the database."
    )
    if st.sidebar.button("Refresh data", width="stretch"):
        st.cache_resource.clear()
        st.rerun()

    st.title(":hospital: Pharmacy Tracker DB - Interactive Viva Dashboard")
    st.caption("Single-file Streamlit + psycopg2 application for presenting the "
               "pharmacy_tracker_db PostgreSQL schema, triggers, functions, procedures, RBAC and indexes.")

    tab1, tab2, tab3, tab4 = st.tabs(
        ["1. Tables & Inventory", "2. POS & Transactions", "3. Query & Explain", "4. Schema Overview"]
    )
    with tab1:
        render_tab_tables(role)
    with tab2:
        render_tab_pos(role)
    with tab3:
        render_tab_explain(role)
    with tab4:
        render_tab_schema()


if __name__ == "__main__":
    main()
