-- =============================================================================
--  PHARMACY INVENTORY & TRANSACTION MANAGEMENT SYSTEM
--  Consolidated SQL Coding Sheet — Compiled from Phases 1, 2 & 3
--  Institute : Lanka Nippon BizTech Institute (LNBTI)
--  Target    : PostgreSQL 15+
--  Compiled  : August 4, 2026
--
--  This file consolidates every DDL statement, index, trigger, function,
--  stored procedure, security grant, and verification query that appears
--  across the three project report phases:
--    PHASE 1 — Physical Database Tuning & Indexing Report (PIETS)
--    PHASE 2 — Revised Indexing & Database Programming Report
--    PHASE 3 — Transactional Layer & Security Architecture Report
--
--  Table definitions below are reconstructed from the entity descriptions,
--  benchmark population script, and column references used throughout the
--  three reports (explicit CREATE TABLE statements were not published in
--  the original documents). Review and adjust data types/constraints to
--  match your actual environment before running in production.
-- =============================================================================


-- #############################################################################
-- PHASE 1 — PHYSICAL DATABASE TUNING & INDEXING REPORT (PIETS)
-- #############################################################################

-- =============================================================================
-- 1. CORE SCHEMA (reconstructed from Phase 1 entity list & benchmark script)
-- =============================================================================

CREATE TABLE users (
    user_id         SERIAL PRIMARY KEY,
    full_name       VARCHAR(150) NOT NULL,
    email           VARCHAR(150) UNIQUE NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,
    user_role       VARCHAR(30)  NOT NULL   -- e.g. 'MANAGER', 'PHARMACIST'
);

CREATE TABLE managers (
    manager_id          INT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    authorization_level  VARCHAR(20),
    office_extension     VARCHAR(10)
);

CREATE TABLE pharmacists (
    pharmacist_id   INT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    license_number  VARCHAR(50) NOT NULL
);

CREATE TABLE suppliers (
    supplier_id     SERIAL PRIMARY KEY,
    supplier_name   VARCHAR(150) NOT NULL,
    contact_name    VARCHAR(100),
    email           VARCHAR(150),
    phone           VARCHAR(20),
    address         VARCHAR(255)
);

CREATE TABLE medicines (
    medicine_id                    SERIAL PRIMARY KEY,
    medicine_name                  VARCHAR(150) NOT NULL,
    generic_name                   VARCHAR(150),
    category                       VARCHAR(50),
    requires_prescription          BOOLEAN DEFAULT FALSE,
    storage_temperature_celsius    NUMERIC(5,2)
);

CREATE TABLE batches (
    batch_id        SERIAL PRIMARY KEY,
    medicine_id     INT NOT NULL REFERENCES medicines(medicine_id) ON DELETE RESTRICT,
    batch_number    VARCHAR(50) NOT NULL,
    manufacture_date DATE,
    expiry_date     DATE NOT NULL,
    stock_quantity  INT NOT NULL DEFAULT 0,
    cost_price      DECIMAL(10,2) NOT NULL,
    selling_price   DECIMAL(10,2) NOT NULL,
    CONSTRAINT chk_price_rule CHECK (selling_price >= cost_price),
    CONSTRAINT chk_stock_nonnegative CHECK (stock_quantity >= 0)
);

CREATE TABLE sales_orders (
    order_id        SERIAL PRIMARY KEY,
    customer_id     INT,
    pharmacist_id   INT REFERENCES pharmacists(pharmacist_id) ON DELETE RESTRICT,
    order_date      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    total_amount    DECIMAL(10,2) DEFAULT 0.00
);

CREATE TABLE order_items (
    order_item_id   SERIAL PRIMARY KEY,
    order_id        INT NOT NULL REFERENCES sales_orders(order_id) ON DELETE CASCADE,
    batch_id        INT NOT NULL REFERENCES batches(batch_id) ON DELETE RESTRICT,
    prescription_id INT,
    quantity        INT NOT NULL,
    unit_price      DECIMAL(10,2) NOT NULL
);

CREATE TABLE inventory_logs (
    log_id              SERIAL PRIMARY KEY,
    batch_id            INT NOT NULL REFERENCES batches(batch_id) ON DELETE CASCADE,
    log_type            VARCHAR(20),         -- e.g. 'RESTOCK', 'SALE'
    quantity_changed    INT,
    change_quantity     INT,                 -- used by Phase 2 trigger/procedures
    reason              VARCHAR(255),
    notes               VARCHAR(255),
    logged_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE prescriptions (
    prescription_id     SERIAL PRIMARY KEY,
    doctor_name         VARCHAR(150),
    patient_tracking_id VARCHAR(50),
    issued_date         DATE DEFAULT CURRENT_DATE
);

CREATE TABLE prescription_items (
    prescription_item_id SERIAL PRIMARY KEY,
    prescription_id       INT REFERENCES prescriptions(prescription_id) ON DELETE CASCADE,
    medicine_id            INT REFERENCES medicines(medicine_id),
    dosage                 VARCHAR(50),
    refill_count            INT DEFAULT 0
);

CREATE TABLE customer_profiles (
    customer_id     SERIAL PRIMARY KEY,
    full_name       VARCHAR(150),
    contact_number  VARCHAR(20),
    medical_history_flags VARCHAR(255)
);

CREATE TABLE return_logs (
    return_id       SERIAL PRIMARY KEY,
    order_id        INT REFERENCES sales_orders(order_id),
    batch_id        INT REFERENCES batches(batch_id),
    return_quantity INT NOT NULL,
    return_reason   VARCHAR(255),
    returned_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE structural_audit (
    audit_id        SERIAL PRIMARY KEY,
    ddl_event       VARCHAR(255),
    performed_by    VARCHAR(150),
    performed_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);


-- =============================================================================
-- 2. DATABASE PROGRAMMING (PL/pgSQL Extensions) — Section 4
-- =============================================================================

-- 2.1 Automated Dispensing Compliance Safety Trigger
CREATE OR REPLACE FUNCTION text_validate_batch_expiry_lock()
RETURNS TRIGGER AS $$
DECLARE
    v_expiry_date DATE;
BEGIN
    -- Extract the physical expiry parameter from the targeted batch line
    SELECT expiry_date INTO v_expiry_date
    FROM batches
    WHERE batch_id = NEW.batch_id;

    -- Evaluate validation condition against contemporary system clock
    IF v_expiry_date IS NULL THEN
        RAISE EXCEPTION 'Validation Failed: Batch ID % lacks valid expiry declaration.', NEW.batch_id;
    ELSIF v_expiry_date <= CURRENT_DATE THEN
        RAISE EXCEPTION 'Compliance Violation: Batch ID % expired on %.', NEW.batch_id, v_expiry_date;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_order_items_expiry_compliance_gate
BEFORE INSERT OR UPDATE ON order_items
FOR EACH ROW
EXECUTE FUNCTION text_validate_batch_expiry_lock();


-- 2.2 Automated Safety Stock Replenishment Procedure
CREATE OR REPLACE PROCEDURE proc_replenish_critical_safety_stock(
    IN p_min_threshold INT,
    IN p_order_allocation INT
) AS $$
DECLARE
    rec_batch RECORD;
    v_log_notes VARCHAR(255);
BEGIN
    -- Open cursor scan loop over depleted inventory vectors
    FOR rec_batch IN
        SELECT batch_id, medicine_id, stock_quantity
        FROM batches
        WHERE stock_quantity < p_min_threshold
          AND expiry_date > CURRENT_DATE
    LOOP
        -- Process atomic restock update transformation
        UPDATE batches
        SET stock_quantity = stock_quantity + p_order_allocation
        WHERE batch_id = rec_batch.batch_id;

        -- Formulate historical audit ledger string trace
        v_log_notes := 'Auto-Replenish Triggered. Prev Stock: ' ||
            rec_batch.stock_quantity || ' Added: ' || p_order_allocation;

        -- Write audit trailing point entry
        INSERT INTO inventory_logs (batch_id, log_type, quantity_changed, notes)
        VALUES (rec_batch.batch_id, 'RESTOCK', p_order_allocation, v_log_notes);
    END LOOP;
    COMMIT;
END;
$$ LANGUAGE plpgsql;


-- =============================================================================
-- 3. PHASE 1 INDEXES — Section 6 (Detailed Index Rationale & Calculations)
-- =============================================================================

-- 6.1 idx_medicines_name — WHERE medicine_name = ?  (~94.4% I/O saving)
CREATE INDEX idx_medicines_name ON medicines(medicine_name);

-- 6.2 idx_batches_med_expiry — WHERE medicine_id = ? ORDER BY expiry_date ASC (~92.4% I/O saving)
CREATE INDEX idx_batches_med_expiry ON batches(medicine_id, expiry_date ASC);

-- 6.3 idx_order_items_batch — JOIN order_items ON b.batch_id = oi.batch_id (~88.0% I/O saving)
CREATE INDEX idx_order_items_batch ON order_items(batch_id);

-- 6.4 idx_logs_batch_type — WHERE batch_id = ? AND log_type = 'RESTOCK' (~96.2% I/O saving)
CREATE INDEX idx_logs_batch_type ON inventory_logs(batch_id, log_type);


-- =============================================================================
-- 4. BENCHMARK DATA POPULATION SCRIPT — Section 7.1 (10,000-row baseline)
-- =============================================================================

DO $$
DECLARE
    v_user_id INT; v_med_id INT; v_batch_id INT;
    v_order_id INT; v_supplier_id INT; v_manager_id INT;
BEGIN
    -- 1. Populate Base Users & Subtypes
    INSERT INTO users (full_name, email, password_hash, user_role)
    VALUES ('Admin Manager', 'manager@pharmacy.com', 'hash123', 'MANAGER')
    RETURNING user_id INTO v_manager_id;

    INSERT INTO managers (manager_id, authorization_level, office_extension)
    VALUES (v_manager_id, 'LEVEL_3', 'X402');

    INSERT INTO users (full_name, email, password_hash, user_role)
    VALUES ('Staff Pharmacist', 'pharmacist@pharmacy.com', 'hash456', 'PHARMACIST')
    RETURNING user_id INTO v_user_id;

    INSERT INTO pharmacists (pharmacist_id, license_number)
    VALUES (v_user_id, 'PH-PHARM-2026-991');

    -- 2. Populate Suppliers (100 vendors)
    FOR i IN 1..100 LOOP
        INSERT INTO suppliers (supplier_name, contact_name, email, phone, address)
        VALUES ('PharmaCorp Global ' || i, 'Agent ' || i,
                'contact' || i || '@pharmacorp.com',
                '+9477123' || LPAD(i::text, 4, '0'),
                'Industrial Sector Colombo, Block ' || i);
    END LOOP;
    SELECT supplier_id INTO v_supplier_id FROM suppliers LIMIT 1;

    -- 3. Populate Medicines Catalog (1,000 unique drug variants)
    FOR i IN 1..1000 LOOP
        INSERT INTO medicines (medicine_name, generic_name, category,
            requires_prescription, storage_temperature_celsius)
        VALUES ('Amoxicillin-Brand-' || i, 'Amoxicillin Trihydrate',
            CASE WHEN i%4=0 THEN 'Antibiotic' WHEN i%4=1 THEN 'Analgesic'
                 WHEN i%4=2 THEN 'Cardiovascular' ELSE 'Other' END,
            CASE WHEN i%2=0 THEN TRUE ELSE FALSE END, 22.50);
    END LOOP;

    -- 4. Populate Batches Table (10,000 rows)
    FOR i IN 1..10000 LOOP
        v_med_id := (i % 1000) + 1;
        INSERT INTO batches (medicine_id, batch_number, manufacture_date,
            expiry_date, stock_quantity, cost_price, selling_price)
        VALUES (v_med_id, 'LOT-' || i || '-2026',
            CURRENT_DATE - (i%180+30), CURRENT_DATE + (i%365+1),
            500+(i%500), 10.00+(i%50), 15.00+(i%50));
    END LOOP;

    -- 5. Populate Sales Orders (2,000 records)
    FOR i IN 1..2000 LOOP
        INSERT INTO sales_orders (customer_id, pharmacist_id, order_date, total_amount)
        VALUES (NULL, v_user_id,
            CURRENT_TIMESTAMP-(i||' minutes')::interval, 0.00)
        RETURNING order_id INTO v_order_id;
    END LOOP;

    -- 6. Populate Order Items (10,000 rows)
    FOR i IN 1..10000 LOOP
        v_order_id := (i%2000)+1; v_batch_id := (i%10000)+1;
        INSERT INTO order_items (order_id, batch_id, prescription_id, quantity, unit_price)
        VALUES (v_order_id, v_batch_id, NULL, 2, 25.00);
    END LOOP;

    -- 7. Populate Inventory Logs (10,000 tracking points)
    FOR i IN 1..10000 LOOP
        v_batch_id := (i%10000)+1;
        INSERT INTO inventory_logs (batch_id, log_type, quantity_changed, notes)
        VALUES (v_batch_id,
            CASE WHEN i%2=0 THEN 'RESTOCK' ELSE 'SALE' END,
            100, 'System tracking baseline reference ID ' || i);
    END LOOP;
END $$;


-- =============================================================================
-- 5. DIAGNOSTIC QUERY PLANS — Section 7.2 (EXPLAIN ANALYZE test predicates)
-- =============================================================================

-- Test Case 1: Point Search Lookup on Product Catalog (idx_medicines_name)
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM medicines WHERE medicine_name = 'Amoxicillin-Brand-789';

-- Test Case 2: Composite Filtering & Sorting for Product Rotations (idx_batches_med_expiry)
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM batches WHERE medicine_id = 450 ORDER BY expiry_date ASC;

-- Test Case 3: High-Frequency Relational Joins (idx_order_items_batch)
EXPLAIN (ANALYZE, BUFFERS)
SELECT b.batch_number, oi.quantity
FROM batches b
JOIN order_items oi ON b.batch_id = oi.batch_id
WHERE b.batch_id = 7500;

-- Test Case 4: Operational Audit Trail Inspections (idx_logs_batch_type)
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM inventory_logs WHERE batch_id = 1250 AND log_type = 'RESTOCK';

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM inventory_logs WHERE batch_id = 1251 AND log_type = 'RESTOCK';


-- #############################################################################
-- PHASE 2 — REVISED INDEXING & DATABASE PROGRAMMING REPORT
-- #############################################################################

-- =============================================================================
-- 1. REVISED INDEXING STRATEGY — Section 2.1 (DDL Implementation)
-- =============================================================================

-- 1. Composite Index for Batch Expiry & Stock Tracking
CREATE INDEX idx_batches_expiry_stock ON batches(expiry_date, stock_quantity);

-- 2. Index for Fast Medicine Name Searching
CREATE INDEX idx_medicines_name_v2 ON medicines(medicine_name);

-- 3. Index for Date-Range Sales Reports
CREATE INDEX idx_sales_orders_date ON sales_orders(order_date);

-- 4. Composite Index for Order Line-Item Joins
CREATE INDEX idx_order_items_order_batch ON order_items(order_id, batch_id);

-- 5. Foreign Key Index for Return Logs
CREATE INDEX idx_return_logs_order_id ON return_logs(order_id);


-- =============================================================================
-- 2. DATABASE PROGRAMMING — TRIGGERS — Section 3
-- =============================================================================

-- Trigger 1: Stock Validation Guard (trg_prevent_negative_stock)
CREATE OR REPLACE FUNCTION fn_prevent_negative_stock()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.stock_quantity < 0 THEN
        RAISE EXCEPTION 'Stock quantity cannot be negative! Batch ID % attempted stock: %',
            NEW.batch_id, NEW.stock_quantity;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_prevent_negative_stock
BEFORE INSERT OR UPDATE ON batches
FOR EACH ROW
EXECUTE FUNCTION fn_prevent_negative_stock();


-- Trigger 2: Automated Sales Order Header Total Calculation (trg_update_order_total)
CREATE OR REPLACE FUNCTION fn_update_order_total()
RETURNS TRIGGER AS $$
DECLARE
    v_target_order_id INT;
BEGIN
    IF (TG_OP = 'DELETE') THEN
        v_target_order_id := OLD.order_id;
    ELSE
        v_target_order_id := NEW.order_id;
    END IF;

    UPDATE sales_orders
    SET total_amount = COALESCE((
        SELECT SUM(quantity * unit_price)
        FROM order_items
        WHERE order_id = v_target_order_id
    ), 0.00)
    WHERE order_id = v_target_order_id;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_update_order_total
AFTER INSERT OR UPDATE OR DELETE ON order_items
FOR EACH ROW
EXECUTE FUNCTION fn_update_order_total();


-- Trigger 3: Inventory Change Audit Logger (trg_log_stock_change)
CREATE OR REPLACE FUNCTION fn_log_stock_change()
RETURNS TRIGGER AS $$
DECLARE
    v_diff INT;
BEGIN
    IF (OLD.stock_quantity IS DISTINCT FROM NEW.stock_quantity) THEN
        v_diff := NEW.stock_quantity - OLD.stock_quantity;
        INSERT INTO inventory_logs (batch_id, change_quantity, reason)
        VALUES (NEW.batch_id, v_diff, 'Automated Trigger Audit: Stock level adjusted from ' ||
            OLD.stock_quantity || ' to ' || NEW.stock_quantity);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_log_stock_change
AFTER UPDATE OF stock_quantity ON batches
FOR EACH ROW
EXECUTE FUNCTION fn_log_stock_change();


-- =============================================================================
-- 3. DATABASE PROGRAMMING — PL/pgSQL FUNCTIONS — Section 4
-- =============================================================================

-- Function 1: Process Medicine Sale Transaction (process_medicine_sale)
CREATE OR REPLACE FUNCTION process_medicine_sale(
    p_batch_id INT,
    p_qty INT
)
RETURNS INT AS $$
DECLARE
    v_order_id INT;
    v_unit_price DECIMAL(10,2);
    v_current_stock INT;
    v_total_price DECIMAL(10,2);
BEGIN
    -- Check batch existence and fetch details
    SELECT stock_quantity, selling_price
    INTO v_current_stock, v_unit_price
    FROM batches
    WHERE batch_id = p_batch_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Batch ID % not found!', p_batch_id;
    END IF;

    -- Validate stock availability
    IF v_current_stock < p_qty THEN
        RAISE EXCEPTION 'Insufficient stock! Requested: %, Available: %', p_qty, v_current_stock;
    END IF;

    v_total_price := v_unit_price * p_qty;

    -- Create Sales Order Header
    INSERT INTO sales_orders (total_amount)
    VALUES (v_total_price)
    RETURNING order_id INTO v_order_id;

    -- Insert Order Line Item
    INSERT INTO order_items (order_id, batch_id, quantity, unit_price)
    VALUES (v_order_id, p_batch_id, p_qty, v_unit_price);

    -- Deduct Stock Quantity
    UPDATE batches
    SET stock_quantity = stock_quantity - p_qty
    WHERE batch_id = p_batch_id;

    RAISE NOTICE 'Sale processed successfully! New Order ID: %, Total Bill: Rs. %', v_order_id, v_total_price;
    RETURN v_order_id;

EXCEPTION
    WHEN OTHERS THEN
        RAISE EXCEPTION 'Sale transaction failed: %', SQLERRM;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;


-- Function 2: Process Medicine Return & Restocking (process_medicine_return)
CREATE OR REPLACE FUNCTION process_medicine_return(
    p_order_id INT,
    p_batch_id INT,
    p_return_qty INT,
    p_reason TEXT,
    p_is_damaged BOOLEAN DEFAULT FALSE
)
RETURNS VOID AS $$
DECLARE
    v_unit_price DECIMAL(10,2);
    v_refund_amount DECIMAL(10,2);
BEGIN
    SELECT selling_price INTO v_unit_price
    FROM batches
    WHERE batch_id = p_batch_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Batch ID % not found!', p_batch_id;
    END IF;

    v_refund_amount := v_unit_price * p_return_qty;

    -- Log Return Event
    INSERT INTO return_logs (order_id, batch_id, return_quantity, return_reason)
    VALUES (p_order_id, p_batch_id, p_return_qty, p_reason);

    -- Conditionally Restock
    IF NOT p_is_damaged THEN
        BEGIN
            UPDATE batches
            SET stock_quantity = stock_quantity + p_return_qty
            WHERE batch_id = p_batch_id;

            RAISE NOTICE 'Return processed successfully! Stock restocked. Customer Refund: Rs. %', v_refund_amount;
        EXCEPTION
            WHEN OTHERS THEN
                RAISE NOTICE 'Stock update failed. Return log saved.';
        END;
    ELSE
        RAISE NOTICE 'Items marked as damaged. Stock was not replenished. Return log saved.';
    END IF;

EXCEPTION
    WHEN OTHERS THEN
        RAISE EXCEPTION 'Return transaction failed completely: %', SQLERRM;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;


-- Function 3: Calculate Medicine Valuation & Stock Stats (calculate_batch_valuation)
CREATE OR REPLACE FUNCTION calculate_batch_valuation(
    p_medicine_id INT,
    OUT total_units INT,
    OUT total_cost_value DECIMAL(10,2),
    OUT total_retail_value DECIMAL(10,2)
) AS $$
BEGIN
    SELECT
        COALESCE(SUM(stock_quantity), 0),
        COALESCE(SUM(stock_quantity * cost_price), 0.00),
        COALESCE(SUM(stock_quantity * selling_price), 0.00)
    INTO total_units, total_cost_value, total_retail_value
    FROM batches
    WHERE medicine_id = p_medicine_id;
END;
$$ LANGUAGE plpgsql;


-- =============================================================================
-- 4. DATABASE PROGRAMMING — STORED PROCEDURES — Section 5
-- =============================================================================

-- Procedure 1: Batch Restock & Re-pricing Procedure (sp_restock_batch)
CREATE OR REPLACE PROCEDURE sp_restock_batch(
    p_batch_id INT,
    p_add_qty INT,
    p_new_cost DECIMAL(10,2),
    p_new_selling DECIMAL(10,2)
)
LANGUAGE plpgsql AS $$
BEGIN
    IF p_add_qty <= 0 THEN
        RAISE EXCEPTION 'Restock quantity must be greater than zero!';
    END IF;

    UPDATE batches
    SET stock_quantity = stock_quantity + p_add_qty,
        cost_price = p_new_cost,
        selling_price = p_new_selling
    WHERE batch_id = p_batch_id;

    INSERT INTO inventory_logs (batch_id, change_quantity, reason)
    VALUES (p_batch_id, p_add_qty, 'Batch Restock Procedure Execution');

    RAISE NOTICE 'Batch % successfully restocked with % units.', p_batch_id, p_add_qty;
END;
$$;


-- Procedure 2: Inventory Write-off with Concurrency Locking (sp_batch_writeoff)
-- NOTE: Phase 3 refers to this capability as the function process_batch_writeoff();
-- the underlying logic (FOR UPDATE row locking + zero-out + audit log) is identical.
CREATE OR REPLACE PROCEDURE sp_batch_writeoff(
    p_batch_id INT,
    p_reason TEXT
)
LANGUAGE plpgsql AS $$
DECLARE
    v_current_stock INT;
BEGIN
    -- Exclusive Row Locking (FOR UPDATE)
    SELECT stock_quantity
    INTO v_current_stock
    FROM batches
    WHERE batch_id = p_batch_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Batch ID % not found!', p_batch_id;
    END IF;

    IF v_current_stock = 0 THEN
        RAISE NOTICE 'Batch ID % already has 0 stock.', p_batch_id;
        RETURN;
    END IF;

    INSERT INTO inventory_logs (batch_id, change_quantity, reason)
    VALUES (p_batch_id, -v_current_stock, p_reason);

    UPDATE batches
    SET stock_quantity = 0
    WHERE batch_id = p_batch_id;

    RAISE NOTICE 'Write-off successful! Batch % cleared (% units removed). Reason: %',
        p_batch_id, v_current_stock, p_reason;
END;
$$;

-- Convenience wrapper so Phase 3's "process_batch_writeoff(...)" call syntax
-- (used in the RBAC/UBAC verification matrix) resolves to the procedure above.
CREATE OR REPLACE FUNCTION process_batch_writeoff(
    p_batch_id INT,
    p_reason TEXT
)
RETURNS VOID AS $$
BEGIN
    CALL sp_batch_writeoff(p_batch_id, p_reason);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;


-- Procedure 3: Sales Performance Reporting Procedure (sp_generate_sales_summary)
CREATE OR REPLACE PROCEDURE sp_generate_sales_summary(
    p_start_date TIMESTAMP,
    p_end_date TIMESTAMP
)
LANGUAGE plpgsql AS $$
DECLARE
    v_total_orders INT;
    v_total_revenue DECIMAL(10,2);
BEGIN
    SELECT COUNT(*), COALESCE(SUM(total_amount), 0.00)
    INTO v_total_orders, v_total_revenue
    FROM sales_orders
    WHERE order_date BETWEEN p_start_date AND p_end_date;

    RAISE NOTICE '================ SALES SUMMARY REPORT ================';
    RAISE NOTICE 'Period: % to %', p_start_date, p_end_date;
    RAISE NOTICE 'Total Orders Processed: %', v_total_orders;
    RAISE NOTICE 'Total Revenue Generated: Rs. %', v_total_revenue;
    RAISE NOTICE '======================================================';
END;
$$;


-- =============================================================================
-- 5. TESTING & EXECUTION VERIFICATION — Section 6
-- =============================================================================

-- 1. Test Sale Function
SELECT process_medicine_sale(87, 2);

-- 2. Test Sales Return Function
SELECT process_medicine_return(2001, 87, 1, 'Customer Refund Request', false);

-- 3. Test Batch Valuation Function
SELECT * FROM calculate_batch_valuation(1);

-- 4. Test Restock Procedure
CALL sp_restock_batch(87, 50, 40.00, 52.00);

-- 5. Test Batch Write-off Procedure
CALL sp_batch_writeoff(1, 'Expired Batch Safe Disposal');

-- 6. Test Sales Summary Report Procedure
CALL sp_generate_sales_summary('2026-01-01 00:00:00', '2026-12-31 23:59:59');


-- #############################################################################
-- PHASE 3 — TRANSACTIONAL LAYER & SECURITY ARCHITECTURE REPORT
-- #############################################################################

-- =============================================================================
-- 1. DECLARATIVE INTEGRITY CONSTRAINTS — Section 2.2
--    (already applied on the batches table above; restated here for reference)
-- =============================================================================
-- ALTER TABLE batches ADD CONSTRAINT chk_price_rule CHECK (selling_price >= cost_price);
-- ALTER TABLE batches ADD CONSTRAINT chk_stock_nonnegative CHECK (stock_quantity >= 0);


-- =============================================================================
-- 2. ROLE-BASED ACCESS CONTROL (RBAC) — Section 4.1
-- =============================================================================

CREATE ROLE admin_role;
CREATE ROLE pharmacist_role;
CREATE ROLE cashier_role;

-- admin_role: Full DDL, DML, and execution rights across all system objects.
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO admin_role;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO admin_role;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO admin_role;
GRANT USAGE, CREATE ON SCHEMA public TO admin_role;

-- pharmacist_role: stock monitoring, sales processing, and return processing.
GRANT SELECT ON medicines, batches, sales_orders, order_items, inventory_logs TO pharmacist_role;
GRANT EXECUTE ON FUNCTION process_medicine_sale(INT, INT) TO pharmacist_role;
GRANT EXECUTE ON FUNCTION process_medicine_return(INT, INT, INT, TEXT, BOOLEAN) TO pharmacist_role;
GRANT SELECT, INSERT, UPDATE ON batches, sales_orders, order_items, return_logs TO pharmacist_role;

-- cashier_role: viewing medicine prices and executing point-of-sale transactions only.
GRANT SELECT ON medicines, batches TO cashier_role;
GRANT EXECUTE ON FUNCTION process_medicine_sale(INT, INT) TO cashier_role;
GRANT SELECT, INSERT ON sales_orders, order_items TO cashier_role;


-- =============================================================================
-- 3. USER-BASED ACCESS CONTROL (UBAC) — Section 4.2
-- =============================================================================

CREATE USER gushan_admin WITH PASSWORD 'change_me' IN ROLE admin_role;
CREATE USER sarah_pharmacist WITH PASSWORD 'change_me' IN ROLE pharmacist_role;
CREATE USER alex_cashier WITH PASSWORD 'change_me' IN ROLE cashier_role;

-- User-Level Grant: sarah_pharmacist explicitly granted EXECUTE on process_batch_writeoff,
-- expanding her privileges beyond the baseline pharmacist_role.
GRANT EXECUTE ON FUNCTION process_batch_writeoff(INT, TEXT) TO sarah_pharmacist;

-- Explicit Revocation: DELETE on sales_orders revoked from alex_cashier to prevent audit tampering.
REVOKE DELETE ON sales_orders FROM alex_cashier;


-- =============================================================================
-- 4. VERIFICATION & SECURITY TESTING MATRIX — Section 5
-- =============================================================================

-- Test 1 — Sales Execution (as alex_cashier)          -> Expected: PASS (Order created)
SELECT process_medicine_sale(87, 1);

-- Test 2 — Unauthorized Write-off (as alex_cashier)    -> Expected: PASS (permission denied)
SELECT process_batch_writeoff(87, 'Test');

-- Test 3 — Return Processing (as sarah_pharmacist)     -> Expected: PASS (Restocked, Refund: Rs. 52.00)
SELECT process_medicine_return(2003, 87, 1, 'Damaged', false);

-- Test 4 — UBAC Grant Execution (as sarah_pharmacist)  -> Expected: PASS (Batch 87 cleared, 586 units)
SELECT process_batch_writeoff(87, 'Expired');


-- =============================================================================
-- END OF CONSOLIDATED CODING SHEET (Phases 1–3)
-- =============================================================================
