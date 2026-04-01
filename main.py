#!/usr/bin/env python3
"""
Rental System – main entry point.

Usage:
    python main.py                         # run once for current month
    python main.py --dry-run               # simulate without issuing anything
    python main.py --billing-month 2026-03 # specify billing month
    python main.py --excel data/custom.xlsx
    python main.py --schedule              # run monthly (1st of each month)

Environment:
    Copy .env.example → .env and fill in credentials before running.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime

import schedule
import time

from dotenv import load_dotenv

# Load .env before importing modules that read env vars
load_dotenv()

from src.bank.discount_bank import DiscountBankClient
from src.cpi.cpi_client import CPIClient
from src.email_service.email_sender import EmailService
from src.excel_parser import load_customers
from src.invoice.invoice4u import Invoice4uClient
from src.processor import RentalProcessor

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s – %(message)s"


def setup_logging(level: str = "INFO") -> None:
    os.makedirs("logs", exist_ok=True)
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            f"logs/rental_system_{date.today().strftime('%Y%m')}.log",
            encoding="utf-8",
        ),
    ]
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format=LOG_FORMAT, handlers=handlers)


# ---------------------------------------------------------------------------
# Core run function
# ---------------------------------------------------------------------------

def run(
    excel_path: str,
    billing_month: date,
    dry_run: bool = False,
    renewal_alert_days: int = 30,
    tolerance_pct: float = 5.0,
) -> None:
    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("Rental System run started")
    logger.info("Excel: %s | Billing month: %s | Dry run: %s",
                excel_path, billing_month.strftime("%Y-%m"), dry_run)
    logger.info("=" * 60)

    # 1. Load customers
    customers = load_customers(excel_path)
    if not customers:
        logger.warning("No customers loaded. Exiting.")
        return

    # 2. Build service clients
    bank_client    = DiscountBankClient()
    invoice_client = Invoice4uClient()
    cpi_client     = CPIClient()
    email_service  = EmailService()

    # 3. Create processor and run
    processor = RentalProcessor(
        bank_client=bank_client,
        invoice_client=invoice_client,
        cpi_client=cpi_client,
        email_service=email_service,
        renewal_alert_days=renewal_alert_days,
        amount_tolerance_pct=tolerance_pct,
        billing_month=billing_month,
        dry_run=dry_run,
    )

    results = processor.process_all(customers)

    success_count = sum(1 for r in results if r.success)
    logger.info(
        "Run complete: %d/%d customers processed successfully.",
        success_count, len(results),
    )


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

def schedule_monthly(run_kwargs: dict) -> None:
    """Run the workflow on the 1st of every month."""
    logger = logging.getLogger(__name__)

    def job():
        billing_month = date.today().replace(day=1)
        run(**{**run_kwargs, "billing_month": billing_month})

    schedule.every().month.do(job)
    logger.info("Scheduler started – will run on the 1st of each month.")

    # Also run immediately on startup
    job()

    while True:
        schedule.run_pending()
        time.sleep(3600)   # check every hour


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rental System – automated customer invoicing"
    )
    parser.add_argument(
        "--excel",
        default=os.environ.get("CUSTOMERS_EXCEL_PATH", "data/customers.xlsx"),
        help="Path to customers Excel file (default: data/customers.xlsx)",
    )
    parser.add_argument(
        "--billing-month",
        default=None,
        metavar="YYYY-MM",
        help="Billing month in YYYY-MM format (default: current month)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate without issuing invoices or sending emails",
    )
    parser.add_argument(
        "--renewal-alert-days",
        type=int,
        default=int(os.environ.get("RENEWAL_ALERT_DAYS", 30)),
        help="Days before contract end to send renewal alert (default: 30)",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=float(os.environ.get("AMOUNT_TOLERANCE_PCT", 5.0)),
        help="Amount tolerance in percent (default: 5.0)",
    )
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="Run continuously, executing on the 1st of each month",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)

    # Determine billing month
    if args.billing_month:
        try:
            billing_month = datetime.strptime(args.billing_month, "%Y-%m").date()
        except ValueError:
            print(f"ERROR: Invalid billing-month format '{args.billing_month}'. Use YYYY-MM.")
            sys.exit(1)
    else:
        billing_month = date.today().replace(day=1)

    run_kwargs = dict(
        excel_path=args.excel,
        billing_month=billing_month,
        dry_run=args.dry_run,
        renewal_alert_days=args.renewal_alert_days,
        tolerance_pct=args.tolerance,
    )

    if args.schedule:
        schedule_monthly(run_kwargs)
    else:
        run(**run_kwargs)


if __name__ == "__main__":
    main()
