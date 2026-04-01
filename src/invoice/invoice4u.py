"""
Invoice4u (invoice4u.co.il) web automation.

Automates logging in and issuing:
  1. A tax invoice (חשבונית מס) for the exact received amount.
  2. A payment request (דרישת תשלום / הצעת מחיר) for the CPI-indexed amount.

Required environment variables:
    INVOICE4U_EMAIL       – login email
    INVOICE4U_PASSWORD    – login password
    INVOICE4U_BUSINESS_ID – business ID shown in the portal URL (optional)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date
from typing import Optional

from playwright.sync_api import Page, sync_playwright

logger = logging.getLogger(__name__)

LOGIN_URL     = "https://app.invoice4u.co.il/login"
DASHBOARD_URL = "https://app.invoice4u.co.il/dashboard"
NEW_INVOICE_URL = "https://app.invoice4u.co.il/documents/new?type=invoice"
NEW_PAYMENT_REQUEST_URL = "https://app.invoice4u.co.il/documents/new?type=quote"

# Selectors (may need updating if Invoice4u changes its markup)
SEL_EMAIL    = "input[type='email'], input[name='email'], input[placeholder*='מייל']"
SEL_PASSWORD = "input[type='password']"
SEL_LOGIN_BTN = "button[type='submit']"

SEL_CLIENT_NAME    = "input[name*='client'], input[placeholder*='לקוח'], input[placeholder*='שם']"
SEL_ITEM_DESC      = "textarea[name*='description'], input[name*='description'], input[placeholder*='תיאור']"
SEL_ITEM_AMOUNT    = "input[name*='price'], input[name*='amount'], input[placeholder*='מחיר']"
SEL_ITEM_QTY       = "input[name*='quantity'], input[name*='qty']"
SEL_NOTES          = "textarea[name*='notes'], textarea[name*='remarks'], input[name*='notes']"
SEL_SAVE_BTN       = "button:has-text('שמור'), button:has-text('הנפק'), button[type='submit']"
SEL_CONFIRM_BTN    = "button:has-text('אישור'), button:has-text('כן')"


@dataclass
class InvoiceResult:
    document_type: str        # "invoice" | "payment_request"
    document_number: str
    customer_name: str
    amount: float
    issue_date: date
    note: str
    pdf_url: Optional[str] = None


class Invoice4uClient:
    """Issues invoices and payment requests via Invoice4u."""

    def __init__(
        self,
        email: Optional[str] = None,
        password: Optional[str] = None,
        headless: bool = True,
    ):
        self.email    = email    or os.environ["INVOICE4U_EMAIL"]
        self.password = password or os.environ["INVOICE4U_PASSWORD"]
        self.headless = headless

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def issue_invoice(
        self,
        customer_name: str,
        amount: float,
        billing_month: date,
        description: str = "",
        extra_note: str = "",
    ) -> InvoiceResult:
        """Issue a tax invoice (חשבונית מס קבלה) and return its details.

        Args:
            customer_name: Customer name as it appears in Invoice4u.
            amount:        Exact invoice amount (already ownership-adjusted).
            billing_month: The month/year the invoice covers.
            description:   Line-item description (defaults to month name).
            extra_note:    Additional note appended to the invoice remarks.
        """
        note = self._build_note(billing_month, extra_note)
        if not description:
            description = self._month_label(billing_month)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless)
            page = browser.new_context(locale="he-IL").new_page()
            try:
                self._login(page)
                result = self._create_document(
                    page,
                    doc_type="invoice",
                    url=NEW_INVOICE_URL,
                    customer_name=customer_name,
                    amount=amount,
                    description=description,
                    note=note,
                )
            finally:
                browser.close()
        return result

    def issue_payment_request(
        self,
        customer_name: str,
        indexed_amount: float,
        billing_month: date,
        description: str = "",
        extra_note: str = "",
    ) -> InvoiceResult:
        """Issue a payment request (דרישת תשלום) for the CPI-indexed amount.

        Args:
            customer_name:  Customer name.
            indexed_amount: CPI-adjusted amount.
            billing_month:  The billing month.
            description:    Line-item description.
            extra_note:     Additional remarks.
        """
        note = self._build_note(billing_month, extra_note)
        if not description:
            description = f"דרישת תשלום – {self._month_label(billing_month)}"

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless)
            page = browser.new_context(locale="he-IL").new_page()
            try:
                self._login(page)
                result = self._create_document(
                    page,
                    doc_type="payment_request",
                    url=NEW_PAYMENT_REQUEST_URL,
                    customer_name=customer_name,
                    amount=indexed_amount,
                    description=description,
                    note=note,
                )
            finally:
                browser.close()
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _login(self, page: Page) -> None:
        logger.info("Logging in to Invoice4u…")
        page.goto(LOGIN_URL, wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(1_000)
        page.fill(SEL_EMAIL, self.email)
        page.fill(SEL_PASSWORD, self.password)
        page.click(SEL_LOGIN_BTN)
        page.wait_for_load_state("networkidle", timeout=60_000)

        if "/login" in page.url:
            raise RuntimeError("Invoice4u login failed – still on login page.")
        logger.info("Logged in to Invoice4u.")

    def _create_document(
        self,
        page: Page,
        doc_type: str,
        url: str,
        customer_name: str,
        amount: float,
        description: str,
        note: str,
    ) -> InvoiceResult:
        logger.info("Creating %s for '%s', amount=%.2f", doc_type, customer_name, amount)
        page.goto(url, wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(1_500)

        # Fill client name
        if page.locator(SEL_CLIENT_NAME).count() > 0:
            page.fill(SEL_CLIENT_NAME, customer_name)
            page.wait_for_timeout(500)
            # Select from autocomplete if visible
            autocomplete = page.locator(
                f"li:has-text('{customer_name}'), div[role='option']:has-text('{customer_name}')"
            )
            if autocomplete.count() > 0:
                autocomplete.first.click()
                page.wait_for_timeout(400)

        # Fill line item description
        if page.locator(SEL_ITEM_DESC).count() > 0:
            page.fill(SEL_ITEM_DESC, description)

        # Fill quantity = 1
        if page.locator(SEL_ITEM_QTY).count() > 0:
            page.fill(SEL_ITEM_QTY, "1")

        # Fill price / amount
        amount_str = f"{amount:.2f}"
        if page.locator(SEL_ITEM_AMOUNT).count() > 0:
            page.fill(SEL_ITEM_AMOUNT, amount_str)

        # Fill notes
        if page.locator(SEL_NOTES).count() > 0:
            page.fill(SEL_NOTES, note)

        # Save / issue
        page.click(SEL_SAVE_BTN)
        page.wait_for_timeout(2_000)

        # Confirm dialog if any
        if page.locator(SEL_CONFIRM_BTN).count() > 0:
            page.click(SEL_CONFIRM_BTN)
            page.wait_for_load_state("networkidle", timeout=30_000)

        # Extract document number from URL or page
        doc_number = self._extract_doc_number(page)

        # Try to get PDF link
        pdf_url = None
        pdf_link = page.locator("a[href*='.pdf'], a:has-text('הורד'), a:has-text('PDF')")
        if pdf_link.count() > 0:
            pdf_url = pdf_link.first.get_attribute("href")

        logger.info("Created %s #%s for %s", doc_type, doc_number, customer_name)
        return InvoiceResult(
            document_type=doc_type,
            document_number=doc_number,
            customer_name=customer_name,
            amount=amount,
            issue_date=date.today(),
            note=note,
            pdf_url=pdf_url,
        )

    def _extract_doc_number(self, page: Page) -> str:
        """Try to extract document number from URL or page content."""
        import re
        # From URL: /documents/123456
        m = re.search(r"/documents/(\d+)", page.url)
        if m:
            return m.group(1)
        # From page heading
        heading = page.locator(
            "h1, h2, [class*='doc-number'], [class*='invoice-number']"
        )
        if heading.count() > 0:
            text = heading.first.inner_text()
            m = re.search(r"\d+", text)
            if m:
                return m.group(0)
        return "N/A"

    @staticmethod
    def _month_label(d: date) -> str:
        """Return Hebrew month/year string, e.g. 'אפריל 2026'."""
        MONTHS_HE = [
            "", "ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני",
            "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר",
        ]
        return f"{MONTHS_HE[d.month]} {d.year}"

    @staticmethod
    def _build_note(billing_month: date, extra: str) -> str:
        MONTHS_HE = [
            "", "ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני",
            "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר",
        ]
        month_name = MONTHS_HE[billing_month.month]
        note = f"עבור חודש {month_name} {billing_month.year}"
        if extra:
            note += f" | {extra}"
        return note
