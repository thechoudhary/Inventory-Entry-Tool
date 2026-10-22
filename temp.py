import sqlite3

conn = sqlite3.connect("inventory.db")
cursor = conn.cursor()

columns = [
    row[1]
    for row in cursor.execute("PRAGMA table_info(inventory)").fetchall()
]

if "lot_number" not in columns:
    cursor.execute("ALTER TABLE inventory ADD COLUMN lot_number TEXT")
    conn.commit()
    print("LOT Number column added successfully.")
else:
    print("LOT Number column already exists.")


cursor.execute("""
    UPDATE inventory
    SET lot_number = '1'
    WHERE lot_number IS NULL OR lot_number = ''
""")

conn.commit()

conn.close()