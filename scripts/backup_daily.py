#!/usr/bin/env python3
"""
YS TRADING — DAILY BACKUP SCRIPT
File location in your repo: scripts/backup_daily.py
Runs via GitHub Actions at 4:00 PM IST daily (Mon-Fri)
Downloads today's data from Supabase and saves as JSON files
"""

import os
import json
import requests
from datetime import datetime, timezone, timedelta

# ── CONFIG ────────────────────────────────────────────────────
SUPABASE_URL = os.environ.get('SUPABASE_URL', 'https://gshrxfkqlfktnjnwxlot.supabase.co')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '')

# IST timezone
IST = timezone(timedelta(hours=5, minutes=30))
today = datetime.now(IST).strftime('%Y-%m-%d')
year_month = datetime.now(IST).strftime('%Y-%m')

print(f"YS Trading Backup — {today}")
print(f"Supabase URL: {SUPABASE_URL}")

# ── SUPABASE QUERY HELPER ──────────────────────────────────────
def query_supabase(table: str, filters: dict = None, select: str = '*') -> list:
    """Query a Supabase table and return rows as list."""
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
        'Content-Type': 'application/json',
        'Prefer': 'return=representation'
    }
    params = {'select': select}
    if filters:
        params.update(filters)

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"  ERROR querying {table}: {e}")
        return []

def save_json(data: any, filepath: str):
    """Save data as pretty-printed JSON."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    size = os.path.getsize(filepath)
    print(f"  Saved: {filepath} ({size:,} bytes, {len(data) if isinstance(data, list) else 1} records)")

# ── NSE HOLIDAY CHECK ──────────────────────────────────────────
NSE_HOLIDAYS = {
    '2025-01-26','2025-02-26','2025-03-14','2025-03-31',
    '2025-04-10','2025-04-14','2025-04-18','2025-05-01',
    '2025-08-15','2025-08-27','2025-10-02','2025-10-21',
    '2025-10-22','2025-11-05','2025-12-25',
    '2026-01-26','2026-03-20','2026-04-02','2026-04-03',
    '2026-04-14','2026-05-01','2026-08-15','2026-10-02',
    '2026-11-14','2026-12-25'
}

dow = datetime.now(IST).weekday()  # 0=Mon, 6=Sun
is_weekend = dow >= 5
is_holiday = today in NSE_HOLIDAYS
is_trading_day = not is_weekend and not is_holiday

print(f"Day of week: {['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][dow]}")
print(f"Is trading day: {is_trading_day}")

# ── BACKUP SECTION 1: TODAY'S SCAN DATA ───────────────────────
print("\n── Backing up scan data ──")

# 1a. Scan runs (every 5-min result)
scan_runs = query_supabase('scan_runs', {'trade_date': f'eq.{today}'})
save_json(scan_runs, f'data/{year_month}/{today}/scan_runs.json')

# 1b. Daily frequency (scored stocks)
daily_freq = query_supabase('daily_frequency', {'trade_date': f'eq.{today}'})
save_json(daily_freq, f'data/{year_month}/{today}/daily_frequency.json')

# 1c. NSE live data (gainers/losers/active)
nse_live = query_supabase('nse_live', {'trade_date': f'eq.{today}'})
save_json(nse_live, f'data/{year_month}/{today}/nse_live.json')

# 1d. Bhavcopy
bhavcopy = query_supabase('bhavcopy', {'trade_date': f'eq.{today}'})
save_json(bhavcopy, f'data/{year_month}/{today}/bhavcopy.json')

# ── BACKUP SECTION 2: TODAY'S JOURNAL ────────────────────────
print("\n── Backing up journal ──")

journal_today = query_supabase('trade_journal', {'trade_date': f'eq.{today}'})
save_json(journal_today, f'data/{year_month}/{today}/journal.json')

# ── BACKUP SECTION 3: ALL JOURNAL (full history) ─────────────
print("\n── Backing up full journal history ──")

all_trades = query_supabase('trade_journal', {
    'order': 'trade_date.desc'
})
save_json(all_trades, f'data/journal_all_time.json')

# ── BACKUP SECTION 4: MONTHLY SUMMARY ────────────────────────
print("\n── Computing monthly summary ──")

# Get this month's trades
this_month_start = datetime.now(IST).strftime('%Y-%m-01')
month_trades = query_supabase('trade_journal', {
    'trade_date': f'gte.{this_month_start}',
    'order': 'trade_date.asc'
})

if month_trades:
    wins = [t for t in month_trades if t.get('result') == 'Win']
    losses = [t for t in month_trades if t.get('result') == 'Loss']
    total_pnl = sum(float(t.get('pnl', 0)) for t in month_trades)
    win_pnl = sum(float(t.get('pnl', 0)) for t in wins)
    loss_pnl = sum(abs(float(t.get('pnl', 0))) for t in losses)
    avg_win = win_pnl / len(wins) if wins else 0
    avg_loss = loss_pnl / len(losses) if losses else 0
    win_rate = len(wins) / len(month_trades) if month_trades else 0
    loss_rate = 1 - win_rate
    expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)
    profit_factor = (len(wins) * avg_win) / (len(losses) * avg_loss) if losses and avg_loss > 0 else 0

    monthly_summary = {
        'period': year_month,
        'generated_at': datetime.now(IST).isoformat(),
        'total_trades': len(month_trades),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate_pct': round(win_rate * 100, 1),
        'total_pnl': round(total_pnl, 2),
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'expectancy': round(expectancy, 2),
        'profit_factor': round(profit_factor, 2),
        'trades': month_trades
    }

    save_json(monthly_summary, f'data/{year_month}/monthly_summary.json')
    print(f"  Monthly stats: {len(month_trades)} trades | Win rate: {win_rate*100:.1f}% | P&L: ₹{total_pnl:.0f} | Expectancy: ₹{expectancy:.0f}")
else:
    print("  No trades this month yet")

# ── BACKUP SECTION 5: BEST PICKS SNAPSHOT ────────────────────
print("\n── Saving best picks snapshot ──")

# Top 10 bull + top 10 bear by score today
bull_picks = query_supabase('daily_frequency', {
    'trade_date': f'eq.{today}',
    'scanner': 'eq.1',
    'order': 'score.desc',
    'limit': '10'
})
bear_picks = query_supabase('daily_frequency', {
    'trade_date': f'eq.{today}',
    'scanner': 'eq.2',
    'order': 'score.desc',
    'limit': '10'
})

best_picks_snapshot = {
    'date': today,
    'generated_at': datetime.now(IST).isoformat(),
    'bull_top10': bull_picks,
    'bear_top10': bear_picks,
    'scan_count_s1': len([s for s in scan_runs if s.get('scanner') == 1]),
    'scan_count_s2': len([s for s in scan_runs if s.get('scanner') == 2]),
}
save_json(best_picks_snapshot, f'data/{year_month}/{today}/best_picks.json')

# ── BACKUP SECTION 6: INDEX FILE ──────────────────────────────
print("\n── Updating backup index ──")

# Load existing index or create new
index_path = 'data/backup_index.json'
try:
    with open(index_path, 'r') as f:
        index = json.load(f)
except:
    index = {'backups': [], 'last_updated': ''}

# Add today's entry
today_entry = {
    'date': today,
    'is_trading_day': is_trading_day,
    'scan_runs': len(scan_runs),
    'unique_stocks_s1': len(set(s['symbol'] for s in daily_freq if s.get('scanner') == 1)),
    'unique_stocks_s2': len(set(s['symbol'] for s in daily_freq if s.get('scanner') == 2)),
    'trades_today': len(journal_today),
    'day_pnl': round(sum(float(t.get('pnl', 0)) for t in journal_today), 2),
    'backed_up_at': datetime.now(IST).isoformat()
}

# Update or add entry
existing = [b for b in index['backups'] if b['date'] != today]
existing.append(today_entry)
existing.sort(key=lambda x: x['date'], reverse=True)
index['backups'] = existing[:90]  # Keep last 90 days
index['last_updated'] = datetime.now(IST).isoformat()
index['total_backups'] = len(existing)

save_json(index, index_path)

# ── SUMMARY ───────────────────────────────────────────────────
print("\n" + "═" * 50)
print("BACKUP COMPLETE")
print(f"  Date:         {today}")
print(f"  Scan runs:    {len(scan_runs)}")
print(f"  Freq stocks:  {len(daily_freq)}")
print(f"  NSE live:     {len(nse_live)}")
print(f"  Trades today: {len(journal_today)}")
print(f"  All trades:   {len(all_trades)}")
if journal_today:
    day_pnl = sum(float(t.get('pnl', 0)) for t in journal_today)
    print(f"  Today P&L:    ₹{day_pnl:.0f}")
print("═" * 50)
