"""
Discount Bank (בנק דיסקונט) web automation.

Uses Playwright to log in to the personal online banking portal,
iterate over the configured accounts, and retrieve recent credit
transactions.  Returns a list of BankTransaction objects that the
caller can match against expected customer amounts.

Required environment variables (loaded via python-dotenv):
    DISCOUNT_BANK_ID        – Israeli ID number (ת.ז.)
    DISCOUNT_BANK_PASSWORD  – Online banking password
    DISCOUNT_BANK_ACCOUNTS  – Comma-separated account numbers to scan
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import List, Optional

from playwright.sync_api import Page, sync_playwright

logger = logging.getLogger(__name__)

LOGIN_URL = "https://www.discountbank.co.il/private/login"
PORTAL_URL = "https://www.discountbank.co.il/private/"

# Selectors – update if the bank changes its markup
SEL_ID_INPUT       = "input[name='id'], input[placeholder*='זהות'], input[id*='userId']"
SEL_PASSWORD_INPUT = "input[type='password']"
SEL_LOGIN_BUTTON   = "button[type='submit'], input[type='submit']"
SEL_ACCOUNT_SELECT = "select[name*='account'], select[id*='account']"
SEL_TRANSACTION_ROWS = "table.transactions tbody tr, tr.transaction-row"


@dataclass
class BankTransaction:
    account_number: str
    transaction_date: date
    description: str
    amount: float          # positive = credit (incoming payment)
    reference: str = ""


class DiscountBankClient:
    """Automates Discount Bank personal portal to fetch incoming transactions."""

    def __init__(
        self,
        id_number: Optional[str] = None,
        password: Optional[str] = None,
        accounts: Optional[List[str]] = None,
        lookback_days: int = 35,
        headless: bool = True,
    ):
        self.id_number    = id_number    or os.environ["DISCOUNT_BANK_ID"]
        self.password     = password     or os.environ["DISCOUNT_BANK_PASSWORD"]
        raw_accounts      = accounts or os.environ.get("DISCOUNT_BANK_ACCOUNTS", "")
        self.accounts     = (
            [a.strip() for a in raw_accounts.split(",") if a.strip()]
            if isinstance(raw_accounts, str)
            else list(raw_accounts)
        )
        self.lookback_days = lookback_days
        self.headless      = headless

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_transactions(self) -> List[BankTransaction]:
        """Log in and return credit transactions for all configured accounts."""
        all_txns: List[BankTransaction] = []
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless)
            context = browser.new_context(
                locale="he-IL",
                timezone_id="Asia/Jerusalem",
            )
            page = context.new_page()
            try:
                self._login(page)
                for account in self.accounts:
                    txns = self._fetch_account_transactions(page, account)
                    all_txns.extend(txns)
                    logger.info(
                        "Account %s: fetched %d transactions", account, len(txns)
                    )
            except Exception as exc:
                logger.error("Discount Bank fetch failed: %s", exc)
                raise
            finally:
                context.close()
                browser.close()
        return all_txns

    def find_matching_transaction(
        self,
        transactions: List[BankTransaction],
        expected_amount: float,
        account_number: Optional[str] = None,
        tolerance_pct: float = 5.0,
    ) -> Optional[BankTransaction]:
        """Return the first transaction whose amount is within tolerance.

        Args:
            transactions:   Pool of transactions to search.
            expected_amount: The amount we expect (e.g. from Excel).
            account_number:  If provided, restrict search to this account.
            tolerance_pct:   Acceptable deviation in percent (default 5%).
        """
        lower = expected_amount * (1 - tolerance_pct / 100)
        upper = expected_amount * (1 + tolerance_pct / 100)
        for txn in transactions:
            if account_number and txn.account_number != account_number:
                continue
            if lower <= txn.amount <= upper:
                return txn
        return None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _login(self, page: Page) -> None:
        logger.info("Navigating to Discount Bank login…")
        page.goto(LOGIN_URL, wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(1_500)

        # Fill ID
        page.fill(SEL_ID_INPUT, self.id_number)
        page.wait_for_timeout(500)

        # Fill password
        page.fill(SEL_PASSWORD_INPUT, self.password)
        page.wait_for_timeout(500)

        # Submit
        page.click(SEL_LOGIN_BUTTON)
        page.wait_for_load_state("networkidle", timeout=60_000)

        # Check for error messages
        error_sel = ".error-message, .login-error, [class*='error']"
        if page.locator(error_sel).count() > 0:
            error_text = page.locator(error_sel).first.inner_text()
            raise RuntimeError(f"Discount Bank login failed: {error_text}")

        logger.info("Logged in to Discount Bank successfully.")

    def _fetch_account_transactions(
        self, page: Page, account_number: str
    ) -> List[BankTransaction]:
        """Navigate to account statement and scrape transactions."""
        transactions: List[BankTransaction] = []

        # Navigate to account movements page
        movements_url = (
            f"{PORTAL_URL}account-movements"
            f"?accountNumber={account_number}"
        )
        page.goto(movements_url, wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(2_000)

        # If there's an account selector dropdown, pick the right account
        if page.locator(SEL_ACCOUNT_SELECT).count() > 0:
            page.select_option(SEL_ACCOUNT_SELECT, value=account_number)
            page.wait_for_timeout(1_500)

        # Set date range: today − lookback_days → today
        start_date = date.today() - timedelta(days=self.lookback_days)
        self._set_date_range(page, start_date, date.today())
        page.wait_for_timeout(2_000)

        # Scrape transaction rows
        rows = page.locator(SEL_TRANSACTION_ROWS)
        count = rows.count()
        logger.debug("Account %s: found %d table rows", account_number, count)

        for i in range(count):
            row = rows.nth(i)
            cells = row.locator("td")
            cell_count = cells.count()
            if cell_count < 3:
                continue
            try:
                txn = self._parse_row(cells, account_number)
                if txn and txn.amount > 0:
                    transactions.append(txn)
            except Exception as exc:
                logger.debug("Could not parse row %d: %s", i, exc)

        return transactions

    def _set_date_range(self, page: Page, start: date, end: date) -> None:
        """Try to set date filter inputs if they exist on the page."""
        fmt = "%d/%m/%Y"
        start_str = start.strftime(fmt)
        end_str   = end.strftime(fmt)

        from_sel = "input[name*='from'], input[placeholder*='מתאריך'], input[id*='fromDate']"
        to_sel   = "input[name*='to'],   input[placeholder*='עד תאריך'], input[id*='toDate']"
        search_btn = "button[type='submit'], button:has-text('חפש'), button:has-text('הצג')"

        if page.locator(from_sel).count() > 0:
            page.fill(from_sel, start_str)
        if page.locator(to_sel).count() > 0:
            page.fill(to_sel, end_str)
        if page.locator(search_btn).count() > 0:
            page.click(search_btn)
            page.wait_for_load_state("networkidle", timeout=30_000)

    def _parse_row(self, cells, account_number: str) -> Optional[BankTransaction]:
        """Parse a table row into a BankTransaction.

        Column order varies by bank portal version; we try common layouts.
        """
        texts = [cells.nth(i).inner_text().strip() for i in range(cells.count())]

        # Try to find date, description, amount from cells
        txn_date = None
        description = ""
        amount = 0.0
        reference = ""

        for text in texts:
            # Date pattern DD/MM/YYYY
            m = re.match(r"(\d{2})[/\-\.](\d{2})[/\-\.](\d{4})", text)
            if m and not txn_date:
                try:
                    txn_date = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                except ValueError:
                    pass
                continue

            # Amount: number with optional commas and minus sign
            m = re.match(r"^-?[\d,]+\.?\d*$", text.replace("\u200e", "").replace("\xa0", ""))
            if m:
                try:
                    val = float(text.replace(",", "").replace("\u200e", ""))
                    if val > 0 and amount == 0:
                        amount = val
                    elif val < 0 and amount == 0:
                        amount = val
                except ValueError:
                    pass
                continue

            if len(text) > 3 and not description:
                description = text

        if not txn_date:
            txn_date = date.today()

        return BankTransaction(
            account_number=account_number,
            transaction_date=txn_date,
            description=description,
            amount=amount,
            reference=reference,
        )
