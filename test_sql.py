import os
import pyodbc
from dotenv import load_dotenv

# Load same env file as scraper
load_dotenv("scrape_gc.env")

SQL_SERVER = os.getenv("SQL_SERVER", "").strip()
SQL_DATABASE = os.getenv("SQL_DATABASE", "").strip()

conn_str = (
    "DRIVER={ODBC Driver 18 for SQL Server};"
    f"SERVER={SQL_SERVER};"
    f"DATABASE={SQL_DATABASE};"
    "Trusted_Connection=yes;"
    "Encrypt=no;"
)

print("[INFO] Connecting with:")
print(conn_str)

try:
    conn = pyodbc.connect(conn_str, timeout=5)
    print("[SUCCESS] Connected!")
    cursor = conn.cursor()
    cursor.execute("SELECT DB_NAME();")
    row = cursor.fetchone()
    print("[RESULT] Query OK — DB name:", row[0])
    conn.close()
except Exception as e:
    print("[ERROR] Could not connect:")
    print(repr(e))
