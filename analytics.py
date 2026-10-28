"""Business logic. Keep Streamlit code OUT of this file so every function can be
tested on its own and reused by the assistant."""
from __future__ import annotations

import functools
import io
import logging
import tempfile
from dataclasses import asdict, dataclass, field

import pandas as pd

logger = logging.getLogger(__name__)

EXCEL_EXT = ('.xlsx', '.xlsm', '.xls')
TEXT_EXT = ('.csv', '.txt', '.tsv')
ACCESS_EXT = ('.accdb', '.mdb')

# Columns each uploaded table must have. Any other columns in the file are kept.
REQUIRED_COLUMNS = {
    'sales': ['Sales ID', 'Date', 'Store ID', 'Category ID', 'Product ID', 'Revenue'],
    'stores': ['ID', 'City', 'StoreType', 'Market', 'Location'],
    'categories': ['Category ID', 'Category'],
    'products': ['Product ID', 'Product'],
    'survey': ['Survey ID', 'Store ID', 'Year', 'Satisfaction'],
}
REQUIRED_TABLES = ('sales', 'stores', 'categories', 'products')  # survey is optional

# role: (display name, what one row represents, key column)
TABLE_INFO = {
    'sales': ('Sales', 'one product at one store in one month', 'Sales ID'),
    'stores': ('Stores', 'one store (including the website)', 'ID'),
    'categories': ('Categories', 'one product category', 'Category ID'),
    'products': ('Products', 'one product', 'Product ID'),
    'survey': ('Survey Results', 'one store survey in one year', 'Survey ID'),
}


# ---------------------------------------------------------------- loading
def excel_sheet_names(file_bytes: bytes) -> list[str]:
    return pd.ExcelFile(io.BytesIO(file_bytes)).sheet_names


def load_table(file_bytes: bytes, filename: str, sheet_name=0) -> pd.DataFrame:
    """Read an uploaded CSV / TXT / TSV / Excel file into a DataFrame."""
    name = filename.lower()
    buffer = io.BytesIO(file_bytes)
    if name.endswith(EXCEL_EXT):
        return pd.read_excel(buffer, sheet_name=sheet_name)
    if name.endswith(TEXT_EXT):
        return pd.read_csv(buffer, sep=None, engine='python')  # sniffs the delimiter
    raise ValueError(f'Unsupported file type: {filename}')


@functools.lru_cache(maxsize=4)
def _access_tables(file_bytes: bytes) -> dict:
    """Every user table in an Access database, read once per distinct file contents."""
    from access_parser import AccessParser

    with tempfile.NamedTemporaryFile(suffix='.accdb') as tmp:
        tmp.write(file_bytes)
        tmp.flush()
        db = AccessParser(tmp.name)
    return {name: pd.DataFrame(db.parse_table(name)) for name in db.catalog
            if not name.startswith(('MSys', 'f_'))}  # skip system and attachment tables


def _candidate_tables(file_bytes: bytes, filename: str):
    """Yield every table in an upload: each sheet of a workbook, each table of an
    Access database, or the single table in a CSV / text file."""
    name = filename.lower()
    if name.endswith(ACCESS_EXT):
        for df in _access_tables(file_bytes).values():
            yield df.copy()
    elif name.endswith(EXCEL_EXT):
        for sheet in excel_sheet_names(file_bytes):
            yield load_table(file_bytes, filename, sheet)
    else:
        yield load_table(file_bytes, filename)


def missing_columns(df: pd.DataFrame, role: str) -> list[str]:
    return [c for c in REQUIRED_COLUMNS[role] if c not in df.columns]


def _promote_header(df: pd.DataFrame, required: list[str], max_scan: int = 10) -> pd.DataFrame:
    """Some sheets put a title above the real header (the survey's header is on row 3).
    If the required columns are not already the header, look for them in the first rows."""
    if all(c in df.columns for c in required):
        return df
    for i in range(min(max_scan, len(df))):
        row = [str(v).strip() for v in df.iloc[i]]
        if all(c in row for c in required):
            out = df.iloc[i + 1:].copy()
            out.columns = row
            return out.reset_index(drop=True)
    return df


def load_role_table(file_bytes: bytes, filename: str, role: str):
    """Load the upload for one table (sales, stores, ...). For an Excel workbook or an
    Access database, use the first sheet / table that has the required columns.
    Returns (df, None), or (None, a readable message naming the missing columns)
    so the app never shows a traceback."""
    required = REQUIRED_COLUMNS[role]
    label = TABLE_INFO[role][0]
    best_missing = required
    try:
        for df in _candidate_tables(file_bytes, filename):
            df.columns = [str(c).strip() for c in df.columns]
            df = _promote_header(df, required)
            missing = missing_columns(df, role)
            if not missing:
                return df, None
            if len(missing) < len(best_missing):
                best_missing = missing
    except Exception as exc:
        return None, f'Could not read {filename} as the {label} table: {exc}'
    return None, (f'{filename} cannot be used as the {label} table. '
                  f'Missing required column(s): {", ".join(best_missing)}.')


# ---------------------------------------------------------------- cleaning
def clean_sales(sales: pd.DataFrame):
    """Give Date and Revenue proper types and add a Year column. No rows are dropped,
    so the revenue total never changes silently; blanks are counted instead.
    Returns (clean_df, issues)."""
    out = sales.copy()
    out['Date'] = pd.to_datetime(out['Date'], errors='coerce')
    revenue = out['Revenue']
    if revenue.dtype == object:  # tolerate "$1,000.00" typed into a CSV by hand
        revenue = revenue.astype(str).str.replace(r'[$,\s]', '', regex=True)
    out['Revenue'] = pd.to_numeric(revenue, errors='coerce')
    out['Year'] = out['Date'].dt.year.astype('Int64')
    issues = {
        'Blank or invalid Date': int(out['Date'].isna().sum()),
        'Blank or non-numeric Revenue': int(out['Revenue'].isna().sum()),
        'Negative Revenue': int((out['Revenue'] < 0).sum()),
        'Duplicate Sales ID': int(out['Sales ID'].duplicated().sum()),
    }
    return out, issues


def clean_survey(survey: pd.DataFrame) -> pd.DataFrame:
    """Numeric scores, and Year as a number ("Y2025" -> 2025) so it lines up with sales years.
    Blank scores stay blank (the website was not surveyed); they are never treated as zero."""
    out = survey.copy()
    out['Satisfaction'] = pd.to_numeric(out['Satisfaction'], errors='coerce')
    if 'Interest' in out.columns:
        out['Interest'] = pd.to_numeric(out['Interest'], errors='coerce')
    out['Year'] = pd.to_numeric(out['Year'].astype(str).str.extract(r'(\d{4})')[0],
                                errors='coerce').astype('Int64')
    return out


def profile_table(df: pd.DataFrame) -> dict:
    """Basic profile: size, duplicates, missing values, data types."""
    missing = df.isna().sum()
    return {
        'rows': len(df),
        'columns': df.shape[1],
        'duplicate_rows': int(df.duplicated().sum()),
        'missing': pd.DataFrame({
            'column': missing.index,
            'missing': missing.values,
            'pct_missing': (missing.values / max(len(df), 1) * 100).round(2),
        }),
        'dtypes': pd.DataFrame({'column': df.columns, 'dtype': df.dtypes.astype(str).values}),
    }


# ---------------------------------------------------------- tool contract
@dataclass
class ToolResult:
    """Every analytical function returns this. The assistant may only narrate it.
    Keep `value` to plain Python types (numbers, strings, dicts, lists)."""
    tool: str
    value: object
    definition: str
    scope: dict
    rows_used: int
    caveats: list = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict:
        """The tool contract. `error` only appears when the call failed."""
        out = asdict(self)
        if out['error'] is None:
            out.pop('error')
        return out


def _error(tool: str, message: str, scope: dict | None = None) -> ToolResult:
    return ToolResult(tool=tool, value=None, definition='', scope=scope or {},
                      rows_used=0, caveats=[], error=message)


def _apply_filters(df: pd.DataFrame, filters: dict | None):
    """Validate filters against the LOADED data, then apply them."""
    for col, wanted in (filters or {}).items():
        if col not in df.columns:
            return df, f'Unknown column: {col}'
        wanted_list = list(wanted) if isinstance(wanted, (list, tuple, set)) else [wanted]
        valid = set(df[col].dropna().unique())
        bad = [w for w in wanted_list if w not in valid]
        if bad:
            examples = sorted(map(str, valid))[:20]
            return df, f'{bad} not found in column {col}. Valid values include: {examples}'
        df = df[df[col].isin(wanted_list)]
    return df, None


# ------------------------------------------------------ analytical tools
def get_total(df: pd.DataFrame, value_col: str, filters: dict | None = None) -> ToolResult:
    """Sum of one numeric column, optionally filtered. EXAMPLE tool: replace the
    generic column names with your own definition of revenue."""
    name = 'get_total'
    if value_col not in df.columns:
        return _error(name, f'Unknown column: {value_col}')
    subset, err = _apply_filters(df, filters)
    if err:
        return _error(name, err, {'filters': filters})
    values = pd.to_numeric(subset[value_col], errors='coerce')
    n_bad = int(values.isna().sum())
    caveats = ['This is a sum of the chosen column. Revenue is not profit.']
    if n_bad:
        caveats.append(f'{n_bad} row(s) had a missing or non-numeric {value_col} and were excluded.')
    return ToolResult(tool=name, value=float(values.sum()), definition=f'Sum of {value_col}',
                      scope={'filters': filters or 'none (all rows)'},
                      rows_used=int(values.notna().sum()), caveats=caveats)


def group_total(df: pd.DataFrame, value_col: str, group_col: str,
                filters: dict | None = None) -> ToolResult:
    """Sum of one numeric column by group (store, channel, year, ...)."""
    name = 'group_total'
    for col in (value_col, group_col):
        if col not in df.columns:
            return _error(name, f'Unknown column: {col}')
    subset, err = _apply_filters(df, filters)
    if err:
        return _error(name, err, {'filters': filters})
    work = subset.assign(**{value_col: pd.to_numeric(subset[value_col], errors='coerce')})
    n_bad = int(work[value_col].isna().sum())
    n_nogroup = int(work[group_col].isna().sum())
    grouped = work.dropna(subset=[value_col, group_col]).groupby(group_col)[value_col].sum()
    caveats = ['Sums of the chosen column. Revenue is not profit.']
    if n_bad:
        caveats.append(f'{n_bad} row(s) with missing or non-numeric {value_col} were excluded.')
    if n_nogroup:
        caveats.append(f'{n_nogroup} row(s) with a missing {group_col} were excluded.')
    return ToolResult(tool=name, value={str(k): float(v) for k, v in grouped.items()},
                      definition=f'Sum of {value_col} by {group_col}',
                      scope={'filters': filters or 'none (all rows)'},
                      rows_used=int(len(work) - n_bad - n_nogroup), caveats=caveats)


def data_quality_report(df: pd.DataFrame) -> ToolResult:
    """Row count, duplicate rows, and missing values per column."""
    missing = {c: int(n) for c, n in df.isna().sum().items() if n > 0}
    caveats = []
    if missing:
        caveats.append('Missing values may limit conclusions drawn from those columns.')
    return ToolResult(tool='data_quality_report',
                      value={'rows': len(df), 'duplicate_rows': int(df.duplicated().sum()),
                             'columns_with_missing': missing},
                      definition='Counts of duplicate rows and missing values in the loaded table',
                      scope={'year': 'all years', 'store': 'all stores', 'channel': 'all channels'},
                      rows_used=len(df), caveats=caveats)


# ------------------------------------------------------------ merge check
def check_merge(left: pd.DataFrame, right: pd.DataFrame, on, value_col: str, how: str = 'left',
                name: str = ''):
    """Merge, and report whether the join changed the row count or the revenue sum.
    `value_col` must be a numeric column in `left`. Also counts left rows that found no
    match (their lookup columns will be blank). Every call is logged. Returns (merged, report)."""
    before_rows = len(left)
    before_sum = float(pd.to_numeric(left[value_col], errors='coerce').sum())
    merged = left.merge(right, on=on, how=how, indicator=True)
    unmatched = int((merged['_merge'] == 'left_only').sum())
    merged = merged.drop(columns='_merge')
    after_rows = len(merged)
    after_sum = float(pd.to_numeric(merged[value_col], errors='coerce').sum())
    fan_out = after_rows != before_rows or abs(after_sum - before_sum) > 1e-6
    warnings = []
    if fan_out:
        warnings.append('The join changed the row count or the total. Check the grain and keys '
                        'of both tables before using this result.')
    if unmatched:
        warnings.append(f'{unmatched} row(s) found no match on {on}; their lookup columns are blank.')
    report = {
        'merge': name or f'on {on}',
        'rows_before': before_rows, 'rows_after': after_rows,
        'sum_before': before_sum, 'sum_after': after_sum,
        'unmatched_rows': unmatched,
        'changed': fan_out,
        'warning': ' '.join(warnings),
    }
    logger.info('merge %s: rows %d -> %d, %s %.2f -> %.2f, unmatched %d',
                report['merge'], before_rows, after_rows, value_col, before_sum, after_sum, unmatched)
    return merged, report


# ------------------------------------------------------------ sales model
def build_sales_model(tables: dict):
    """Join Stores, Categories and Products onto Sales, one checked join at a time.
    Each join is many-to-one on a unique key, so rows and revenue must stay the same.
    Survey Results is NOT joined: it has one row per store per year, and joining it
    would repeat each score on every sales row. Returns (model, merge_reports)."""
    steps = [
        ('Sales → Stores', tables['stores'].rename(columns={'ID': 'Store ID'}), 'Store ID'),
        ('Sales → Categories', tables['categories'], 'Category ID'),
        ('Sales → Products', tables['products'], 'Product ID'),
    ]
    model, reports = tables['sales'], []
    for name, lookup, key in steps:
        model, report = check_merge(model, lookup, on=key, value_col='Revenue', name=name)
        reports.append(report)
    return model, reports


def build_dataset(uploads) -> dict:
    """`uploads` is an iterable of (role, filename, file_bytes). Loads, validates and
    cleans each table, then builds the joined sales model. Pure function (no Streamlit),
    so the app can cache it on the file contents. 'errors' holds messages for the UI."""
    uploads = list(uploads)
    tables, sources, errors = {}, {}, []
    for role, filename, file_bytes in uploads:
        df, err = load_role_table(file_bytes, filename, role)
        if err:
            errors.append(err)
        else:
            tables[role], sources[role] = df, filename
    # An Access database holds several tables (Sales, Categories, Products), so one
    # upload can fill every role that was not uploaded separately.
    access_files = [(f, b) for _, f, b in uploads if f.lower().endswith(ACCESS_EXT)]
    for role in REQUIRED_COLUMNS:
        for filename, file_bytes in access_files:
            if role in tables:
                break
            df, err = load_role_table(file_bytes, filename, role)
            if not err:
                tables[role], sources[role] = df, f'{filename} (Access table)'
    for role in REQUIRED_TABLES:
        if role not in tables and not errors:
            errors.append(f'No {TABLE_INFO[role][0]} table was found. Upload it, or upload '
                          'an Access database that contains it.')
    result = {'tables': tables, 'sources': sources, 'errors': errors}
    if errors:
        return result
    tables['sales'], result['sales_issues'] = clean_sales(tables['sales'])
    if 'survey' in tables:
        tables['survey'] = clean_survey(tables['survey'])
    result['model'], result['merge_reports'] = build_sales_model(tables)
    return result


def revenue_summary(sales: pd.DataFrame) -> dict:
    """Total revenue and revenue by year from the cleaned Sales table.
    Rows with blank revenue are excluded from the sum and counted."""
    revenue = sales['Revenue']
    by_year = sales.dropna(subset=['Year', 'Revenue']).groupby('Year')['Revenue'].sum()
    return {
        'total': float(revenue.sum()),
        'rows_used': int(revenue.notna().sum()),
        'rows_excluded': int(revenue.isna().sum()),
        'by_year': {int(y): float(v) for y, v in by_year.items()},
    }


# --------------------------------------------------------------- dashboard
MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
               'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


def store_label(store_id: str, city, location) -> str:
    """Readable store name for filters and charts, e.g. 'SH01 · Orlando (South)'."""
    if pd.isna(city) or not str(city).strip():
        return f'{store_id} · {location}'  # the website has no city
    return f'{store_id} · {city} ({location})'


def filter_options(model: pd.DataFrame) -> dict:
    """Every filter list comes from the loaded data, never from hardcoded values.
    Stores are returned as {store_id: label} so the filter can show names."""
    stores = model.drop_duplicates('Store ID').sort_values('Store ID')
    return {
        'years': sorted(int(y) for y in model['Year'].dropna().unique()),
        'channels': sorted(model['StoreType'].dropna().unique()),
        'stores': {s: store_label(s, c, loc) for s, c, loc in
                   zip(stores['Store ID'], stores['City'], stores['Location'])},
        'categories': sorted(model['Category'].dropna().unique()),
    }


def filter_sales(model: pd.DataFrame, years=None, channels=None, stores=None,
                 categories=None) -> pd.DataFrame:
    """Rows matching every filter. An empty or None filter means 'all'.
    Returns a filtered view; the loaded data is never changed."""
    mask = pd.Series(True, index=model.index)
    for col, wanted in (('Year', years), ('StoreType', channels),
                        ('Store ID', stores), ('Category', categories)):
        if wanted:
            mask &= model[col].isin(list(wanted))
    return model[mask]


def revenue_by_year_channel(df: pd.DataFrame) -> pd.DataFrame:
    """Revenue for each year and channel (StoreType): one row per year-channel pair."""
    return (df.dropna(subset=['Year', 'Revenue'])
              .groupby(['Year', 'StoreType'], as_index=False)['Revenue'].sum()
              .sort_values(['Year', 'StoreType']))


def category_breakdown(df: pd.DataFrame) -> pd.DataFrame:
    """Revenue and share of the filtered total for each category, largest first (dashboard)."""
    out = df.groupby('Category', as_index=False)['Revenue'].sum()
    total = out['Revenue'].sum()
    out['Share'] = out['Revenue'] / total if total else 0.0
    return out.sort_values('Revenue', ascending=False).reset_index(drop=True)


def revenue_by_month(df: pd.DataFrame) -> pd.DataFrame:
    """Revenue for each calendar month, summed over the selected years, with its share
    of the filtered total. Shows seasonality. All 12 months are listed, even if zero."""
    by_month = (df.dropna(subset=['Date', 'Revenue'])
                  .groupby(df['Date'].dt.month)['Revenue'].sum()
                  .reindex(range(1, 13), fill_value=0.0))
    total = by_month.sum()
    return pd.DataFrame({
        'Month': MONTH_NAMES,
        'Revenue': by_month.values,
        'Share': by_month.values / total if total else 0.0,
    })


# --------------------------------------------------------- assistant tools
# Each tool takes explicit year / channel / store arguments (never Streamlit state),
# checks them against the loaded data, and returns a ToolResult. None of them change
# the data they are given.
NOT_PROFIT = 'Revenue is not profit: the data has no costs or margins.'


def _resolve_scope(model: pd.DataFrame, year=None, channel=None, store=None):
    """Validate year, channel and store against the LOADED data and apply them.
    Unknown or ambiguous values return an error naming the valid choices; nothing
    is guessed. Returns (subset, scope, error)."""
    scope = {'year': 'all years', 'store': 'all stores', 'channel': 'all channels'}
    mask = pd.Series(True, index=model.index)
    if year is not None:
        years = sorted(int(y) for y in model['Year'].dropna().unique())
        try:
            year = int(str(year).strip())
        except ValueError:
            return None, scope, f'"{year}" is not a year. Available years: {years}.'
        if year not in years:
            return None, scope, f'There are no sales for {year}. Available years: {years}.'
        mask &= model['Year'] == year
        scope['year'] = year
    if channel is not None:
        channels = sorted(model['StoreType'].dropna().unique())
        match = [c for c in channels if c.lower() == str(channel).strip().lower()]
        if not match:
            return None, scope, f'Unknown channel "{channel}". Valid channels: {channels}.'
        mask &= model['StoreType'] == match[0]
        scope['channel'] = match[0]
    if store is not None:
        stores = model.drop_duplicates('Store ID')
        wanted = str(store).strip().lower()
        hits = stores[(stores['Store ID'].str.lower() == wanted)
                      | (stores['City'].fillna('').str.lower() == wanted)]
        labels = [store_label(s, c, loc) for s, c, loc in
                  zip(hits['Store ID'], hits['City'], hits['Location'])]
        if hits.empty:
            return None, scope, (f'Unknown store "{store}". Use a store ID or a city, e.g. '
                                 f'{sorted(stores["Store ID"])[:5]}.')
        if len(hits) > 1:
            return None, scope, (f'"{store}" matches {len(hits)} stores: {", ".join(labels)}. '
                                 'Ask again using the store ID.')
        mask &= model['Store ID'] == hits['Store ID'].iloc[0]
        scope['store'] = labels[0]
    subset = model[mask]
    if subset.empty:
        return None, scope, 'No sales match this combination of year, channel and store.'
    return subset, scope, None


def _caveats(subset: pd.DataFrame, *extra: str) -> list:
    caveats = [NOT_PROFIT, *extra]
    n_blank = int(subset['Revenue'].isna().sum())
    if n_blank:
        caveats.append(f'{n_blank} sales row(s) with blank revenue were left out.')
    return caveats


def _shares(revenue: pd.Series) -> dict:
    """{name: {'revenue': x, 'share': x / total}} in the series' order."""
    total = revenue.sum()
    return {str(k): {'revenue': round(float(v), 2),
                     'share': round(float(v / total), 4) if total else None}
            for k, v in revenue.items()}


def total_revenue(model: pd.DataFrame, year=None, channel=None, store=None) -> ToolResult:
    """Total revenue in scope, and its share of all loaded revenue."""
    name = 'total_revenue'
    subset, scope, err = _resolve_scope(model, year, channel, store)
    if err:
        return _error(name, err, scope)
    revenue = float(subset['Revenue'].sum())
    everything = float(model['Revenue'].sum())
    return ToolResult(
        tool=name,
        value={'revenue': round(revenue, 2),
               'share_of_all_revenue': round(revenue / everything, 4) if everything else None},
        definition=('Sum of Revenue over the sales rows in scope. Share = that sum divided '
                    'by the revenue of all loaded sales.'),
        scope=scope, rows_used=int(subset['Revenue'].notna().sum()), caveats=_caveats(subset))


def revenue_by_year(model: pd.DataFrame, channel=None, store=None) -> ToolResult:
    """Revenue for each year, and the % change from the first to the last year."""
    name = 'revenue_by_year'
    subset, scope, err = _resolve_scope(model, None, channel, store)
    if err:
        return _error(name, err, scope)
    by_year = subset.dropna(subset=['Year']).groupby('Year')['Revenue'].sum().sort_index()
    first, last = by_year.iloc[0], by_year.iloc[-1]
    return ToolResult(
        tool=name,
        value={'by_year': {str(int(y)): round(float(v), 2) for y, v in by_year.items()},
               'change_first_to_last_pct': round(float((last - first) / first * 100), 2)
               if first and len(by_year) > 1 else None},
        definition=('Sum of Revenue for each calendar year of the sale date. Change = '
                    '(last year - first year) / first year x 100.'),
        scope=scope, rows_used=int(subset['Revenue'].notna().sum()),
        caveats=_caveats(subset, 'A change over time shows what happened, not why it happened.'))


def revenue_by_category(model: pd.DataFrame, year=None, channel=None, store=None) -> ToolResult:
    """Revenue and share of the scope's revenue for each product category, largest first."""
    name = 'revenue_by_category'
    subset, scope, err = _resolve_scope(model, year, channel, store)
    if err:
        return _error(name, err, scope)
    by_cat = subset.groupby('Category')['Revenue'].sum().sort_values(ascending=False)
    return ToolResult(
        tool=name, value={'by_category': _shares(by_cat)},
        definition='Sum of Revenue for each product category; share = category revenue / revenue in scope.',
        scope=scope, rows_used=int(subset['Revenue'].notna().sum()), caveats=_caveats(subset))


def revenue_by_channel(model: pd.DataFrame, year=None, store=None) -> ToolResult:
    """Revenue and share for each channel (Website vs Brick & Mortar)."""
    name = 'revenue_by_channel'
    subset, scope, err = _resolve_scope(model, year, None, store)
    if err:
        return _error(name, err, scope)
    by_channel = subset.groupby('StoreType')['Revenue'].sum().sort_values(ascending=False)
    return ToolResult(
        tool=name, value={'by_channel': _shares(by_channel)},
        definition='Sum of Revenue for each channel; share = channel revenue / revenue in scope.',
        scope=scope, rows_used=int(subset['Revenue'].notna().sum()),
        caveats=_caveats(subset, "Channel is the store's StoreType in the Stores table "
                                 '(the website is one "store").'))


def top_stores(model: pd.DataFrame, year=None, channel=None, n=5) -> ToolResult:
    """The n stores with the most revenue in scope, with each one's share."""
    name = 'top_stores'
    subset, scope, err = _resolve_scope(model, year, channel, None)
    if err:
        return _error(name, err, scope)
    n_stores = subset['Store ID'].nunique()
    try:
        n = int(n)
    except (TypeError, ValueError):
        return _error(name, f'"{n}" is not a number of stores.', scope)
    if not 1 <= n <= n_stores:
        return _error(name, f'n must be between 1 and {n_stores}.', scope)
    labels = {s: store_label(s, c, loc) for s, c, loc in
              zip(subset['Store ID'], subset['City'], subset['Location'])}
    by_store = subset.groupby('Store ID')['Revenue'].sum().sort_values(ascending=False)
    total = by_store.sum()
    ranking = [{'rank': i + 1, 'store': labels[s], 'revenue': round(float(v), 2),
                'share': round(float(v / total), 4) if total else None}
               for i, (s, v) in enumerate(by_store.head(n).items())]
    return ToolResult(
        tool=name, value={'stores': ranking, 'stores_in_scope': int(n_stores)},
        definition=('Sum of Revenue for each store, ranked highest first; share = store '
                    'revenue / revenue of all stores in scope.'),
        scope=scope, rows_used=int(subset['Revenue'].notna().sum()), caveats=_caveats(subset))


def _resolve_years(model: pd.DataFrame, start_year=None, end_year=None):
    """Default to the first and last year in the loaded data; reject unknown years."""
    years = sorted(int(y) for y in model['Year'].dropna().unique())
    try:
        start = years[0] if start_year is None else int(str(start_year).strip())
        end = years[-1] if end_year is None else int(str(end_year).strip())
    except ValueError:
        return None, None, f'Years must be numbers. Available years: {years}.'
    for y in (start, end):
        if y not in years:
            return None, None, f'There are no sales for {y}. Available years: {years}.'
    if start >= end:
        return None, None, f'The start year ({start}) must be before the end year ({end}).'
    return start, end, None


def _change_rows(subset: pd.DataFrame, key: str, start: int, end: int, label=str) -> list:
    """Revenue in the start and end year for each value of `key`, with the change."""
    pivot = (subset[subset['Year'].isin([start, end])]
             .pivot_table(index=key, columns='Year', values='Revenue', aggfunc='sum'))
    rows = []
    for k, r in pivot.iterrows():
        before, after = r.get(start), r.get(end)
        if pd.isna(before) or pd.isna(after) or before == 0:
            continue  # only compare names present (and non-zero) in both years
        rows.append({'name': label(k), 'start_revenue': round(float(before), 2),
                     'end_revenue': round(float(after), 2),
                     'change': round(float(after - before), 2),
                     'change_pct': round(float((after - before) / before * 100), 2)})
    return sorted(rows, key=lambda x: x['change_pct'])


def revenue_change_by_store(model: pd.DataFrame, start_year=None, end_year=None,
                            channel=None) -> ToolResult:
    """Each store's revenue change between two years, with the largest decline and growth."""
    name = 'revenue_change_by_store'
    subset, scope, err = _resolve_scope(model, None, channel, None)
    if err:
        return _error(name, err, scope)
    start, end, err = _resolve_years(model, start_year, end_year)
    if err:
        return _error(name, err, scope)
    scope['year'] = f'{start} vs {end}'
    labels = {s: store_label(s, c, loc) for s, c, loc in
              zip(subset['Store ID'], subset['City'], subset['Location'])}
    rows = _change_rows(subset, 'Store ID', start, end, label=lambda s: labels[s])
    declined = [r for r in rows if r['change'] < 0]
    caveats = _caveats(subset, 'A change over time shows what happened, not why it happened.')
    if not declined:
        caveats.append(f'No store had lower revenue in {end} than in {start}.')
    missing = subset['Store ID'].nunique() - len(rows)
    if missing:
        caveats.append(f'{missing} store(s) without revenue in both years were left out.')
    return ToolResult(
        tool=name,
        value={'start_year': start, 'end_year': end, 'stores': rows,
               'largest_decline': declined[0] if declined else None,
               'largest_growth': rows[-1] if rows and rows[-1]['change'] > 0 else None,
               'stores_declined': len(declined), 'stores_compared': len(rows)},
        definition=('For each store: revenue in the end year minus revenue in the start year; '
                    'change % = that difference / start-year revenue x 100.'),
        scope=scope, rows_used=int(subset['Year'].isin([start, end]).sum()), caveats=caveats)


def revenue_change_by_channel(model: pd.DataFrame, start_year=None, end_year=None,
                              store=None) -> ToolResult:
    """Each channel's revenue change between two years, and which grew the most."""
    name = 'revenue_change_by_channel'
    subset, scope, err = _resolve_scope(model, None, None, store)
    if err:
        return _error(name, err, scope)
    start, end, err = _resolve_years(model, start_year, end_year)
    if err:
        return _error(name, err, scope)
    scope['year'] = f'{start} vs {end}'
    rows = _change_rows(subset, 'StoreType', start, end)
    return ToolResult(
        tool=name,
        value={'start_year': start, 'end_year': end, 'channels': rows,
               'largest_growth': rows[-1] if rows else None},
        definition=('For each channel (StoreType): revenue in the end year minus revenue in the '
                    'start year; change % = that difference / start-year revenue x 100.'),
        scope=scope, rows_used=int(subset['Year'].isin([start, end]).sum()),
        caveats=_caveats(subset, 'A change over time shows what happened, not why it happened.'))


def satisfaction_vs_revenue(model: pd.DataFrame, survey: pd.DataFrame = None,
                            year=None) -> ToolResult:
    """How store revenue and survey satisfaction move together, one point per store-year."""
    name = 'satisfaction_vs_revenue'
    if survey is None:
        return _error(name, 'The Survey Results file is not loaded, so satisfaction cannot be '
                            'analyzed. Upload it in the sidebar.')
    subset, scope, err = _resolve_scope(model, year, None, None)
    if err:
        return _error(name, err, scope)
    per_store_year = subset.groupby(['Store ID', 'Year'], as_index=False)['Revenue'].sum()
    scores = survey[['Store ID', 'Year', 'Satisfaction']].dropna(subset=['Year'])
    joined = per_store_year.merge(scores, on=['Store ID', 'Year'], how='inner')
    blank = int(joined['Satisfaction'].isna().sum())
    pairs = joined.dropna(subset=['Satisfaction'])
    if len(pairs) < 3:
        return _error(name, 'Fewer than three store-years have both revenue and a satisfaction '
                            'score, so no relationship can be measured.', scope)
    r = float(pairs['Satisfaction'].corr(pairs['Revenue']))
    caveats = _caveats(subset,
                       'Correlation is not causation: other factors (store size, market, '
                       'location) can drive both satisfaction and revenue.',
                       'Satisfaction is one annual survey score per store; it is not a '
                       'measure of every customer.')
    if blank:
        caveats.append(f'{blank} store-year(s) had no satisfaction score (for example, the '
                       'website is not surveyed) and were left out, not counted as zero.')
    return ToolResult(
        tool=name,
        value={'correlation': round(r, 3), 'store_years_used': int(len(pairs)),
               'store_years_without_score': blank,
               'average_satisfaction': round(float(pairs['Satisfaction'].mean()), 2)},
        definition=('Pearson correlation between each store-year\'s total revenue and its '
                    'satisfaction score (0-5 scale), using only store-years with a score.'),
        scope=scope, rows_used=int(len(subset)), caveats=caveats)


def grain_table(tables: dict, sources: dict) -> pd.DataFrame:
    """The grain statement for every loaded table, built from the data itself."""
    rows = []
    for role, df in tables.items():
        label, grain, key = TABLE_INFO[role]
        rows.append({
            'Table': label,
            'Source': sources.get(role, ''),
            'One row =': grain,
            'Key column(s)': key,
            'Row count': len(df),
            'Key unique?': 'Yes' if df[key].notna().all() and df[key].is_unique else 'No',
            'Contains revenue?': 'Yes' if 'Revenue' in df.columns else 'No',
        })
    return pd.DataFrame(rows)
