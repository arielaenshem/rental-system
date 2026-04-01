# מערכת ניהול שכירות – Rental Management System

מערכת אוטומטית לניהול לקוחות, גביית דמי שכירות, הפקת חשבוניות וניהול חוזים.

---

## תכונות

| תכונה | תיאור |
|---|---|
| **קריאת Excel** | טעינת רשימת לקוחות עם סכומים, אחוזי החזקה ותאריכי חוזה |
| **בנק דיסקונט** | התחברות אוטומטית לחשבונות מרובים וחיפוש תשלומים נכנסים (סטייה 5%) |
| **Invoice4u** | הפקת חשבונית מס וכן דרישת תשלום עם הסכום הצמוד למדד |
| **מדד המחירים** | הצמדה למדד על בסיס נתוני הלמ"ס – חישוב סכום מעודכן |
| **מייל חידוש** | שליחת התראה אוטומטית ל-ariel.abash@gmail.com לפני פקיעת חוזה |
| **Scheduler** | הרצה חודשית אוטומטית (ה-1 בכל חודש) |

---

## דרישות מערכת

- Python 3.11+
- חשבון Invoice4u פעיל
- חשבון בנק דיסקונט עם גישה לבנקאות אינטרנטית
- חשבון Gmail עם App Password

---

## התקנה

```bash
# שכפל את הפרויקט
git clone <repo-url>
cd rental-system

# צור סביבה וירטואלית
python -m venv .venv
source .venv/bin/activate   # Linux/Mac
.venv\Scripts\activate      # Windows

# התקן תלויות
pip install -r requirements.txt

# התקן דפדפן Playwright
playwright install chromium

# הגדר קבצי סביבה
cp .env.example .env
# ערוך .env עם הפרטים שלך

# צור תבנית Excel לקוחות
python create_template.py
```

---

## קובץ ה-.env

```env
# בנק דיסקונט
DISCOUNT_BANK_ID=123456789
DISCOUNT_BANK_PASSWORD=your_password
DISCOUNT_BANK_ACCOUNTS=12345678,87654321   # חשבונות מופרדים בפסיק

# Invoice4u
INVOICE4U_EMAIL=your@email.com
INVOICE4U_PASSWORD=your_password

# Gmail (App Password בלבד – לא הסיסמה הרגילה)
EMAIL_SENDER=your@gmail.com
EMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
EMAIL_RENEWAL_RECIPIENT=ariel.abash@gmail.com

# הגדרות
RENEWAL_ALERT_DAYS=30          # ימים לפני פקיעת חוזה לשליחת התראה
CUSTOMERS_EXCEL_PATH=data/customers.xlsx
AMOUNT_TOLERANCE_PCT=5
```

---

## מבנה קובץ Excel הלקוחות

| עמודה | תיאור |
|---|---|
| שם לקוח | שם מלא |
| סכום צפוי | סכום חודשי צפוי (ב-₪) |
| אחוז החזקה | אחוז בעלות לחישוב החשבונית (0-100) |
| תאריך בסיס מדד | תאריך בסיס להצמדה למדד |
| תאריך התחלה | תחילת החוזה |
| תאריך סיום | סיום החוזה |
| חשבון בנק | מספר חשבון דיסקונט (ניתן לכתוב כמה מופרדים בפסיק) |
| מייל לקוח | כתובת מייל (אופציונלי) |
| הערות | הערות לחשבונית (אופציונלי) |

---

## הרצה

```bash
# הרצה רגילה לחודש הנוכחי
python main.py

# סימולציה ללא הפקת מסמכים
python main.py --dry-run

# חודש חיוב ספציפי
python main.py --billing-month 2026-03

# קובץ Excel מותאם
python main.py --excel data/customers_2026.xlsx

# הרצה חודשית אוטומטית (ה-1 בכל חודש)
python main.py --schedule
```

---

## תהליך העבודה האוטומטי

```
קובץ Excel
    │
    ▼
טעינת לקוחות
    │
    ▼
בנק דיסקונט ──► חיפוש תשלום נכנס (±5%)
    │                    │
    │            לא נמצא │  נמצא
    │                    ▼
    │        חישוב סכום חשבונית = תשלום × אחוז החזקה
    │        חישוב סכום צמוד    = סכום × (מדד עכשיו / מדד בסיס)
    │                    │
    │                    ▼
    │              Invoice4u ──► חשבונית מס (סכום מדויק)
    │                       └──► דרישת תשלום (סכום צמוד)
    │                    │
    │                    ▼
    │              מייל סיכום ──► ariel.abash@gmail.com
    │
    ▼
בדיקת תאריך סיום חוזה
    │
    ▼ (≤30 ימים)
מייל התראת חידוש ──► ariel.abash@gmail.com
```

---

## לוגים

הלוגים נשמרים ב-`logs/rental_system_YYYYMM.log`.

---

## מבנה הפרויקט

```
rental-system/
├── main.py                        # נקודת הכניסה הראשית
├── create_template.py             # יוצר תבנית Excel
├── requirements.txt
├── .env.example
├── data/
│   └── customers.xlsx             # קובץ הלקוחות
├── logs/
└── src/
    ├── excel_parser.py            # קריאת Excel
    ├── processor.py               # מנוע העיבוד הראשי
    ├── bank/
    │   └── discount_bank.py      # אוטומציה – בנק דיסקונט
    ├── invoice/
    │   └── invoice4u.py          # אוטומציה – Invoice4u
    ├── cpi/
    │   └── cpi_client.py         # מדד המחירים לצרכן (הלמ"ס)
    └── email_service/
        └── email_sender.py       # שליחת מיילים
```
