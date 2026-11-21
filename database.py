"""SQLite persistence for providers, milk deliveries, and payments."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exception_type: Any, exception: Any, traceback: Any) -> Any:
        try:
            return super().__exit__(exception_type, exception, traceback)
        finally:
            self.close()


def connect(database_path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path, factory=_ClosingConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _create_records_table(connection: sqlite3.Connection, table_name: str = "milk_records") -> None:
    connection.execute(
        """
        CREATE TABLE {} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_id INTEGER NOT NULL REFERENCES providers(id),
            record_date TEXT NOT NULL,
            morning_ml INTEGER NOT NULL CHECK (morning_ml >= 0),
            evening_ml INTEGER NOT NULL CHECK (evening_ml >= 0),
            rate_paise_per_litre INTEGER NOT NULL CHECK (rate_paise_per_litre >= 0),
            morning_amount_paise INTEGER NOT NULL CHECK (morning_amount_paise >= 0),
            evening_amount_paise INTEGER NOT NULL CHECK (evening_amount_paise >= 0),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (provider_id, record_date)
        )
        """.format(table_name)
    )


def initialize_database(database_path: str | Path) -> None:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS providers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                phone TEXT,
                rate_paise_per_litre INTEGER NOT NULL DEFAULT 5500 CHECK (rate_paise_per_litre >= 0),
                is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        provider_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(providers)").fetchall()
        }
        if "rate_paise_per_litre" not in provider_columns:
            connection.execute(
                "ALTER TABLE providers ADD COLUMN rate_paise_per_litre INTEGER NOT NULL DEFAULT 5500"
            )
        table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'milk_records'"
        ).fetchone()
        if not table_exists:
            _create_records_table(connection)
        else:
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(milk_records)").fetchall()
            }
            if "provider_id" not in columns:
                _migrate_legacy_records(connection)

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_id INTEGER NOT NULL REFERENCES providers(id),
                billing_year INTEGER NOT NULL,
                billing_month INTEGER NOT NULL CHECK (billing_month BETWEEN 1 AND 12),
                payment_date TEXT NOT NULL,
                amount_paise INTEGER NOT NULL CHECK (amount_paise > 0),
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_milk_records_date ON milk_records(record_date)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_milk_records_provider_date ON milk_records(provider_id, record_date)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_payments_provider_month ON payments(provider_id, billing_year, billing_month)"
        )


def _migrate_legacy_records(connection: sqlite3.Connection) -> None:
    legacy_rows = connection.execute(
        "SELECT * FROM milk_records ORDER BY record_date, session"
    ).fetchall()
    if legacy_rows:
        connection.execute(
            "INSERT OR IGNORE INTO providers (name, phone) VALUES (?, '')",
            ("Legacy provider (edit this name)",),
        )
        provider_id = connection.execute(
            "SELECT id FROM providers WHERE name = ? COLLATE NOCASE",
            ("Legacy provider (edit this name)",),
        ).fetchone()["id"]
    else:
        provider_id = None

    connection.execute("ALTER TABLE milk_records RENAME TO milk_records_legacy")
    _create_records_table(connection)
    grouped: dict[str, dict[str, int]] = {}
    for row in legacy_rows:
        item = grouped.setdefault(
            row["record_date"],
            {
                "morning_ml": 0,
                "evening_ml": 0,
                "morning_amount_paise": 0,
                "evening_amount_paise": 0,
                "rate_paise_per_litre": row["rate_paise_per_litre"],
            },
        )
        session = row["session"]
        item[session + "_ml"] = row["quantity_ml"]
        item[session + "_amount_paise"] = row["amount_paise"]
    for record_date, values in grouped.items():
        connection.execute(
            """
            INSERT INTO milk_records (
                provider_id, record_date, morning_ml, evening_ml, rate_paise_per_litre,
                morning_amount_paise, evening_amount_paise
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                provider_id,
                record_date,
                values["morning_ml"],
                values["evening_ml"],
                values["rate_paise_per_litre"],
                values["morning_amount_paise"],
                values["evening_amount_paise"],
            ),
        )
    connection.execute("DROP TABLE milk_records_legacy")


def list_providers(database_path: str | Path, include_inactive: bool = False) -> list[dict[str, Any]]:
    query = "SELECT * FROM providers"
    if not include_inactive:
        query += " WHERE is_active = 1"
    query += " ORDER BY is_active DESC, name COLLATE NOCASE"
    with connect(database_path) as connection:
        return [dict(row) for row in connection.execute(query).fetchall()]


def get_provider(database_path: str | Path, provider_id: int) -> dict[str, Any] | None:
    with connect(database_path) as connection:
        row = connection.execute("SELECT * FROM providers WHERE id = ?", (provider_id,)).fetchone()
    return dict(row) if row else None


def save_provider(
    database_path: str | Path,
    name: str,
    phone: str,
    rate_paise_per_litre: int,
    provider_id: int | None = None,
) -> int:
    with connect(database_path) as connection:
        if provider_id is None:
            cursor = connection.execute(
                "INSERT INTO providers (name, phone, rate_paise_per_litre) VALUES (?, ?, ?)",
                (name, phone, rate_paise_per_litre),
            )
            return int(cursor.lastrowid)
        connection.execute(
            "UPDATE providers SET name = ?, phone = ?, rate_paise_per_litre = ? WHERE id = ?",
            (name, phone, rate_paise_per_litre, provider_id),
        )
        return provider_id


def set_provider_active(database_path: str | Path, provider_id: int, active: bool) -> None:
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE providers SET is_active = ? WHERE id = ?",
            (int(active), provider_id),
        )


def count_active_providers(database_path: str | Path) -> int:
    with connect(database_path) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM providers WHERE is_active = 1").fetchone()[0])


def get_record_for_date(
    database_path: str | Path,
    provider_id: int,
    record_date: str,
) -> dict[str, Any] | None:
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM milk_records WHERE provider_id = ? AND record_date = ?",
            (provider_id, record_date),
        ).fetchone()
    return dict(row) if row else None


def save_record(
    database_path: str | Path,
    provider_id: int,
    record_date: str,
    morning_ml: int,
    evening_ml: int,
    rate_paise_per_litre: int,
    morning_amount_paise: int,
    evening_amount_paise: int,
) -> None:
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO milk_records (
                provider_id, record_date, morning_ml, evening_ml, rate_paise_per_litre,
                morning_amount_paise, evening_amount_paise
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider_id, record_date) DO UPDATE SET
                morning_ml = excluded.morning_ml,
                evening_ml = excluded.evening_ml,
                rate_paise_per_litre = excluded.rate_paise_per_litre,
                morning_amount_paise = excluded.morning_amount_paise,
                evening_amount_paise = excluded.evening_amount_paise,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                provider_id,
                record_date,
                morning_ml,
                evening_ml,
                rate_paise_per_litre,
                morning_amount_paise,
                evening_amount_paise,
            ),
        )


def delete_record(database_path: str | Path, provider_id: int, record_date: str) -> None:
    with connect(database_path) as connection:
        connection.execute(
            "DELETE FROM milk_records WHERE provider_id = ? AND record_date = ?",
            (provider_id, record_date),
        )


def get_records(
    database_path: str | Path,
    provider_id: int | None = None,
    record_date: str | None = None,
) -> list[dict[str, Any]]:
    query = """
        SELECT milk_records.*, providers.name AS provider_name
        FROM milk_records JOIN providers ON providers.id = milk_records.provider_id
    """
    conditions: list[str] = []
    parameters: list[Any] = []
    if provider_id is not None:
        conditions.append("milk_records.provider_id = ?")
        parameters.append(provider_id)
    if record_date:
        conditions.append("milk_records.record_date = ?")
        parameters.append(record_date)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY milk_records.record_date DESC, providers.name COLLATE NOCASE"
    with connect(database_path) as connection:
        return [dict(row) for row in connection.execute(query, parameters).fetchall()]


def get_month_records(
    database_path: str | Path,
    year: int,
    month: int,
    provider_id: int | None = None,
) -> list[dict[str, Any]]:
    month_prefix = "{:04d}-{:02d}".format(year, month)
    query = """
        SELECT milk_records.*, providers.name AS provider_name
        FROM milk_records JOIN providers ON providers.id = milk_records.provider_id
        WHERE milk_records.record_date LIKE ?
    """
    parameters: list[Any] = [month_prefix + "-%"]
    if provider_id is not None:
        query += " AND milk_records.provider_id = ?"
        parameters.append(provider_id)
    query += " ORDER BY milk_records.record_date, providers.name COLLATE NOCASE"
    with connect(database_path) as connection:
        return [dict(row) for row in connection.execute(query, parameters).fetchall()]


def get_month_payments(
    database_path: str | Path,
    year: int,
    month: int,
    provider_id: int,
) -> list[dict[str, Any]]:
    with connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT * FROM payments
            WHERE provider_id = ? AND billing_year = ? AND billing_month = ?
            ORDER BY payment_date, id
            """,
            (provider_id, year, month),
        ).fetchall()
    return [dict(row) for row in rows]


def get_payment(database_path: str | Path, payment_id: int) -> dict[str, Any] | None:
    with connect(database_path) as connection:
        row = connection.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
    return dict(row) if row else None


def save_payment(
    database_path: str | Path,
    provider_id: int,
    year: int,
    month: int,
    payment_date: str,
    amount_paise: int,
    note: str,
) -> None:
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO payments (provider_id, billing_year, billing_month, payment_date, amount_paise, note)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (provider_id, year, month, payment_date, amount_paise, note),
        )


def delete_payment(database_path: str | Path, payment_id: int) -> None:
    with connect(database_path) as connection:
        connection.execute("DELETE FROM payments WHERE id = ?", (payment_id,))
