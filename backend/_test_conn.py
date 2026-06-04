import os, pyodbc
from pathlib import Path

env_path = Path(__file__).with_name(".env")
for line in env_path.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    os.environ[k.strip()] = v.strip()

print("User:", os.environ.get("ERP_SQL_USERNAME"))
print("Pwd len:", len(os.environ.get("ERP_SQL_PASSWORD", "")))
print("Server:", os.environ.get("ERP_SQL_SERVER"))

for db in ("ERPAI", "call_analyzer"):
    cs = (
        f"Driver={{ODBC Driver 18 for SQL Server}};"
        f"Server={os.environ['ERP_SQL_SERVER']};Database={db};"
        f"UID={os.environ['ERP_SQL_USERNAME']};PWD={os.environ['ERP_SQL_PASSWORD']};"
        f"Encrypt=yes;TrustServerCertificate=yes;Connection Timeout=10;"
    )
    try:
        c = pyodbc.connect(cs, autocommit=True)
        row = c.cursor().execute("SELECT SYSTEM_USER, DB_NAME()").fetchone()
        print(f"{db} OK -> login={row[0]}, db={row[1]}")
        c.close()
    except Exception as e:
        print(f"{db} FAIL -> {e}")
