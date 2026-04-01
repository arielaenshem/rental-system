"""
Core processing engine.

For each customer in the Excel file:
  1. Fetch bank transactions from Discount Bank.
  2. Find a transaction matching the expected amount (±5%).
  3. Calculate the invoice amount = transaction_amount × ownership%.
  4. Calculate the CPI-indexed amount for the payment request.
  5. Issue invoice on Invoice4u.
  6. Issue payment request on Invoice4u.
  7. Send invoice summary email.
  8. Check contract end date; if within alert window, send renewal email.

All results are logged and returned as ProcessingResult objects.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional

from src.bank.discount_bank import BankTransaction, DiscountBankClient
from src.cpi.cpi_client import CPIClient
from src.email_service.email_sender import EmailService
from src.excel_parser import Customer
from src.invoice.invoice4u import Invoice4uClient, InvoiceResult

logger = logging.getLogger(__name__)


@dataclass
class ProcessingResult:
    customer_name: str
    success: bool
    matched_transaction: Optional[BankTransaction] = None
    invoice: Optional[InvoiceResult] = None
    payment_request: Optional[InvoiceResult] = None
    indexed_amount: Optional[float] = None
    renewal_alert_sent: bool = False
    error: Optional[str] = None


class RentalProcessor:
    """Orchestrates the full invoice-automation workflow."""

    def __init__(
        self,
        bank_client: Optional[DiscountBankClient] = None,
        invoice_client: Optional[Invoice4uClient] = None,
        cpi_client: Optional[CPIClient] = None,
        email_service: Optional[EmailService] = None,
        renewal_alert_days: int = 30,
        amount_tolerance_pct: float = 5.0,
        billing_month: Optional[date] = None,
        dry_run: bool = False,
    ):
        self.bank_client        = bank_client        or DiscountBankClient()
        self.invoice_client     = invoice_client     or Invoice4uClient()
        self.cpi_client         = cpi_client         or CPIClient()
        self.email_service      = email_service      or EmailService()
        self.renewal_alert_days = renewal_alert_days
        self.tolerance          = amount_tolerance_pct
        self.billing_month      = billing_month or date.today().replace(day=1)
        self.dry_run            = dry_run

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def process_all(self, customers: List[Customer]) -> List[ProcessingResult]:
        """Run the full workflow for every customer.

        Returns one ProcessingResult per customer.
        """
        logger.info("Starting processing for %d customers (dry_run=%s).", len(customers), self.dry_run)

        # Fetch all bank transactions once (covers all accounts)
        try:
            all_transactions = self.bank_client.fetch_transactions()
            logger.info("Fetched %d bank transactions total.", len(all_transactions))
        except Exception as exc:
            logger.error("Could not fetch bank transactions: %s", exc)
            all_transactions = []

        results: List[ProcessingResult] = []
        for customer in customers:
            result = self._process_customer(customer, all_transactions)
            results.append(result)

        self._log_summary(results)
        return results

    # ------------------------------------------------------------------
    # Per-customer processing
    # ------------------------------------------------------------------

    def _process_customer(
        self,
        customer: Customer,
        all_transactions: List[BankTransaction],
    ) -> ProcessingResult:
        logger.info("Processing customer: %s", customer.customer_name)
        result = ProcessingResult(customer_name=customer.customer_name, success=False)

        try:
            # 1. Find matching bank transaction
            txn = self._find_transaction(customer, all_transactions)
            if txn is None:
                logger.warning(
                    "%s: no matching transaction found (expected ₪%.2f ±%.0f%%).",
                    customer.customer_name, customer.expected_amount, self.tolerance,
                )
                result.error = "No matching bank transaction found."
                # Still send renewal alert if applicable
                self._check_renewal(customer, result)
                return result

            result.matched_transaction = txn
            logger.info(
                "%s: matched transaction ₪%.2f on %s.",
                customer.customer_name, txn.amount, txn.transaction_date,
            )

            # 2. Calculate amounts
            invoice_amount = customer.invoice_amount(txn.amount)
            indexed_amount = self.cpi_client.indexed_amount(
                invoice_amount, customer.cpi_base_date
            )
            result.indexed_amount = indexed_amount

            logger.info(
                "%s: invoice=₪%.2f, CPI-indexed=₪%.2f",
                customer.customer_name, invoice_amount, indexed_amount,
            )

            if not self.dry_run:
                # 3. Issue invoice
                invoice = self.invoice_client.issue_invoice(
                    customer_name=customer.customer_name,
                    amount=invoice_amount,
                    billing_month=self.billing_month,
                    extra_note=customer.notes or "",
                )
                result.invoice = invoice

                # 4. Issue payment request
                payment_req = self.invoice_client.issue_payment_request(
                    customer_name=customer.customer_name,
                    indexed_amount=indexed_amount,
                    billing_month=self.billing_month,
                    extra_note=customer.notes or "",
                )
                result.payment_request = payment_req

                # 5. Send summary email
                self.email_service.send_invoice_summary(
                    customer_name=customer.customer_name,
                    invoice_number=invoice.document_number,
                    invoice_amount=invoice_amount,
                    payment_request_number=payment_req.document_number,
                    indexed_amount=indexed_amount,
                    billing_month=self.billing_month,
                )
            else:
                logger.info("[DRY RUN] Would issue invoice ₪%.2f and payment request ₪%.2f",
                            invoice_amount, indexed_amount)

            # 6. Contract renewal check
            self._check_renewal(customer, result)

            result.success = True

        except Exception as exc:
            logger.error("Error processing %s: %s", customer.customer_name, exc, exc_info=True)
            result.error = str(exc)

        return result

    def _find_transaction(
        self,
        customer: Customer,
        all_transactions: List[BankTransaction],
    ) -> Optional[BankTransaction]:
        """Search transactions for this customer's expected amount and accounts."""
        for account in customer.bank_accounts:
            txn = self.bank_client.find_matching_transaction(
                transactions=all_transactions,
                expected_amount=customer.expected_amount,
                account_number=account,
                tolerance_pct=self.tolerance,
            )
            if txn:
                return txn
        # If not found per-account, try all transactions
        return self.bank_client.find_matching_transaction(
            transactions=all_transactions,
            expected_amount=customer.expected_amount,
            tolerance_pct=self.tolerance,
        )

    def _check_renewal(self, customer: Customer, result: ProcessingResult) -> None:
        """Send renewal alert if contract is ending soon."""
        days_left = customer.days_until_contract_end()
        if 0 <= days_left <= self.renewal_alert_days:
            logger.info(
                "%s: contract ends in %d days – sending renewal alert.",
                customer.customer_name, days_left,
            )
            if not self.dry_run:
                sent = self.email_service.send_renewal_alert(
                    customer_name=customer.customer_name,
                    contract_end_date=customer.contract_end_date,
                    days_remaining=days_left,
                )
                result.renewal_alert_sent = sent
            else:
                logger.info("[DRY RUN] Would send renewal alert for %s.", customer.customer_name)

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    @staticmethod
    def _log_summary(results: List[ProcessingResult]) -> None:
        total   = len(results)
        success = sum(1 for r in results if r.success)
        failed  = total - success
        renewals = sum(1 for r in results if r.renewal_alert_sent)

        logger.info("=" * 60)
        logger.info("Processing summary: %d/%d succeeded, %d failed, %d renewal alerts sent.",
                    success, total, failed, renewals)
        for r in results:
            status = "✓" if r.success else "✗"
            msg = f"  {status} {r.customer_name}"
            if r.invoice:
                msg += f" | Invoice #{r.invoice.document_number} ₪{r.invoice.amount:,.2f}"
            if r.indexed_amount:
                msg += f" | CPI-indexed ₪{r.indexed_amount:,.2f}"
            if r.error:
                msg += f" | ERROR: {r.error}"
            logger.info(msg)
        logger.info("=" * 60)
