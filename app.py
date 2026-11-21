"""Milk provider management, daily records, reports, and payments."""

from __future__ import annotations

import csv
import io
import os
import secrets
import sqlite3
from calendar import month_name
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from flask import (
    Flask,
    abort,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from database import (
    count_active_providers,
    delete_payment,
    delete_record,
    get_month_payments,
    get_month_records,
    get_provider,
    get_payment,
    get_record_for_date,
    get_records,
    initialize_database,
    list_providers,
    save_payment,
    save_provider,
    save_record,
    set_provider_active,
)

PRESET_PRICES_PAISE = {1000: 5500, 750: 4150, 500: 3300, 250: 1600}
BASE_RATE_PAISE_PER_LITRE = 5500
MAX_QUANTITY_ML = 100_000
MAX_ACTIVE_PROVIDERS = 20


def calculate_amount_paise(quantity_ml: int, rate_paise_per_litre: int = BASE_RATE_PAISE_PER_LITRE) -> int:
    """Price complete preset pack sizes exactly and prorate any leftover quantity."""
    if quantity_ml < 0:
        raise ValueError("Milk quantity cannot be negative.")
    amount_paise = 0
    remaining_ml = quantity_ml
    for pack_ml in (1000, 750, 500, 250):
        pack_count, remaining_ml = divmod(remaining_ml, pack_ml)
        amount_paise += pack_count * PRESET_PRICES_PAISE[pack_ml]
    if remaining_ml:
        amount_paise += int(
            (Decimal(rate_paise_per_litre) * Decimal(remaining_ml) / Decimal(1000)).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
    return amount_paise


def _parse_litres(raw_value: str, session: str) -> int:
    try:
        litres = Decimal(raw_value.strip())
    except (InvalidOperation, AttributeError):
        raise ValueError("{} milk quantity must be a valid number of litres.".format(session.title())) from None
    if not litres.is_finite() or litres < 0:
        raise ValueError("{} milk quantity cannot be negative.".format(session.title()))
    if litres > Decimal(MAX_QUANTITY_ML) / Decimal(1000):
        raise ValueError("Milk quantity cannot exceed 100 litres per session.")
    millilitres = litres * 1000
    if millilitres != millilitres.to_integral_value():
        raise ValueError("{} quantity must be in increments of 0.001 litres.".format(session.title()))
    quantity_ml = int(millilitres)
    return quantity_ml


def _parse_money_paise(raw_value: str, label: str, allow_zero: bool = True) -> int:
    try:
        amount = Decimal(raw_value.strip())
    except (InvalidOperation, AttributeError):
        raise ValueError("{} must be a valid amount.".format(label)) from None
    if not amount.is_finite() or amount < 0 or (not allow_zero and amount == 0):
        raise ValueError("{} must be greater than zero.".format(label) if not allow_zero else "{} cannot be negative.".format(label))
    paise = amount * 100
    if paise != paise.to_integral_value():
        raise ValueError("{} can have at most two decimal places.".format(label))
    return int(paise)


def format_money(amount_paise: int) -> str:
    rupees = Decimal(amount_paise) / Decimal(100)
    return "₹{:,.2f}".format(rupees).rstrip("0").rstrip(".")


def format_quantity(quantity_ml: int) -> str:
    litres = (Decimal(quantity_ml) / Decimal(1000)).normalize()
    return "{} L".format(format(litres, "f"))


def _month_and_year(args: Any) -> tuple[int, int]:
    today = date.today()
    try:
        month = int(args.get("month", today.month))
        year = int(args.get("year", today.year))
        date(year, month, 1)
    except (TypeError, ValueError):
        raise ValueError("Select a valid month and year.") from None
    return year, month


def _daily_summary(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for record in records:
        record["total_ml"] = record["morning_ml"] + record["evening_ml"]
        record["total_amount_paise"] = (
            record["morning_amount_paise"] + record["evening_amount_paise"]
        )
    return records


def _provider_month_summary(
    database_path: str,
    providers: list[dict[str, Any]],
    year: int,
    month: int,
) -> list[dict[str, Any]]:
    summaries = []
    for provider in providers:
        records = get_month_records(database_path, year, month, provider["id"])
        payments = get_month_payments(database_path, year, month, provider["id"])
        morning_ml = sum(record["morning_ml"] for record in records)
        evening_ml = sum(record["evening_ml"] for record in records)
        payable = sum(
            record["morning_amount_paise"] + record["evening_amount_paise"]
            for record in records
        )
        paid = sum(payment["amount_paise"] for payment in payments)
        summaries.append(
            {
                **provider,
                "morning_ml": morning_ml,
                "evening_ml": evening_ml,
                "total_ml": morning_ml + evening_ml,
                "payable_paise": payable,
                "paid_paise": paid,
                "balance_paise": payable - paid,
                "status": "Paid" if payable and paid >= payable else "Partially paid" if paid > 0 else "Pending",
            }
        )
    return summaries


def create_app(database_path: str | Path | None = None) -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("MILK_APP_SECRET") or secrets.token_hex(32)
    app.config["DATABASE"] = str(database_path or Path(__file__).with_name("milk_records.db"))
    initialize_database(app.config["DATABASE"])
    app.jinja_env.filters["money"] = format_money
    app.jinja_env.filters["quantity"] = format_quantity

    @app.get("/")
    def index() -> str:
        today = date.today().isoformat()
        year, month = date.today().year, date.today().month
        providers = list_providers(app.config["DATABASE"], include_inactive=True)
        today_records = _daily_summary(get_records(app.config["DATABASE"], record_date=today))
        month_records = _daily_summary(get_month_records(app.config["DATABASE"], year, month))
        return render_template(
            "index.html",
            today=today,
            providers=providers,
            provider_count=count_active_providers(app.config["DATABASE"]),
            today_morning_ml=sum(record["morning_ml"] for record in today_records),
            today_evening_ml=sum(record["evening_ml"] for record in today_records),
            today_total_ml=sum(record["total_ml"] for record in today_records),
            monthly_ml=sum(record["total_ml"] for record in month_records),
            monthly_amount=sum(record["total_amount_paise"] for record in month_records),
        )

    @app.get("/providers/<int:provider_id>")
    def provider_detail(provider_id: int) -> Any:
        provider = get_provider(app.config["DATABASE"], provider_id)
        if not provider:
            abort(404)
        try:
            year, month = _month_and_year(request.args)
        except ValueError as exc:
            flash(str(exc), "error")
            year, month = date.today().year, date.today().month
        summary = _provider_month_summary(
            app.config["DATABASE"], [provider], year, month
        )[0]
        rows = _daily_summary(
            get_month_records(app.config["DATABASE"], year, month, provider_id)
        )
        return render_template(
            "provider_detail.html",
            provider=provider,
            summary=summary,
            rows=rows,
            year=year,
            month=month,
            month_title=month_name[month],
            months=list(month_name)[1:],
        )

    @app.route("/providers", methods=["GET", "POST"])
    def providers_page() -> Any:
        if request.method == "POST":
            action = request.form.get("action", "add")
            try:
                if action in ("add", "edit"):
                    name = (request.form.get("name") or "").strip()
                    phone = (request.form.get("phone") or "").strip()
                    if not name:
                        raise ValueError("Provider name is required.")
                    if len(name) > 100 or len(phone) > 30:
                        raise ValueError("Name must be at most 100 characters and phone at most 30.")
                    provider_id = int(request.form["provider_id"]) if action == "edit" else None
                    if provider_id and not get_provider(app.config["DATABASE"], provider_id):
                        abort(404)
                    if action == "add" and count_active_providers(app.config["DATABASE"]) >= MAX_ACTIVE_PROVIDERS:
                        raise ValueError("You can have up to 20 active providers.")
                    rate_paise = _parse_money_paise(
                        request.form.get("rate_per_litre", "55"), "Rate per litre", allow_zero=False
                    )
                    save_provider(app.config["DATABASE"], name, phone, rate_paise, provider_id)
                    flash("Provider information saved.", "success")
                elif action in ("archive", "restore"):
                    provider_id = int(request.form["provider_id"])
                    if not get_provider(app.config["DATABASE"], provider_id):
                        abort(404)
                    if action == "restore" and count_active_providers(app.config["DATABASE"]) >= MAX_ACTIVE_PROVIDERS:
                        raise ValueError("There are already 20 active providers.")
                    set_provider_active(app.config["DATABASE"], provider_id, action == "restore")
                    flash(
                        "Provider archived. Historical records and payments were preserved."
                        if action == "archive"
                        else "Provider restored.",
                        "success",
                    )
                else:
                    abort(400)
            except ValueError as exc:
                flash(str(exc), "error")
            except sqlite3.IntegrityError:
                flash("A provider with that name already exists.", "error")
            return redirect(url_for("providers_page"))
        editing_id = request.args.get("edit", type=int)
        return render_template(
            "providers.html",
            providers=list_providers(app.config["DATABASE"], include_inactive=True),
            editing=get_provider(app.config["DATABASE"], editing_id) if editing_id else None,
            active_count=count_active_providers(app.config["DATABASE"]),
            provider_limit=MAX_ACTIVE_PROVIDERS,
        )

    @app.route("/daily", methods=["GET", "POST"])
    def daily_entry() -> Any:
        providers = list_providers(app.config["DATABASE"])
        selected_date = request.values.get("date", date.today().isoformat())
        try:
            selected_date = date.fromisoformat(selected_date).isoformat()
        except (TypeError, ValueError):
            flash("Enter a valid delivery date.", "error")
            selected_date = date.today().isoformat()

        selected_provider_id = request.values.get("provider_id", type=int)
        if request.method == "POST":
            try:
                selected_provider_id = int(request.form["provider_id"])
                provider = get_provider(app.config["DATABASE"], selected_provider_id)
                if not provider:
                    raise ValueError("Choose a registered milk provider.")
                morning_ml = _parse_litres(request.form.get("morning_litres", ""), "Morning")
                evening_ml = _parse_litres(request.form.get("evening_litres", ""), "Evening")
                parsed_date = date.fromisoformat(request.form.get("date", "")).isoformat()
                existing_record = get_record_for_date(
                    app.config["DATABASE"], selected_provider_id, parsed_date
                )
                if not provider["is_active"] and not existing_record:
                    raise ValueError("Archived providers cannot receive new milk records.")
                save_record(
                    app.config["DATABASE"],
                    selected_provider_id,
                    parsed_date,
                    morning_ml,
                    evening_ml,
                    provider["rate_paise_per_litre"],
                    calculate_amount_paise(morning_ml, provider["rate_paise_per_litre"]),
                    calculate_amount_paise(evening_ml, provider["rate_paise_per_litre"]),
                )
                flash("Milk record saved.", "success")
                return redirect(url_for("daily_entry", provider_id=selected_provider_id, date=parsed_date))
            except (ValueError, KeyError) as exc:
                flash(str(exc) or "Enter a valid date.", "error")

        if selected_provider_id is None and providers:
            selected_provider_id = providers[0]["id"]
        selected_provider = get_provider(app.config["DATABASE"], selected_provider_id) if selected_provider_id else None
        if selected_provider and not selected_provider["is_active"]:
            providers.append(selected_provider)
        record = (
            get_record_for_date(app.config["DATABASE"], selected_provider_id, selected_date)
            if selected_provider_id
            else None
        )
        return render_template(
            "daily_entry.html",
            providers=providers,
            selected_provider=selected_provider,
            selected_provider_id=selected_provider_id,
            selected_date=selected_date,
            record=record,
        )

    @app.post("/records/<int:provider_id>/<record_date>/delete")
    def remove_record(provider_id: int, record_date: str) -> Any:
        try:
            parsed_date = date.fromisoformat(record_date).isoformat()
        except ValueError:
            abort(400, description="Invalid record date.")
        delete_record(app.config["DATABASE"], provider_id, parsed_date)
        flash("Milk record deleted.", "success")
        return redirect(url_for("records_page"))

    @app.get("/records")
    def records_page() -> str:
        provider_id = request.args.get("provider_id", type=int)
        record_date = request.args.get("date", "").strip() or None
        if record_date:
            try:
                record_date = date.fromisoformat(record_date).isoformat()
            except ValueError:
                flash("Choose a valid date to search.", "error")
                record_date = None
        return render_template(
            "records.html",
            providers=list_providers(app.config["DATABASE"], include_inactive=True),
            selected_provider_id=provider_id,
            selected_date=record_date or "",
            records=_daily_summary(get_records(app.config["DATABASE"], provider_id, record_date)),
        )

    @app.get("/monthly")
    def monthly_report() -> Any:
        try:
            year, month = _month_and_year(request.args)
        except ValueError as exc:
            flash(str(exc), "error")
            year, month = date.today().year, date.today().month
        active_providers = list_providers(app.config["DATABASE"])
        all_providers = list_providers(app.config["DATABASE"], include_inactive=True)
        selected_provider_id = request.args.get("provider_id", type=int)
        if selected_provider_id is None and active_providers:
            selected_provider_id = active_providers[0]["id"]
        selected_provider = get_provider(app.config["DATABASE"], selected_provider_id) if selected_provider_id else None
        summaries = _provider_month_summary(app.config["DATABASE"], all_providers, year, month)
        rows = _daily_summary(
            get_month_records(app.config["DATABASE"], year, month, selected_provider_id)
        ) if selected_provider_id else []
        total_ml = sum(row["total_ml"] for row in rows)
        total_amount = sum(row["total_amount_paise"] for row in rows)
        paid = sum(
            payment["amount_paise"]
            for payment in get_month_payments(app.config["DATABASE"], year, month, selected_provider_id)
        ) if selected_provider_id else 0
        return render_template(
            "monthly_report.html",
            rows=rows,
            summaries=summaries,
            providers=all_providers,
            selected_provider=selected_provider,
            selected_provider_id=selected_provider_id,
            year=year,
            month=month,
            month_title=month_name[month],
            months=list(month_name)[1:],
            total_ml=total_ml,
            total_amount=total_amount,
            paid=paid,
            balance=total_amount - paid,
        )

    @app.route("/payments", methods=["GET", "POST"])
    def payments_page() -> Any:
        providers = list_providers(app.config["DATABASE"], include_inactive=True)
        try:
            year, month = _month_and_year(request.values)
        except ValueError as exc:
            flash(str(exc), "error")
            year, month = date.today().year, date.today().month
        provider_id = request.values.get("provider_id", type=int)
        if provider_id is None and providers:
            provider_id = next(
                (provider["id"] for provider in providers if provider["is_active"]),
                providers[0]["id"],
            )
        if request.method == "POST":
            try:
                provider_id = int(request.form["provider_id"])
                provider = get_provider(app.config["DATABASE"], provider_id)
                if not provider:
                    raise ValueError("Choose a registered provider.")
                year = int(request.form["year"])
                month = int(request.form["month"])
                date(year, month, 1)
                payment_date = date.fromisoformat(request.form["payment_date"]).isoformat()
                amount_paise = _parse_money_paise(request.form.get("amount", ""), "Payment", allow_zero=False)
                note = (request.form.get("note") or "").strip()
                if len(note) > 250:
                    raise ValueError("Payment note must be at most 250 characters.")
                save_payment(
                    app.config["DATABASE"], provider_id, year, month, payment_date, amount_paise, note
                )
                flash("Payment recorded.", "success")
                return redirect(
                    url_for("payments_page", provider_id=provider_id, year=year, month=month)
                )
            except (ValueError, KeyError) as exc:
                flash(str(exc) or "Enter valid payment details.", "error")
        selected_provider = get_provider(app.config["DATABASE"], provider_id) if provider_id else None
        payments = (
            get_month_payments(app.config["DATABASE"], year, month, provider_id)
            if provider_id else []
        )
        records = get_month_records(app.config["DATABASE"], year, month, provider_id) if provider_id else []
        payable = sum(
            record["morning_amount_paise"] + record["evening_amount_paise"] for record in records
        )
        paid = sum(payment["amount_paise"] for payment in payments)
        return render_template(
            "payments.html",
            providers=providers,
            selected_provider=selected_provider,
            selected_provider_id=provider_id,
            year=year,
            month=month,
            today=date.today().isoformat(),
            months=list(month_name)[1:],
            payments=payments,
            payable=payable,
            paid=paid,
            balance=payable - paid,
        )

    @app.post("/payments/<int:payment_id>/delete")
    def remove_payment(payment_id: int) -> Any:
        payment = get_payment(app.config["DATABASE"], payment_id)
        delete_payment(app.config["DATABASE"], payment_id)
        flash("Payment entry deleted.", "success")
        if payment:
            return redirect(
                url_for(
                    "payments_page",
                    provider_id=payment["provider_id"],
                    year=payment["billing_year"],
                    month=payment["billing_month"],
                )
            )
        return redirect(url_for("payments_page"))

    @app.get("/monthly/export.csv")
    def export_csv() -> Any:
        try:
            year, month = _month_and_year(request.args)
        except ValueError:
            abort(400, description="Select a valid month and year.")
        provider_id = request.args.get("provider_id", type=int)
        if provider_id is not None and not get_provider(app.config["DATABASE"], provider_id):
            abort(404)
        rows = _daily_summary(get_month_records(app.config["DATABASE"], year, month, provider_id))
        providers = (
            [get_provider(app.config["DATABASE"], provider_id)]
            if provider_id is not None
            else list_providers(app.config["DATABASE"], include_inactive=True)
        )
        summaries = _provider_month_summary(app.config["DATABASE"], providers, year, month)
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(
            ["Provider", "Date", "Morning milk (L)", "Evening milk (L)", "Total milk (L)", "Daily amount (INR)"]
        )
        for row in rows:
            writer.writerow(
                [
                    row["provider_name"],
                    row["record_date"],
                    "{:.3f}".format(row["morning_ml"] / 1000),
                    "{:.3f}".format(row["evening_ml"] / 1000),
                    "{:.3f}".format(row["total_ml"] / 1000),
                    "{:.2f}".format(row["total_amount_paise"] / 100),
                ]
            )
        writer.writerow([])
        writer.writerow(["Monthly provider summary"])
        writer.writerow(
            ["Provider", "Month", "Morning total (L)", "Evening total (L)", "Total milk (L)", "Amount payable (INR)", "Paid (INR)", "Balance (INR)", "Status"]
        )
        for summary in summaries:
            writer.writerow(
                [
                    summary["name"],
                    "{} {}".format(month_name[month], year),
                    "{:.3f}".format(summary["morning_ml"] / 1000),
                    "{:.3f}".format(summary["evening_ml"] / 1000),
                    "{:.3f}".format(summary["total_ml"] / 1000),
                    "{:.2f}".format(summary["payable_paise"] / 100),
                    "{:.2f}".format(summary["paid_paise"] / 100),
                    "{:.2f}".format(summary["balance_paise"] / 100),
                    summary["status"],
                ]
            )
        response = make_response("\ufeff" + output.getvalue())
        response.headers["Content-Type"] = "text/csv; charset=utf-8"
        filename = "milk-report-provider-{}-{:04d}-{:02d}.csv".format(provider_id, year, month) if provider_id else "milk-report-{:04d}-{:02d}.csv".format(year, month)
        response.headers["Content-Disposition"] = "attachment; filename=" + filename
        return response

    @app.get("/monthly/export.xlsx")
    def export_xlsx() -> Any:
        try:
            year, month = _month_and_year(request.args)
        except ValueError:
            abort(400, description="Select a valid month and year.")
        try:
            from openpyxl import Workbook
        except ImportError as exc:
            raise RuntimeError("Excel export requires openpyxl. Install requirements.txt.") from exc
        provider_id = request.args.get("provider_id", type=int)
        if provider_id is not None and not get_provider(app.config["DATABASE"], provider_id):
            abort(404)
        rows = _daily_summary(get_month_records(app.config["DATABASE"], year, month, provider_id))
        providers = (
            [get_provider(app.config["DATABASE"], provider_id)]
            if provider_id is not None
            else list_providers(app.config["DATABASE"], include_inactive=True)
        )
        summaries = _provider_month_summary(app.config["DATABASE"], providers, year, month)
        workbook = Workbook()
        summary_sheet = workbook.active
        summary_sheet.title = "Monthly summary"
        summary_sheet.append(
            ["Provider", "Month", "Morning total (L)", "Evening total (L)", "Total milk (L)", "Amount payable (INR)", "Paid (INR)", "Balance (INR)", "Status"]
        )
        for summary in summaries:
            summary_sheet.append(
                [
                    summary["name"],
                    "{} {}".format(month_name[month], year),
                    summary["morning_ml"] / 1000,
                    summary["evening_ml"] / 1000,
                    summary["total_ml"] / 1000,
                    summary["payable_paise"] / 100,
                    summary["paid_paise"] / 100,
                    summary["balance_paise"] / 100,
                    summary["status"],
                ]
            )
        sheet = workbook.create_sheet("Daily records")
        sheet.append(["Provider", "Date", "Morning milk (L)", "Evening milk (L)", "Total milk (L)", "Daily amount (INR)"])
        for row in rows:
            sheet.append(
                [
                    row["provider_name"],
                    row["record_date"],
                    row["morning_ml"] / 1000,
                    row["evening_ml"] / 1000,
                    row["total_ml"] / 1000,
                    row["total_amount_paise"] / 100,
                ]
            )
        sheet.freeze_panes = "A2"
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = max(
                len(str(cell.value or "")) for cell in column
            ) + 2
        file_buffer = io.BytesIO()
        workbook.save(file_buffer)
        file_buffer.seek(0)
        filename = "milk-report-provider-{}-{:04d}-{:02d}.xlsx".format(provider_id, year, month) if provider_id else "milk-report-{:04d}-{:02d}.xlsx".format(year, month)
        return send_file(
            file_buffer,
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    @app.get("/backup")
    def download_backup() -> Any:
        buffer = io.BytesIO()
        temporary_path = Path(app.config["DATABASE"] + ".backup")
        try:
            source = sqlite3.connect(app.config["DATABASE"])
            destination = sqlite3.connect(temporary_path)
            try:
                source.backup(destination)
            finally:
                destination.close()
                source.close()
            with temporary_path.open("rb") as backup_file:
                buffer.write(backup_file.read())
        finally:
            temporary_path.unlink(missing_ok=True)
        buffer.seek(0)
        return send_file(
            buffer,
            as_attachment=True,
            download_name="milk-records-backup.db",
            mimetype="application/octet-stream",
        )

    @app.errorhandler(sqlite3.Error)
    def handle_database_error(error: sqlite3.Error) -> tuple[str, int]:
        app.logger.exception("Database operation failed", exc_info=error)
        return render_template(
            "error.html", message="A database error occurred. Your record was not confirmed as saved."
        ), 500

    @app.errorhandler(500)
    def handle_server_error(error: Any) -> tuple[str, int]:
        app.logger.error("Request failed: %s", error)
        return render_template("error.html", message="Something went wrong while processing the request."), 500

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
