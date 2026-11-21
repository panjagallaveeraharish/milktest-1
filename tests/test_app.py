import csv
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

from app import calculate_amount_paise, create_app
from database import connect, get_month_records, initialize_database, list_providers


class MilkManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "test.db"
        self.app = create_app(self.database_path)
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()
        self.provider_id = self.add_provider("Test Dairy")

    def tearDown(self):
        self.temp_dir.cleanup()

    def add_provider(self, name, rate="55.00"):
        response = self.client.post(
            "/providers",
            data={"action": "add", "name": name, "phone": "", "rate_per_litre": rate},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        with connect(self.database_path) as connection:
            row = connection.execute("SELECT id FROM providers WHERE name = ?", (name,)).fetchone()
        return row["id"]

    def test_exact_preset_and_configurable_custom_prices(self):
        self.assertEqual(calculate_amount_paise(1000), 5500)
        self.assertEqual(calculate_amount_paise(500), 3300)
        self.assertEqual(calculate_amount_paise(250), 1600)
        self.assertEqual(calculate_amount_paise(750), 4150)
        self.assertEqual(calculate_amount_paise(1250), 7100)
        self.assertEqual(calculate_amount_paise(1100, 6000), 6100)

    def test_provider_crud_and_archive_preserves_history(self):
        other_id = self.add_provider("Second Dairy", "60.00")
        self.assertNotEqual(other_id, self.provider_id)
        self.client.post(
            "/daily",
            data={
                "provider_id": self.provider_id,
                "date": "2026-03-04",
                "morning_litres": "1.25",
                "evening_litres": "0.5",
            },
        )
        response = self.client.post(
            "/providers",
            data={
                "action": "edit",
                "provider_id": self.provider_id,
                "name": "Renamed Dairy",
                "phone": "123456",
                "rate_per_litre": "58.50",
            },
            follow_redirects=True,
        )
        self.assertIn("Provider information saved.", response.get_data(as_text=True))
        self.client.post(
            "/providers",
            data={"action": "archive", "provider_id": self.provider_id},
        )
        self.assertEqual(get_month_records(self.database_path, 2026, 3, self.provider_id)[0]["morning_ml"], 1250)
        self.assertNotIn(self.provider_id, [p["id"] for p in list_providers(self.database_path)])
        self.assertEqual(
            self.client.get(
                "/daily?provider_id={}&date=2026-03-04".format(self.provider_id)
            ).status_code,
            200,
        )
        payment_response = self.client.post(
            "/payments",
            data={
                "provider_id": self.provider_id,
                "year": "2026",
                "month": "3",
                "payment_date": "2026-03-31",
                "amount": "1.00",
                "note": "",
            },
            follow_redirects=True,
        )
        self.assertIn("Payment recorded.", payment_response.get_data(as_text=True))

    def test_decimal_daily_record_edit_duplicate_prevention_and_provider_isolation(self):
        response = self.client.post(
            "/daily",
            data={
                "provider_id": self.provider_id,
                "date": "2026-03-04",
                "morning_litres": "1.25",
                "evening_litres": "0.5",
            },
            follow_redirects=True,
        )
        self.assertIn("Milk record saved.", response.get_data(as_text=True))
        records = get_month_records(self.database_path, 2026, 3, self.provider_id)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["morning_ml"], 1250)
        self.assertEqual(records[0]["evening_ml"], 500)
        self.assertEqual(records[0]["morning_amount_paise"], 7100)
        self.assertEqual(records[0]["evening_amount_paise"], 3300)

        self.client.post(
            "/daily",
            data={
                "provider_id": self.provider_id,
                "date": "2026-03-04",
                "morning_litres": "0",
                "evening_litres": "2.5",
            },
        )
        records = get_month_records(self.database_path, 2026, 3, self.provider_id)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["morning_ml"], 0)
        self.assertEqual(records[0]["evening_ml"], 2500)

        second_id = self.add_provider("Another Dairy")
        self.client.post(
            "/daily",
            data={
                "provider_id": second_id,
                "date": "2026-03-04",
                "morning_litres": "0.25",
                "evening_litres": "0",
            },
        )
        self.assertEqual(len(get_month_records(self.database_path, 2026, 3)), 2)

    def test_provider_profile_filters_own_month_and_exports_settlement(self):
        second_id = self.add_provider("Second Dairy")
        for provider_id, morning, evening in (
            (self.provider_id, "1", "0.5"),
            (second_id, "2", "0"),
        ):
            self.client.post(
                "/daily",
                data={
                    "provider_id": provider_id,
                    "date": "2026-03-04",
                    "morning_litres": morning,
                    "evening_litres": evening,
                },
            )
        self.client.post(
            "/payments",
            data={
                "provider_id": self.provider_id,
                "year": "2026",
                "month": "3",
                "payment_date": "2026-03-31",
                "amount": "50",
                "note": "",
            },
        )

        profile = self.client.get(
            "/providers/{}?year=2026&month=3".format(self.provider_id)
        )
        profile_html = profile.get_data(as_text=True)
        self.assertEqual(profile.status_code, 200)
        self.assertIn("Test Dairy", profile_html)
        self.assertIn("1.5 L", profile_html)
        self.assertIn("₹88", profile_html)
        self.assertIn("₹50", profile_html)
        self.assertIn("Partially paid", profile_html)
        self.assertNotIn("Second Dairy", profile_html)

        other_month = self.client.get(
            "/providers/{}?year=2026&month=4".format(self.provider_id)
        ).get_data(as_text=True)
        self.assertIn("No milk records for Test Dairy in April 2026.", other_month)

        dashboard = self.client.get("/").get_data(as_text=True)
        self.assertIn('data-provider-search', dashboard)
        self.assertIn('href="/providers/{}"'.format(self.provider_id), dashboard)
        self.assertIn('href="/providers/{}"'.format(second_id), dashboard)

        export = self.client.get(
            "/monthly/export.csv?provider_id={}&year=2026&month=3".format(self.provider_id)
        ).get_data(as_text=True)
        self.assertIn("Test Dairy", export)
        self.assertNotIn("Second Dairy", export)
        self.assertIn("88.00,50.00,38.00,Partially paid", export)
        workbook_response = self.client.get(
            "/monthly/export.xlsx?provider_id={}&year=2026&month=3".format(self.provider_id)
        )
        workbook = load_workbook(io.BytesIO(workbook_response.data), read_only=True)
        self.assertEqual(workbook.sheetnames, ["Monthly summary", "Daily records"])
        self.assertEqual(workbook["Monthly summary"]["A2"].value, "Test Dairy")
        workbook.close()

    def test_invalid_quantities_are_rejected(self):
        for quantity in ("-0.1", "0.0001", "101", "not-a-number"):
            response = self.client.post(
                "/daily",
                data={
                    "provider_id": self.provider_id,
                    "date": "2026-03-04",
                    "morning_litres": quantity,
                    "evening_litres": "0",
                },
            )
            self.assertEqual(response.status_code, 200)
            error_page = response.get_data(as_text=True).lower()
            self.assertTrue("quantity" in error_page or "number" in error_page)
        self.assertEqual(get_month_records(self.database_path, 2026, 3), [])

    def test_payments_report_status_and_csv_export(self):
        self.client.post(
            "/daily",
            data={
                "provider_id": self.provider_id,
                "date": "2026-03-04",
                "morning_litres": "1",
                "evening_litres": "0.5",
            },
        )
        response = self.client.post(
            "/payments",
            data={
                "provider_id": self.provider_id,
                "year": "2026",
                "month": "3",
                "payment_date": "2026-03-31",
                "amount": "50",
                "note": "Advance",
            },
            follow_redirects=True,
        )
        self.assertIn("Payment recorded.", response.get_data(as_text=True))
        report = self.client.get(
            "/monthly?year=2026&month=3&provider_id={}".format(self.provider_id)
        ).get_data(as_text=True)
        self.assertIn("Partially paid", report)
        self.assertIn("₹38", report)
        self.client.post(
            "/payments",
            data={
                "provider_id": self.provider_id,
                "year": "2026",
                "month": "3",
                "payment_date": "2026-03-31",
                "amount": "38",
                "note": "Final balance",
            },
        )
        paid_report = self.client.get(
            "/monthly?year=2026&month=3&provider_id={}".format(self.provider_id)
        ).get_data(as_text=True)
        self.assertIn(">Paid<", paid_report)
        csv_response = self.client.get(
            "/monthly/export.csv?year=2026&month=3&provider_id={}".format(self.provider_id)
        )
        rows = list(csv.reader(io.StringIO(csv_response.get_data(as_text=True).lstrip("\ufeff"))))
        self.assertEqual(rows[1], ["Test Dairy", "2026-03-04", "1.000", "0.500", "1.500", "88.00"])

    def test_legacy_database_migration_preserves_existing_entries(self):
        legacy_path = Path(self.temp_dir.name) / "legacy.db"
        connection = sqlite3.connect(legacy_path)
        try:
            connection.execute(
                """
                CREATE TABLE milk_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_date TEXT NOT NULL,
                    session TEXT NOT NULL,
                    quantity_ml INTEGER NOT NULL,
                    rate_paise_per_litre INTEGER NOT NULL,
                    amount_paise INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (record_date, session)
                )
                """
            )
            connection.executemany(
                """
                INSERT INTO milk_records (record_date, session, quantity_ml, rate_paise_per_litre, amount_paise)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    ("2026-03-04", "morning", 1000, 5500, 5500),
                    ("2026-03-04", "evening", 500, 5500, 2750),
                ],
            )
            connection.commit()
        finally:
            connection.close()
        initialize_database(legacy_path)
        providers = list_providers(legacy_path)
        self.assertEqual(len(providers), 1)
        records = get_month_records(legacy_path, 2026, 3, providers[0]["id"])
        self.assertEqual(len(records), 1)
        self.assertEqual((records[0]["morning_ml"], records[0]["evening_ml"]), (1000, 500))
        self.assertEqual((records[0]["morning_amount_paise"], records[0]["evening_amount_paise"]), (5500, 2750))

    def test_main_screens_load(self):
        for route in ("/", "/providers", "/daily", "/records", "/monthly", "/payments"):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route).status_code, 200)
        backup = self.client.get("/backup")
        self.assertEqual(backup.status_code, 200)
        self.assertTrue(backup.data.startswith(b"SQLite format 3"))
        self.assertEqual(
            self.client.get("/monthly/export.xlsx?year=2026&month=3").status_code,
            200,
        )


if __name__ == "__main__":
    unittest.main()
