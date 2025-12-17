"""
Name: Wipe Database
Description: COMPLETELY WIPES the 'expiring_albums' and 'album_tracks' tables. Use with caution! Ensure you have a backup.
Author: Assistant
Version: 1.0
"""

import sqlite3
import os
import sys

# Add parent directory to path to import database
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import get_db

def wipe_database():
    """Wipes the database tables."""
    print("WARNING: This will delete ALL data from 'expiring_albums' and 'album_tracks'.")
    print("Are you sure you want to proceed? (This script assumes you know what you are doing)")
    
    # In a script run from UI, we can't easily get user input. 
    # We'll assume if they ran it, they want it. 
    # But maybe we should check for the backup file first as a safety measure?
    
    if not os.path.exists("backup_first_detected.json"):
        print("WARNING: 'backup_first_detected.json' not found!")
        print("It is HIGHLY recommended to run 'Backup First Seen Dates' before wiping.")
        # We won't block it, but we'll warn loudly in the logs.
    
    try:
        db = get_db()
        with db.get_connection() as conn:
            cursor = conn.cursor()
            
            print("Dropping table 'expiring_albums'...")
            cursor.execute("DROP TABLE IF EXISTS expiring_albums")
            
            print("Dropping table 'album_tracks'...")
            cursor.execute("DROP TABLE IF EXISTS album_tracks")
            
            conn.commit()
            print("Tables dropped successfully.")
        
        print("Re-initializing database tables...")
        db.ensure_database_exists()
        print("Database re-initialized.")
        
        return True

    except Exception as e:
        print(f"Error during wipe: {e}")
        return False

if __name__ == "__main__":
    success = wipe_database()
    if not success:
        sys.exit(1)
