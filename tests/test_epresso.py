"""Tests for the E-Presso pipeline on a tiny made-up dataset you can check by hand.

Three stores (W = website, A and B = shops), two categories, six sales rows:

    Sales ID  Date        Store  Category       Revenue
    S1        2025-01-31  W      C1 Beans          100
    S2        2025-12-31  W      C2 Machines       300
    S3        2025-12-31  A      C1 Beans           50
    S4        2026-01-31  A      C2 Machines       200
    S5        2026-12-31  B      C1 Beans          150
    S6        2026-12-31  W      C2 Machines       200
                                           Total  1000

By hand: 2025 = 100+300+50 = 450, 2026 = 200+150+200 = 550.
Website = 100+300+200 = 600, Brick & Mortar = 50+200+150 = 400.
Beans = 100+50+150 = 300 (30%), Machines = 300+200+200 = 700 (70%).
January = 100+200 = 300 (30%), December = 300+50+150+200 = 700 (70%).
"""
import io
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import analytics  # noqa: E402

SALES = pd.DataFrame({
    'Sales ID': ['S1', 'S2', 'S3', 'S4', 'S5', 'S6'],
    'Date': ['2025-01-31', '2025-12-31', '2025-12-31', '2026-01-31', '2026-12-31', '2026-12-31'],
    'Store ID': ['W', 'W', 'A', 'A', 'B', 'W'],
    'Category ID': ['C1', 'C2', 'C1', 'C2', 'C1', 'C2'],
    'Product ID': ['P1', 'P2', 'P1', 'P2', 'P1', 'P2'],
    'Revenue': [100, 300, 50, 200, 150, 200],
})
STORES = pd.DataFrame({
    'ID': ['W', 'A', 'B'],
    'City': [None, 'Lansing', 'Denver'],
    'StoreType': ['Website', 'Brick & Mortar', 'Brick & Mortar'],
    'Market': ['5-Global', '1-Small', '4-Large'],
    'Location': ['Website', 'Midwest', 'West'],
})
CATEGORIES = pd.DataFrame({'Category ID': ['C1', 'C2'], 'Category': ['Beans', 'Machines']})
PRODUCTS = pd.DataFrame({'Product ID': ['P1', 'P2'], 'Product': ['Roast', 'Espresso']})


def csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode()


def uploads(sales=SALES, stores=STORES):
    """The four required tables as (role, filename, bytes), like the app's uploaders."""
    return [('sales', 'sales.csv', csv_bytes(sales)),
            ('stores', 'stores.csv', csv_bytes(stores)),
            ('categories', 'categories.csv', csv_bytes(CATEGORIES)),
            ('products', 'products.csv', csv_bytes(PRODUCTS))]


@pytest.fixture
def data():
    return analytics.build_dataset(uploads())


@pytest.fixture
def model(data):
    return data['model']


# ------------------------------------------------------- revenue and merges
def test_total_and_revenue_by_year(data):
    summary = analytics.revenue_summary(data['tables']['sales'])
    assert summary['total'] == 1000
    assert summary['by_year'] == {2025: 450, 2026: 550}
    assert summary['rows_used'] == 6 and summary['rows_excluded'] == 0


def test_every_join_keeps_rows_and_revenue(data):
    assert data['errors'] == []
    for report in data['merge_reports']:
        assert report['rows_before'] == report['rows_after'] == 6
        assert report['sum_before'] == report['sum_after'] == 1000
        assert report['unmatched_rows'] == 0 and not report['changed']


def test_plus_1000_changes_total_by_exactly_1000():
    edited = SALES.copy()
    edited.loc[0, 'Revenue'] += 1000  # S1: 100 -> 1100
    summary = analytics.revenue_summary(analytics.build_dataset(uploads(sales=edited))['tables']['sales'])
    assert summary['total'] == 2000
    assert summary['by_year'] == {2025: 1450, 2026: 550}


def test_duplicate_store_key_is_flagged_as_fan_out():
    stores = pd.concat([STORES, STORES.iloc[[1]]])  # store A twice
    report = analytics.build_dataset(uploads(stores=stores))['merge_reports'][0]
    # A has two sales (50 + 200), so each is counted twice: 1000 + 250 = 1250
    assert report['changed'] and report['rows_after'] == 8 and report['sum_after'] == 1250


def test_sales_for_unknown_store_is_counted_not_dropped():
    sales = SALES.copy()
    sales.loc[0, 'Store ID'] = 'Z'  # no store Z in the Stores table
    report = analytics.build_dataset(uploads(sales=sales))['merge_reports'][0]
    assert report['unmatched_rows'] == 1
    assert report['sum_after'] == 1000 and not report['changed']
    assert 'no match' in report['warning']


# ------------------------------------------------------- loading and cleaning
def test_missing_column_gives_message_naming_it():
    result = analytics.build_dataset(uploads(sales=SALES.drop(columns='Revenue')))
    assert len(result['errors']) == 1
    assert 'Missing required column(s): Revenue' in result['errors'][0]
    assert 'model' not in result


def test_excel_survey_with_title_rows_and_extra_sheet_is_found():
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer) as writer:
        pd.DataFrame({'Notes': ['not the data']}).to_excel(writer, sheet_name='Documentation', index=False)
        rows = [['E-Presso Market Survey', None, None, None],
                [None, None, None, None],
                ['Survey ID', 'Store ID', 'Year', 'Satisfaction'],
                ['A_25', 'A', 'Y2025', 4.5],
                ['W_25', 'W', 'Y2025', None]]
        pd.DataFrame(rows).to_excel(writer, sheet_name='Survey Results', index=False, header=False)
    df, err = analytics.load_role_table(buffer.getvalue(), 'survey.xlsx', 'survey')
    assert err is None and list(df['Survey ID']) == ['A_25', 'W_25']
    survey = analytics.clean_survey(df)
    assert list(survey['Year']) == [2025, 2025]
    assert survey['Satisfaction'].isna().sum() == 1  # blank stays blank, never 0


def test_clean_sales_keeps_every_row_and_counts_problems():
    raw = pd.DataFrame({'Sales ID': ['S1', 'S2', 'S2'],
                        'Date': ['2025-01-31', 'not a date', '2025-03-31'],
                        'Revenue': ['$1,000.00', None, '250']})
    clean, issues = analytics.clean_sales(raw)
    assert len(clean) == 3  # nothing dropped
    assert list(clean['Revenue'].fillna(-1)) == [1000, -1, 250]
    assert issues['Blank or non-numeric Revenue'] == 1
    assert issues['Blank or invalid Date'] == 1
    assert issues['Duplicate Sales ID'] == 1
    assert analytics.revenue_summary(clean)['total'] == 1250


def test_grain_table_is_built_from_the_data(data):
    grain = analytics.grain_table(data['tables'], data['sources']).set_index('Table')
    assert grain.loc['Sales', 'Row count'] == 6
    assert grain.loc['Stores', 'Key unique?'] == 'Yes'
    assert grain.loc['Sales', 'Contains revenue?'] == 'Yes'
    assert grain.loc['Categories', 'Contains revenue?'] == 'No'


# ---------------------------------------------------- filters and charts
def test_filter_options_come_from_the_data(model):
    options = analytics.filter_options(model)
    assert options['years'] == [2025, 2026]
    assert options['channels'] == ['Brick & Mortar', 'Website']
    assert options['categories'] == ['Beans', 'Machines']
    assert options['stores'] == {'A': 'A · Lansing (Midwest)', 'B': 'B · Denver (West)',
                                 'W': 'W · Website'}


def test_filter_sales_combines_filters_and_leaves_data_unchanged(model):
    before = model.copy()
    shops_2026 = analytics.filter_sales(model, years=[2026], channels=['Brick & Mortar'])
    assert sorted(shops_2026['Sales ID']) == ['S4', 'S5']
    assert shops_2026['Revenue'].sum() == 350
    assert analytics.filter_sales(model, stores=['A'])['Revenue'].sum() == 250
    assert len(analytics.filter_sales(model)) == 6  # no filters = everything
    pd.testing.assert_frame_equal(model, before)


def test_revenue_by_year_and_channel(model):
    table = analytics.revenue_by_year_channel(model)
    got = {(int(r.Year), r.StoreType): r.Revenue for r in table.itertuples()}
    assert got == {(2025, 'Brick & Mortar'): 50, (2025, 'Website'): 400,
                   (2026, 'Brick & Mortar'): 350, (2026, 'Website'): 200}


def test_revenue_by_category_largest_first_with_shares(model):
    table = analytics.revenue_by_category(model)
    assert list(table['Category']) == ['Machines', 'Beans']
    assert list(table['Revenue']) == [700, 300]
    assert list(table['Share']) == pytest.approx([0.7, 0.3])


def test_revenue_by_month_lists_all_twelve_months(model):
    table = analytics.revenue_by_month(model).set_index('Month')
    assert len(table) == 12
    assert table.loc['Jan', 'Revenue'] == 300 and table.loc['Dec', 'Revenue'] == 700
    assert table.loc['Jun', 'Revenue'] == 0
    assert table['Share'].sum() == pytest.approx(1.0)


def test_empty_selection_does_not_crash(model):
    empty = analytics.filter_sales(model, stores=['W'], channels=['Brick & Mortar'])
    assert empty.empty
    assert analytics.revenue_by_category(empty).empty
    assert analytics.revenue_by_month(empty)['Revenue'].sum() == 0
