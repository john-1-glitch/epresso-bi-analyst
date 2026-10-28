"""One-off conversion: copy every table in the E-Presso Access database to its
own CSV so the Streamlit app (which accepts CSV/Excel uploads) can load it.
Values are written as-is; only the Date column is formatted as YYYY-MM-DD.

Usage: .venv/bin/python scripts/export_access.py
"""
from pathlib import Path

import pandas as pd
from access_parser import AccessParser

SOURCE = Path('data/raw/Support_EX_B_Sales.accdb')
OUT_DIR = Path('data/exported')
TABLES = ['Sales', 'Categories', 'Products']

OUT_DIR.mkdir(parents=True, exist_ok=True)
db = AccessParser(str(SOURCE))
for table in TABLES:
    df = pd.DataFrame(db.parse_table(table))
    if 'Date' in df.columns:
        df['Date'] = pd.to_datetime(df['Date']).dt.strftime('%Y-%m-%d')
    path = OUT_DIR / f'{table}.csv'
    df.to_csv(path, index=False)
    extra = f", revenue {df['Revenue'].sum():,.2f}" if 'Revenue' in df.columns else ''
    print(f'{table}: {len(df):,} rows -> {path}{extra}')
