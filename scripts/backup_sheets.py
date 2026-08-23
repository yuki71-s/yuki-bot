#!/usr/bin/env python3
"""Backup Google Sheets to CSV files on VPS."""
import os
import sys
import json
import csv
import base64
from datetime import datetime, timedelta
from pathlib import Path

import gspread

# Config
SHEET_ID = os.getenv("SHEET_ID", "")
SERVICE_ACCOUNT_JSON = os.getenv("SERVICE_ACCOUNT_JSON", "")
BACKUP_DIR = Path("/opt/yuki-bot/backups")
KEEP_DAYS = 7

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_sheets_client():
    if SERVICE_ACCOUNT_JSON:
        info = json.loads(base64.b64decode(SERVICE_ACCOUNT_JSON))
        return gspread.service_account_from_dict(info)
    return gspread.service_account(filename="/opt/yuki-bot/service_account.json")


def export_worksheet(ws, filepath: Path):
    """Export worksheet to CSV."""
    records = ws.get_all_records()
    if not records:
        return 0

    headers = list(records[0].keys())
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(records)
    return len(records)


def cleanup_old_backups():
    """Delete backup folders older than KEEP_DAYS."""
    if not BACKUP_DIR.exists():
        return
    cutoff = datetime.now() - timedelta(days=KEEP_DAYS)
    for folder in BACKUP_DIR.iterdir():
        if folder.is_dir():
            try:
                folder_date = datetime.strptime(folder.name, "%Y-%m-%d")
                if folder_date < cutoff:
                    for f in folder.iterdir():
                        f.unlink()
                    folder.rmdir()
                    print(f"  Deleted old backup: {folder.name}")
            except ValueError:
                pass


def main():
    if not SHEET_ID:
        print("ERROR: SHEET_ID not set")
        sys.exit(1)

    today = datetime.now().strftime("%Y-%m-%d")
    backup_path = BACKUP_DIR / today
    backup_path.mkdir(parents=True, exist_ok=True)

    print(f"Backup started: {today}")

    try:
        gc = get_sheets_client()
        sh = gc.open_by_key(SHEET_ID)

        worksheets = {
            "YukiHistory": "YukiHistory.csv",
            "YukiProfile": "YukiProfile.csv",
            "YukiMemory": "YukiMemory.csv",
        }

        for ws_name, filename in worksheets.items():
            try:
                ws = sh.worksheet(ws_name)
                count = export_worksheet(ws, backup_path / filename)
                print(f"  {ws_name}: {count} rows exported")
            except Exception as e:
                print(f"  {ws_name}: ERROR — {e}")

        cleanup_old_backups()
        print(f"Backup completed: {backup_path}")

    except Exception as e:
        print(f"Backup failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
