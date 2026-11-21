# milktest-1

## Milk Management System

A local Flask and SQLite application for managing milk providers, recording decimal-litre deliveries, generating monthly provider reports, and tracking payments.

## Features

- Provider directory with unique IDs, optional phone number, editable custom rate, and up to 20 active providers.
- Searchable provider list on the dashboard. Selecting a provider opens its own profile with only its deliveries and payments.
- Providers can be archived/restored. Archiving prevents new entries but preserves all historical deliveries and payments.
- One morning/evening record per provider and date. Saving an existing date updates it; database constraints prevent duplicates.
- Milk quantities in litres with 0.001 L (1 ml) precision, including zero quantities.
- Live daily total quantity and estimated amount on the entry form.
- Fixed quantity prices: 1 L = ₹55, 500 ml = ₹33, and 250 ml = ₹16. The previously requested 750 ml pack is also available at ₹41.50.
- Composite quantities use complete pack prices first; any remainder is priced proportionally using that provider's configured rate per litre. New providers default to ₹55/L for custom remainders.
- Daily-record search by provider and/or date; edit or delete a complete provider-day record.
- Monthly provider summaries with morning/evening totals, payable, paid, balance, and payment status.
- Provider profile month/year filter, daily breakdown, monthly totals, payment status, provider-only CSV/Excel exports, and printing.
- Payment entries allocated to a provider and billing month; supports partial payments, notes, and removal of incorrect payment entries.
- CSV/Excel report downloads, browser printing, and a consistent SQLite database backup.
- Responsive interface for desktop and mobile.

## Requirements

- Python 3.10 or newer
- pip

Dependencies are listed in `requirements.txt`: Flask and openpyxl (used for Excel export). SQLite is included with Python.

## Run from VS Code on Windows

Open this project folder in VS Code, open its integrated terminal, and run:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Then visit <http://127.0.0.1:5000>. Keep the terminal open while using the app. Press `Ctrl+C` to stop the server.

If PowerShell prevents environment activation, install dependencies with `py -m pip install -r requirements.txt` and start with `py app.py` instead.

The database file `milk_records.db` is created in the project folder. Keep it to preserve records. Use **Download database backup** on the dashboard for a SQLite backup.

When opening a database from the previous single-provider version, the app migrates existing records under a provider named **Legacy provider (edit this name)**. Existing quantities, dates, and saved amounts are retained.

For a stable secret key in a longer-running deployment, set `MILK_APP_SECRET` in the environment. This app is designed for local personal use; do not expose its development server directly to the internet.

## Using the app

1. Open **Providers** to add names, optional phone numbers, and custom remainder rates. The active-provider limit is 20.
2. Select **Add milk**, choose provider and date, and enter morning/evening litres. Both values can be zero. Saving again edits the same provider/date record.
3. Use **Daily records** to search and correct a saved record.
4. Search or select a name on the dashboard to open that provider's individual profile. Choose its month and year to see only that provider's daily records and monthly totals.
5. Open **Monthly reports** for an all-provider summary, or use a provider profile to print/download that provider's report separately.
6. In **Payments**, select a provider, the billing month, payment date, amount, and optional note. The report updates the paid amount and remaining balance.
7. To retain history but stop a provider from appearing for new deliveries, archive the provider. Restore it later from the provider list.

## Tests

```powershell
python -m unittest discover -s tests -v
```
