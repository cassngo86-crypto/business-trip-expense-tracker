import sqlite3
import pandas as pd
import os

DB_NAME = "expenses.db"

def init_db():
    """Initializes the database schema if the file does not exist."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            organization TEXT,
            amount REAL,
            category TEXT,
            currency TEXT DEFAULT 'SGD',
            original_amount REAL,
            receipt_file BLOB
        )
    ''')
    conn.commit()
    conn.close()

def fetch_all_expenses():
    """Fetches all rows from the database and returns them as a Pandas DataFrame."""
    conn = sqlite3.connect(DB_NAME)
    try:
        df = pd.read_sql_query("SELECT * FROM expenses", conn)
    except Exception:
        df = pd.DataFrame()
    finally:
        conn.close()
    return df

# ==========================================
# NEW BACKUP & RESTORE FUNCTIONS
# ==========================================
def get_db_bytes():
    """Reads the local SQLite database file as raw bytes for downlading."""
    if os.path.exists(DB_NAME):
        with open(DB_NAME, "rb") as f:
            return f.read()
    return None

def restore_db_from_bytes(uploaded_bytes):
    """Overwrites the local SQLite database file with uploaded bytes."""
    with open(DB_NAME, "wb") as f:
        f.write(uploaded_bytes)

def delete_expense(expense_id):
    """Deletes a specific expense record by its ID."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
    conn.commit()
    conn.close()
def fetch_receipt_file(expense_id):
    """Retrieves the binary receipt blob and file metadata for a specific expense."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT receipt_file, organization, category FROM expenses WHERE id = ?", (expense_id,))
    row = cursor.fetchone()
    conn.close()
    
    if row and row[0]:
        return {
            "bytes": row[0],
            "merchant": row[1],
            "category": row[2]
        }
    return None

def get_expense_trend(conn):
    """Fetches total daily spending for trend timeline analysis."""
    import pandas as pd
    query = """
        SELECT date, SUM(amount) as total_amount 
        FROM expenses 
        GROUP BY date 
        ORDER BY date ASC
    """
    df = pd.read_sql_query(query, conn)
    return df

def get_category_breakdown(conn):
    """Fetches total spending grouped by expense categories."""
    import pandas as pd
    query = """
        SELECT category, SUM(amount) as total_amount 
        FROM expenses 
        GROUP BY category 
        ORDER BY total_amount DESC
    """
    return pd.read_sql_query(query, conn)