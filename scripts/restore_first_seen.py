"""
Name: Restore First Seen Dates
Description: Restores the 'first_detected' dates from the backup JSON file. Run this after repopulating the database.
Author: Assistant
Version: 1.0
"""

import sqlite3
import json
import os
import sys

# Add parent directory to path to import database
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import get_db

BACKUP_FILE = "backup_first_detected.json"

def restore_first_seen():
    """Restores first_detected dates from JSON file."""
    print(f"Starting restore of first_detected dates from {BACKUP_FILE}...")
    
    if not os.path.exists(BACKUP_FILE):
        print(f"Error: Backup file {BACKUP_FILE} not found.")
        return False
        
    try:
        with open(BACKUP_FILE, 'r') as f:
            backup_data = json.load(f)
            
        print(f"Loaded {len(backup_data)} records from backup.")
        
        db = get_db()
        with db.get_connection() as conn:
            cursor = conn.cursor()
            
            updated_count = 0
            not_found_count = 0
            
            for item in backup_data:
                artist = item["artist"]
                album = item["album"]
                first_detected = item["first_detected"]
                
                # Check if album exists in current DB
                cursor.execute("SELECT id FROM expiring_albums WHERE artist = ? AND album = ?", (artist, album))
                result = cursor.fetchone()
                
                if result:
                    cursor.execute(
                        "UPDATE expiring_albums SET first_detected = ? WHERE artist = ? AND album = ?",
                        (first_detected, artist, album)
                    )
                    updated_count += 1
                else:
                    # print(f"Warning: Album not found in current DB: {artist} - {album}")
                    not_found_count += 1
                    
            conn.commit()
            print(f"Restore complete.")
            print(f"Updated: {updated_count}")
            print(f"Not Found (skipped): {not_found_count}")
            
            return True

    except Exception as e:
        print(f"Error during restore: {e}")
        return False

if __name__ == "__main__":
    success = restore_first_seen()
    if not success:
        sys.exit(1)
