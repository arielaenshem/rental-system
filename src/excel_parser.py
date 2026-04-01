"""
Excel parser for rental system customers file.

Expected columns in the Excel file:
- customer_name       : string  - Customer full name
- expected_amount     : float   - Expected monthly payment (5% tolerance applied)
- ownership_pct       : float   - Ownership percentage (e.g. 50 means 50%)
- cpi_base_date       : date    - Date from which CPI indexing starts
- contract_start_date : date    - Contract start date
- contract_end_date   : date    - Contract end date (renewal alert sent near this date)
- bank_account        : string  - Discount Bank account number(s), comma-separated
- customer_email      : string  - Customer email (optional)
- notes               : string  - Free-text notes (optional)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = {
    "customer_name",
    "expected_amount",
    "ownership_pct",
    "cpi_base_date",
    "contract_start_date",
    "contract_end_date",
    "bank_account",
}

COLUMN_ALIASES: dict[str, str] = {
    # Hebrew aliases → canonical name
    "שם לקוח": "customer_name",
    "שם": "customer_name",
    "סכום צפוי": "expected_amount",
    "סכום": "expected_amount",
    "אחוז החזקה": "ownership_pct",
    "אחוז": "ownership_pct",
    "תאריך בסיס מדד": "cpi_base_date",
    "מדד בסיס": "cpi_base_date",
    "תאריך התחלה": "contract_start_date",
    "תחילת חוזה": "contract_start_date",
    "תאריך סיום": "contract_end_date",
    "סיום חוזה": "contract_end_date",
    "חשבון בנק": "bank_account",
    "מספר חשבון": "bank_account",
    "מייל לקוח": "customer_email",
    "אימייל": "customer_email",
    "הערות": "notes",
    # English aliases
    "name": "customer_name",
    "amount": "expected_amount",
    "ownership": "ownership_pct",
    "cpi_date": "cpi_base_date",
    "start_date": "contract_start_date",
    "end_date": "contract_end_date",
    "account": "bank_account",
    "email": "customer_email",
}


@dataclass
class Customer:
    customer_name: str
    expected_amount: float
    ownership_pct: float          # percentage, e.g. 50.0
    cpi_base_date: date
    contract_start_date: date
    contract_end_date: date
    bank_accounts: List[str]      # one or more account numbers
    customer_email: Optional[str] = None
    notes: Optional[str] = None

    @property
    def ownership_fraction(self) -> float:
        return self.ownership_pct / 100.0

    @property
    def amount_lower(self) -> float:
        """Lower bound with 5% tolerance."""
        return self.expected_amount * 0.95

    @property
    def amount_upper(self) -> float:
        """Upper bound with 5% tolerance."""
        return self.expected_amount * 1.05

    def amount_matches(self, actual: float, tolerance_pct: float = 5.0) -> bool:
        """Return True if *actual* is within tolerance of expected_amount."""
        lower = self.expected_amount * (1 - tolerance_pct / 100)
        upper = self.expected_amount * (1 + tolerance_pct / 100)
        return lower <= actual <= upper

    def invoice_amount(self, actual_received: float) -> float:
        """Invoice amount = actual received × ownership fraction."""
        return round(actual_received * self.ownership_fraction, 2)

    def days_until_contract_end(self) -> int:
        return (self.contract_end_date - date.today()).days


def _normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename columns using COLUMN_ALIASES so downstream code uses canonical names."""
    rename_map = {}
    for col in df.columns:
        canonical = COLUMN_ALIASES.get(col.strip())
        if canonical:
            rename_map[col] = canonical
    return df.rename(columns=rename_map)


def _parse_date(value) -> Optional[date]:
    if pd.isna(value):
        return None
    if isinstance(value, (date, pd.Timestamp)):
        return pd.Timestamp(value).date()
    try:
        return pd.Timestamp(str(value)).date()
    except Exception:
        return None


def _parse_accounts(value) -> List[str]:
    if pd.isna(value):
        return []
    return [a.strip() for a in str(value).split(",") if a.strip()]


def load_customers(path: str | Path, sheet_name: int | str = 0) -> List[Customer]:
    """Load and validate customers from an Excel file.

    Args:
        path: Path to the .xlsx / .xls file.
        sheet_name: Sheet name or 0-based index (default first sheet).

    Returns:
        List of validated Customer objects.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Customers file not found: {path}")

    df = pd.read_excel(path, sheet_name=sheet_name)
    df.columns = [str(c).strip() for c in df.columns]
    df = _normalise_columns(df)

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(
            f"Excel file is missing required columns: {missing}\n"
            f"Available columns: {list(df.columns)}"
        )

    customers: List[Customer] = []
    for idx, row in df.iterrows():
        row_num = idx + 2  # 1-based, header is row 1
        try:
            name = str(row["customer_name"]).strip()
            expected_amount = float(row["expected_amount"])
            ownership_pct = float(row["ownership_pct"])
            cpi_base_date = _parse_date(row["cpi_base_date"])
            contract_start = _parse_date(row["contract_start_date"])
            contract_end = _parse_date(row["contract_end_date"])
            accounts = _parse_accounts(row["bank_account"])

            if not name or name.lower() == "nan":
                logger.warning("Row %d: empty customer_name, skipping.", row_num)
                continue
            if expected_amount <= 0:
                logger.warning("Row %d: expected_amount must be > 0, skipping.", row_num)
                continue
            if not (0 < ownership_pct <= 100):
                logger.warning("Row %d: ownership_pct must be 0-100, skipping.", row_num)
                continue
            if not cpi_base_date:
                logger.warning("Row %d: invalid cpi_base_date, skipping.", row_num)
                continue
            if not contract_start:
                logger.warning("Row %d: invalid contract_start_date, skipping.", row_num)
                continue
            if not contract_end:
                logger.warning("Row %d: invalid contract_end_date, skipping.", row_num)
                continue
            if not accounts:
                logger.warning("Row %d: no bank_account specified, skipping.", row_num)
                continue

            customer_email = (
                str(row.get("customer_email", "")).strip() or None
                if "customer_email" in df.columns else None
            )
            notes = (
                str(row.get("notes", "")).strip() or None
                if "notes" in df.columns else None
            )

            customers.append(
                Customer(
                    customer_name=name,
                    expected_amount=expected_amount,
                    ownership_pct=ownership_pct,
                    cpi_base_date=cpi_base_date,
                    contract_start_date=contract_start,
                    contract_end_date=contract_end,
                    bank_accounts=accounts,
                    customer_email=customer_email,
                    notes=notes,
                )
            )
            logger.debug("Loaded customer: %s", name)
        except Exception as exc:
            logger.error("Row %d: failed to parse — %s", row_num, exc)

    logger.info("Loaded %d customers from %s", len(customers), path)
    return customers
