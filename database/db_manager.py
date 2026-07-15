import sqlite3
import pandas as pd

DB_NAME = "expenses.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Added receipt_file column as BLOB
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

def insert_expense(date, organization, amount, category, currency='SGD', original_amount=None, receipt_file=None):
    if original_amount is None:
        original_amount = amount
        
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO expenses (date, organization, amount, category, currency, original_amount, receipt_file) 
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (date, organization, amount, category, currency, original_amount, receipt_file)
    )
    conn.commit()
    conn.close()

def delete_expense_record(record_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM expenses WHERE id = ?", (record_id,))
    conn.commit()
    conn.close()

def fetch_all_expenses():
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql_query("SELECT * FROM expenses", conn)
    conn.close()
    if not df.empty:
        df['date'] = pd.to_datetime(df['date'])
    return df