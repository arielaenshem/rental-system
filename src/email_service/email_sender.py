"""
Email notification service.

Sends two types of emails:
  1. Contract renewal alert – when contract_end_date is within RENEWAL_ALERT_DAYS.
  2. (Optional) Invoice summary – after invoices are issued.

Credentials via environment variables:
    EMAIL_SENDER           – Gmail address used to send
    EMAIL_APP_PASSWORD     – Gmail App Password (not the regular password)
    EMAIL_RENEWAL_RECIPIENT – recipient for renewal alerts (default ariel.abash@gmail.com)
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_RECIPIENT = "ariel.abash@gmail.com"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465  # SSL


class EmailService:
    def __init__(
        self,
        sender: Optional[str] = None,
        app_password: Optional[str] = None,
        renewal_recipient: Optional[str] = None,
    ):
        self.sender = sender or os.environ.get("EMAIL_SENDER", "")
        self.app_password = app_password or os.environ.get("EMAIL_APP_PASSWORD", "")
        self.renewal_recipient = (
            renewal_recipient
            or os.environ.get("EMAIL_RENEWAL_RECIPIENT", DEFAULT_RECIPIENT)
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send_renewal_alert(
        self,
        customer_name: str,
        contract_end_date: date,
        days_remaining: int,
    ) -> bool:
        """Send a contract renewal reminder email.

        Returns True on success, False on failure.
        """
        subject = f"[מערכת שכירות] חידוש חוזה נדרש – {customer_name}"
        body_html = self._renewal_html(customer_name, contract_end_date, days_remaining)
        body_text = self._renewal_text(customer_name, contract_end_date, days_remaining)
        return self._send(self.renewal_recipient, subject, body_html, body_text)

    def send_invoice_summary(
        self,
        customer_name: str,
        invoice_number: str,
        invoice_amount: float,
        payment_request_number: str,
        indexed_amount: float,
        billing_month: date,
        recipient: Optional[str] = None,
    ) -> bool:
        """Send a summary email after issuing an invoice.

        Sent to the renewal recipient by default; pass *recipient* to override.
        """
        to = recipient or self.renewal_recipient
        subject = f"[מערכת שכירות] חשבונית הופקה – {customer_name}"
        body_html = self._summary_html(
            customer_name, invoice_number, invoice_amount,
            payment_request_number, indexed_amount, billing_month,
        )
        body_text = self._summary_text(
            customer_name, invoice_number, invoice_amount,
            payment_request_number, indexed_amount, billing_month,
        )
        return self._send(to, subject, body_html, body_text)

    def send_custom(
        self,
        subject: str,
        body_html: str,
        body_text: str,
        recipient: Optional[str] = None,
    ) -> bool:
        """Send an arbitrary email (defaults to the renewal recipient)."""
        return self._send(recipient or self.renewal_recipient, subject, body_html, body_text)

    # ------------------------------------------------------------------
    # Core send method
    # ------------------------------------------------------------------

    def _send(
        self,
        to: str,
        subject: str,
        body_html: str,
        body_text: str,
    ) -> bool:
        if not self.sender or not self.app_password:
            logger.warning(
                "Email credentials not configured – skipping send to %s.", to
            )
            return False

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = self.sender
        msg["To"]      = to

        msg.attach(MIMEText(body_text, "plain", "utf-8"))
        msg.attach(MIMEText(body_html, "html",  "utf-8"))

        context = ssl.create_default_context()
        try:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context) as server:
                server.login(self.sender, self.app_password)
                server.sendmail(self.sender, to, msg.as_string())
            logger.info("Email sent to %s: %s", to, subject)
            return True
        except Exception as exc:
            logger.error("Failed to send email to %s: %s", to, exc)
            return False

    # ------------------------------------------------------------------
    # Email body templates
    # ------------------------------------------------------------------

    @staticmethod
    def _renewal_html(
        customer_name: str, end_date: date, days_remaining: int
    ) -> str:
        end_str = end_date.strftime("%d/%m/%Y")
        urgency = "🔴 דחוף" if days_remaining <= 7 else ("🟡 עדכון" if days_remaining <= 14 else "🔔 תזכורת")
        return f"""
<!DOCTYPE html>
<html dir="rtl" lang="he">
<head><meta charset="UTF-8"><title>חידוש חוזה</title></head>
<body style="font-family: Arial, sans-serif; direction: rtl; padding: 20px;">
  <h2 style="color: #c0392b;">{urgency} – חידוש חוזה שכירות</h2>
  <p>שלום,</p>
  <p>
    החוזה עם הלקוח <strong>{customer_name}</strong> מסתיים בתאריך
    <strong>{end_str}</strong>
    (עוד <strong>{days_remaining} ימים</strong>).
  </p>
  <p>יש לפנות ללקוח לצורך חידוש ההסכם.</p>
  <hr/>
  <p style="font-size:12px; color:#888;">
    הודעה זו נשלחה אוטומטית ממערכת ניהול השכירות.
  </p>
</body>
</html>
"""

    @staticmethod
    def _renewal_text(
        customer_name: str, end_date: date, days_remaining: int
    ) -> str:
        return (
            f"תזכורת חידוש חוזה\n\n"
            f"לקוח: {customer_name}\n"
            f"תאריך סיום חוזה: {end_date.strftime('%d/%m/%Y')}\n"
            f"ימים שנותרו: {days_remaining}\n\n"
            f"יש לפנות ללקוח לצורך חידוש ההסכם.\n"
        )

    @staticmethod
    def _summary_html(
        customer_name: str,
        invoice_number: str,
        invoice_amount: float,
        payment_request_number: str,
        indexed_amount: float,
        billing_month: date,
    ) -> str:
        MONTHS_HE = [
            "", "ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני",
            "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר",
        ]
        month_str = f"{MONTHS_HE[billing_month.month]} {billing_month.year}"
        return f"""
<!DOCTYPE html>
<html dir="rtl" lang="he">
<head><meta charset="UTF-8"><title>סיכום חשבונית</title></head>
<body style="font-family: Arial, sans-serif; direction: rtl; padding: 20px;">
  <h2 style="color: #27ae60;">✅ חשבונית הופקה – {customer_name}</h2>
  <table border="1" cellpadding="8" cellspacing="0" style="border-collapse:collapse;">
    <tr><td><strong>לקוח</strong></td><td>{customer_name}</td></tr>
    <tr><td><strong>חודש חיוב</strong></td><td>{month_str}</td></tr>
    <tr><td><strong>מספר חשבונית</strong></td><td>{invoice_number}</td></tr>
    <tr><td><strong>סכום חשבונית (₪)</strong></td><td>{invoice_amount:,.2f}</td></tr>
    <tr><td><strong>מספר דרישת תשלום</strong></td><td>{payment_request_number}</td></tr>
    <tr><td><strong>סכום צמוד מדד (₪)</strong></td><td>{indexed_amount:,.2f}</td></tr>
  </table>
  <hr/>
  <p style="font-size:12px; color:#888;">
    הודעה זו נשלחה אוטומטית ממערכת ניהול השכירות.
  </p>
</body>
</html>
"""

    @staticmethod
    def _summary_text(
        customer_name: str,
        invoice_number: str,
        invoice_amount: float,
        payment_request_number: str,
        indexed_amount: float,
        billing_month: date,
    ) -> str:
        return (
            f"חשבונית הופקה\n\n"
            f"לקוח: {customer_name}\n"
            f"חודש: {billing_month.strftime('%m/%Y')}\n"
            f"מספר חשבונית: {invoice_number}  |  סכום: ₪{invoice_amount:,.2f}\n"
            f"דרישת תשלום: {payment_request_number}  |  סכום צמוד: ₪{indexed_amount:,.2f}\n"
        )
