"""
Name: Backup First Seen Dates
Description: Backs up the 'first_detected' dates for all albums in the database to a JSON file. Use this before wiping the database.
Author: Assistant
Version: 1.0
"""

import sqlite3
import json
import os
import sys
from datetime import datetime

# Add parent directory to path to import database
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import get_db

BACKUP_FILE = "backup_first_detected.json"

def backup_first_seen():
    """Backs up first_detected dates to a JSON file."""
    print(f"Starting backup of first_detected dates...")
    
    try:
        db = get_db()
        with db.get_connection() as conn:
            cursor = conn.cursor()
            
            # Check if table exists
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='expiring_albums'")
            if not cursor.fetchone():
                print("Error: Table 'expiring_albums' does not exist.")
                return False

            # Select data
            cursor.execute("SELECT artist, album, first_detected FROM expiring_albums")
            rows = cursor.fetchall()
            
            backup_data = []
            for row in rows:
                backup_data.append({
                    "artist": row["artist"],
                    "album": row["album"],
                    "first_detected": row["first_detected"]
                })
            
            print(f"Found {len(backup_data)} records to backup.")
            
            # Save to JSON
            with open(BACKUP_FILE, 'w') as f:
                json.dump(backup_data, f, indent=4)
                
            print(f"Successfully backed up {len(backup_data)} records to {BACKUP_FILE}")
            return True

    except Exception as e:
        print(f"Error during backup: {e}")
        return False

if __name__ == "__main__":
    success = backup_first_seen()
    if not success:
        sys.exit(1)
