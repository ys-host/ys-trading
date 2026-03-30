# ═══════════════════════════════════════════════════════════════
# YS TRADING — GITHUB ACTIONS: Daily 4 PM IST Backup
# File location in your repo: .github/workflows/daily-backup.yml
# Runs daily at 4:00 PM IST = 10:30 UTC, Mon-Fri
# Downloads data from Supabase → saves as JSON → commits to repo
# ═══════════════════════════════════════════════════════════════

name: YS Trading Daily Backup

on:
  schedule:
    # 4:00 PM IST = 10:30 UTC, Mon-Fri
    - cron: '30 10 * * 1-5'
  # Allow manual trigger from GitHub Actions tab
  workflow_dispatch:

jobs:
  backup:
    runs-on: ubuntu-latest
    timeout-minutes: 15

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install requests

      - name: Run backup script
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_KEY: ${{ secrets.SUPABASE_SERVICE_KEY }}
        run: python scripts/backup_daily.py

      - name: Commit and push backup
        run: |
          git config user.email "backup@ys-trading.com"
          git config user.name "YS Trading Backup Bot"
          git add data/
          git diff --staged --quiet || git commit -m "Backup: $(date -u '+%Y-%m-%d') 4PM IST data"
          git push
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
