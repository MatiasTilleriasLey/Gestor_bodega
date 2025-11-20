"""
Script de migración para agregar la tabla dispatch_photos.

Uso:
    python db_migrations/add_dispatch_photos.py

El script intenta ubicar el archivo SQLite (mydb.db) en la raíz del proyecto o
en la carpeta instance/. No borra datos ni modifica tablas existentes.
"""

import os
import sqlite3
from pathlib import Path


def find_db():
    candidates = [
        Path("mydb.db"),
        Path("instance") / "mydb.db",
    ]
    for path in candidates:
        if path.exists():
            return path
    raise SystemExit("No se encontró mydb.db; ejecuta esto desde la raíz del proyecto.")


def run_migration():
    db_path = find_db()
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS dispatch_photos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id INTEGER NOT NULL,
        stage TEXT NOT NULL,
        path TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(batch_id) REFERENCES dispatch_batches(id)
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_dispatch_photos_batch ON dispatch_photos(batch_id);")

    conn.commit()
    conn.close()
    print(f"Tabla dispatch_photos verificada en {db_path}")


if __name__ == "__main__":
    run_migration()
