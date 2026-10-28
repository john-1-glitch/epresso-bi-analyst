"""Independent check of the app's key numbers, using plain pandas and NONE of the app's
code (no analytics.py, no merge helpers). It reads the exported CSVs, joins Sales to
Stores on its own, and prints the measures in the validation table.

Usage: .venv/bin/python scripts/independent_check.py
"""
import pandas as pd

sales = pd.read_csv('data/exported/Sales.csv', parse_dates=['Date'])
stores = pd.read_csv('data/raw/Support_EX_B_Stores.csv', sep='\t')
categories = pd.read_csv('data/exported/Categories.csv')

joined = sales.merge(stores[['ID', 'StoreType']], left_on='Store ID', right_on='ID', how='left')
assert len(joined) == len(sales), 'the store join changed the row count'
joined = joined.merge(categories, on='Category ID', how='left')

print(f"Rows: {len(sales):,}")
print(f"Total revenue: {sales['Revenue'].sum():,.2f}")
print('Revenue by channel:')
print(joined.groupby('StoreType')['Revenue'].sum().map('{:,.2f}'.format).to_string())
print('Revenue by year:')
print(sales.groupby(sales['Date'].dt.year)['Revenue'].sum().map('{:,.2f}'.format).to_string())
month = sales.groupby(sales['Date'].dt.month)['Revenue'].sum()
print('Share of revenue by month (%):')
print((month / month.sum() * 100).round(2).to_string())
print('Revenue by category:')
print(joined.groupby('Category')['Revenue'].sum().sort_values(ascending=False)
      .map('{:,.2f}'.format).to_string())
