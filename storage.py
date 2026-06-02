import sqlite3
import os

DB_PATH = os.getenv('DB_PATH', 'users.db')


def init_db():
    with sqlite3.connect(DB_PATH) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id   INTEGER PRIMARY KEY,
            org_name  TEXT, org_href  TEXT,
            store_name TEXT, store_href TEXT,
            expense_name TEXT, expense_href TEXT
        )''')


def get_user(user_id: int) -> dict | None:
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        row = db.execute('SELECT * FROM users WHERE user_id=?', (user_id,)).fetchone()
        return dict(row) if row else None


def upsert_user(user_id: int, **fields):
    with sqlite3.connect(DB_PATH) as db:
        db.execute('INSERT OR IGNORE INTO users(user_id) VALUES(?)', (user_id,))
        if fields:
            sets = ','.join(f'{k}=?' for k in fields)
            db.execute(f'UPDATE users SET {sets} WHERE user_id=?', (*fields.values(), user_id))
