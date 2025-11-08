#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Backup and replace pages with new versions
"""

import os
import shutil
from pathlib import Path

# Page mappings: old filename -> new filename
PAGE_MAPPINGS = {
    'index.html': 'index_new.html',
    'dashboard.html': 'dashboard_new.html',
    'contract_trading.html': 'contract_trading_new.html',
    'strategies.html': 'strategies_new.html',
    'etf_data.html': 'etf_data_new.html',
    'corporate_treasury.html': 'corporate_treasury_new.html',
}

TEMPLATES_DIR = Path('templates')
BACKUP_DIR = TEMPLATES_DIR / 'backup'

def backup_and_replace():
    """Backup and replace page files"""

    # Create backup directory
    BACKUP_DIR.mkdir(exist_ok=True)
    print(f"[OK] Backup directory created: {BACKUP_DIR}")

    # Process each page
    for old_file, new_file in PAGE_MAPPINGS.items():
        old_path = TEMPLATES_DIR / old_file
        new_path = TEMPLATES_DIR / new_file
        backup_path = BACKUP_DIR / old_file

        # Check if new file exists
        if not new_path.exists():
            print(f"[SKIP] {old_file}: New file {new_file} does not exist")
            continue

        # Backup original file (if exists)
        if old_path.exists():
            shutil.copy2(old_path, backup_path)
            print(f"[BACKUP] {old_file} -> backup/{old_file}")
        else:
            print(f"[INFO] Original file {old_file} does not exist, no backup needed")

        # Replace file
        shutil.copy2(new_path, old_path)
        print(f"[REPLACE] {old_file} (using {new_file})")

    print("\n" + "="*60)
    print("[COMPLETE] All pages replaced!")
    print("="*60)
    print("\nReplaced pages:")
    for old_file in PAGE_MAPPINGS.keys():
        print(f"  - {old_file}")

    print(f"\nOriginal files backed up to: {BACKUP_DIR}")
    print("\nTo restore, copy files back from backup/ directory")

if __name__ == '__main__':
    print("\n" + "="*60)
    print(" Backup and Replace Pages with New Versions")
    print("="*60)
    print("\nWill replace the following pages:")
    print("  - index.html")
    print("  - dashboard.html")
    print("  - contract_trading.html")
    print("  - strategies.html")
    print("  - etf_data.html")
    print("  - corporate_treasury.html")
    print("\nOriginal files will be backed up to templates/backup/")
    print("="*60 + "\n")

    backup_and_replace()
