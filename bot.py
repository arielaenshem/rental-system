"""
Telegram bot for controlling the rental invoice system from your phone.

Commands:
    /start          – welcome message
    /help           – show all commands
    /run            – run invoicing for current month
    /run 2026-03    – run for a specific month
    /dryrun         – simulate without issuing anything
    /dryrun 2026-03 – simulate for a specific month
    /status         – show last run results

Security: only the TELEGRAM_ALLOWED_CHAT_ID user can operate the bot.

Setup:
    1. Create a bot via @BotFather → get TELEGRAM_BOT_TOKEN
    2. Get your chat ID via @userinfobot → set TELEGRAM_ALLOWED_CHAT_ID
    3. Add both to .env
    4. Run:  python bot.py
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, date
from typing import Optional

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

load_dotenv()

from src.bank.discount_bank import DiscountBankClient
from src.cpi.cpi_client import CPIClient
from src.email_service.email_sender import EmailService
from src.excel_parser import load_customers
from src.invoice.invoice4u import Invoice4uClient
from src.processor import RentalProcessor, ProcessingResult

logger = logging.getLogger(__name__)

MONTHS_HE = [
    "", "ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני",
    "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר",
]

# ---------------------------------------------------------------------------
# Security – only allow the configured chat ID
# ---------------------------------------------------------------------------

def _get_allowed_id() -> Optional[int]:
    val = os.environ.get("TELEGRAM_ALLOWED_CHAT_ID", "")
    return int(val) if val.strip().lstrip("-").isdigit() else None


def _is_authorised(update: Update) -> bool:
    allowed = _get_allowed_id()
    if allowed is None:
        return True   # not configured → allow all (useful for first setup)
    return update.effective_chat.id == allowed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_month(arg: Optional[str]) -> Optional[date]:
    """Parse 'YYYY-MM' string into a date, or return None."""
    if not arg:
        return None
    try:
        return datetime.strptime(arg.strip(), "%Y-%m").date()
    except ValueError:
        return None


def _format_results(results: list[ProcessingResult], billing_month: date, dry_run: bool) -> str:
    month_str = f"{MONTHS_HE[billing_month.month]} {billing_month.year}"
    mode = "🔵 סימולציה" if dry_run else "✅ הרצה אמיתית"
    lines = [f"{mode} – {month_str}\n"]

    for r in results:
        if r.success:
            inv = f"חשבונית #{r.invoice.document_number} ₪{r.invoice.amount:,.0f}" if r.invoice else f"₪{r.indexed_amount or 0:,.0f}"
            idx = f" | צמוד ₪{r.indexed_amount:,.0f}" if r.indexed_amount else ""
            renewal = " 🔔 נשלחה התראת חידוש" if r.renewal_alert_sent else ""
            lines.append(f"✅ {r.customer_name}\n   {inv}{idx}{renewal}")
        else:
            lines.append(f"❌ {r.customer_name}\n   {r.error or 'שגיאה לא ידועה'}")

    success = sum(1 for r in results if r.success)
    lines.append(f"\nסה״כ: {success}/{len(results)} הצליחו")
    return "\n".join(lines)


async def _run_processor(
    update: Update,
    billing_month: date,
    dry_run: bool,
) -> None:
    month_str = f"{MONTHS_HE[billing_month.month]} {billing_month.year}"
    mode = "סימולציה" if dry_run else "הרצה אמיתית"
    await update.message.reply_text(
        f"⏳ מתחיל {mode} עבור {month_str}...\n(זה עשוי לקחת מספר דקות)"
    )

    excel_path = os.environ.get("CUSTOMERS_EXCEL_PATH", "data/customers.xlsx")
    tolerance  = float(os.environ.get("AMOUNT_TOLERANCE_PCT", 5.0))
    alert_days = int(os.environ.get("RENEWAL_ALERT_DAYS", 30))

    try:
        customers = load_customers(excel_path)
        if not customers:
            await update.message.reply_text("⚠️ לא נמצאו לקוחות בקובץ האקסל.")
            return

        await update.message.reply_text(f"📋 נטענו {len(customers)} לקוחות מהאקסל.")

        processor = RentalProcessor(
            bank_client=DiscountBankClient(),
            invoice_client=Invoice4uClient(),
            cpi_client=CPIClient(),
            email_service=EmailService(),
            renewal_alert_days=alert_days,
            amount_tolerance_pct=tolerance,
            billing_month=billing_month,
            dry_run=dry_run,
        )

        results = processor.process_all(customers)
        report  = _format_results(results, billing_month, dry_run)
        await update.message.reply_text(report)

    except FileNotFoundError:
        await update.message.reply_text(
            f"❌ קובץ האקסל לא נמצא:\n{excel_path}\n\nודא שהנתיב נכון ב-.env"
        )
    except Exception as exc:
        logger.error("Bot run failed: %s", exc, exc_info=True)
        await update.message.reply_text(f"❌ שגיאה:\n{exc}")


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorised(update):
        await update.message.reply_text("⛔ אין הרשאה.")
        return
    await update.message.reply_text(
        "👋 שלום! מערכת ניהול שכירות מוכנה.\n\n"
        "השתמש ב-/help לרשימת הפקודות."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorised(update):
        await update.message.reply_text("⛔ אין הרשאה.")
        return
    await update.message.reply_text(
        "📖 פקודות זמינות:\n\n"
        "/run – הרץ עבור החודש הנוכחי\n"
        "/run 2026-03 – הרץ עבור חודש ספציפי (YYYY-MM)\n"
        "/dryrun – סימולציה ללא הפקת מסמכים\n"
        "/dryrun 2026-03 – סימולציה לחודש ספציפי\n"
        "/status – מצב המערכת\n"
        "/help – הצג עזרה זו"
    )


async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorised(update):
        await update.message.reply_text("⛔ אין הרשאה.")
        return

    month_arg = context.args[0] if context.args else None
    billing_month = _parse_month(month_arg) or date.today().replace(day=1)

    if month_arg and not _parse_month(month_arg):
        await update.message.reply_text("❌ פורמט חודש שגוי. השתמש ב: YYYY-MM (למשל 2026-03)")
        return

    await _run_processor(update, billing_month, dry_run=False)


async def cmd_dryrun(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorised(update):
        await update.message.reply_text("⛔ אין הרשאה.")
        return

    month_arg = context.args[0] if context.args else None
    billing_month = _parse_month(month_arg) or date.today().replace(day=1)

    if month_arg and not _parse_month(month_arg):
        await update.message.reply_text("❌ פורמט חודש שגוי. השתמש ב: YYYY-MM (למשל 2026-03)")
        return

    await _run_processor(update, billing_month, dry_run=True)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorised(update):
        await update.message.reply_text("⛔ אין הרשאה.")
        return

    excel_path = os.environ.get("CUSTOMERS_EXCEL_PATH", "data/customers.xlsx")
    try:
        customers = load_customers(excel_path)
        today = date.today()
        lines = [f"📊 מצב מערכת – {today.strftime('%d/%m/%Y')}\n"]
        lines.append(f"👥 לקוחות פעילים: {len(customers)}\n")

        upcoming = [c for c in customers if 0 <= c.days_until_contract_end() <= 60]
        if upcoming:
            lines.append("🔔 חוזים שמסתיימים ב-60 יום הקרובים:")
            for c in upcoming:
                lines.append(
                    f"  • {c.customer_name} – {c.contract_end_date.strftime('%d/%m/%Y')} "
                    f"(עוד {c.days_until_contract_end()} ימים)"
                )
        else:
            lines.append("✅ אין חוזים הפגים בקרוב")

        await update.message.reply_text("\n".join(lines))
    except Exception as exc:
        await update.message.reply_text(f"❌ שגיאה בטעינת נתונים:\n{exc}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
        level=logging.INFO,
    )

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise ValueError(
            "TELEGRAM_BOT_TOKEN not set in .env\n"
            "Create a bot via @BotFather and add the token to .env"
        )

    allowed_id = _get_allowed_id()
    if allowed_id:
        logger.info("Bot restricted to chat_id: %s", allowed_id)
    else:
        logger.warning("TELEGRAM_ALLOWED_CHAT_ID not set – bot is open to anyone!")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("help",   cmd_help))
    app.add_handler(CommandHandler("run",    cmd_run))
    app.add_handler(CommandHandler("dryrun", cmd_dryrun))
    app.add_handler(CommandHandler("status", cmd_status))

    logger.info("Rental bot started. Waiting for commands…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
