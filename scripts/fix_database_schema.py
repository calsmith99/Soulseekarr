#!/usr/bin/env python3
"""
Fix Database Schema

Forcefully aligns the database schema with the instructions, specifically fixing
the 'expiring_albums' table which might be missing columns or corrupt.

Name: Fix Database Schema
Author: SoulSeekarr
Version: 1.0
Section: commands
Tags: database, fix, maintenance
Supports dry run: true
"""

import os
import sys
import sqlite3
import logging
import argparse
import shutil
from pathlib import Path
from datetime import datetime

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

# Import project settings
from settings import get_setting

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

DB_PATH = Path("work/soulseekarr.db")

def backup_database():
    """Create a backup of the database."""
    if not DB_PATH.exists():
        return
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = DB_PATH.parent / f"soulseekarr.db.backup_{timestamp}"
    
    logger.info(f"Creating database backup at {backup_path}")
    shutil.copy2(DB_PATH, backup_path)
    return backup_path

def fix_expiring_albums_table(conn, dry_run=False):
    """Recreate expiring_albums table with correct schema."""
    cursor = conn.cursor()
    
    logger.info("Checking expiring_albums schema...")
    
    # Check existing columns
    try:
        cursor.execute("PRAGMA table_info(expiring_albums)")
        columns = {row[1]: row for row in cursor.fetchall()}
        logger.info(f"Existing columns: {list(columns.keys())}")
    except Exception as e:
        logger.error(f"Could not inspect table: {e}")
        return

    # Usage of temporary table to migrate data
    if dry_run:
        logger.info("🧪 DRY RUN - Would recreate expiring_albums table")
        return

    logger.info("Migrating expiring_albums table...")
    
    # 1. Rename existing
    try:
        cursor.execute("DROP TABLE IF EXISTS expiring_albums_old_backup")
        cursor.execute("ALTER TABLE expiring_albums RENAME TO expiring_albums_old_backup")
    except Exception as e:
        logger.error(f"Error renaming table: {e}")
        return

    # 2. Create correct schema (FROM INSTRUCTIONS)
    # Includes album_art_url and standard fields
    create_sql = """
    CREATE TABLE expiring_albums (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        album_key TEXT NOT NULL UNIQUE,
        artist TEXT NOT NULL,
        album TEXT NOT NULL,
        directory TEXT NOT NULL,
        file_count INTEGER NOT NULL DEFAULT 0,
        total_size_mb REAL NOT NULL DEFAULT 0,
        is_starred BOOLEAN DEFAULT FALSE,
        first_detected TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_seen TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        status TEXT DEFAULT 'pending',
        deleted_at TIMESTAMP,
        album_art_url TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """
    cursor.execute(create_sql)

    # 4. Copy data
    # We construct the INSERT statement dynamically based on what columns exist in source
    
    # Target columns we want to fill
    target_cols = [
        'id', 'album_key', 'artist', 'album', 'directory', 'file_count', 
        'total_size_mb', 'is_starred', 'first_detected', 'last_seen', 
        'status', 'deleted_at', 'album_art_url', 'created_at', 'updated_at'
    ]
    
    # Source columns we actually have
    available_cols = [c for c in target_cols if c in columns]
    
    # Special handling for file_count if missing locally but we want to recover data
    # If file_count missing, we can default to 0
    
    select_parts = []
    insert_parts = []
    
    for col in target_cols:
        if col in available_cols:
            select_parts.append(col)
            insert_parts.append(col)
        elif col == 'file_count':
            select_parts.append('0') # Default
            insert_parts.append(col)
        elif col == 'total_size_mb':
            select_parts.append('0')
            insert_parts.append(col)
        elif col == 'first_detected':
             select_parts.append("datetime('now')")
             insert_parts.append(col)
        elif col == 'last_seen':
             select_parts.append("datetime('now')")
             insert_parts.append(col)
    
    # Construct SQL
    sql = f"""
    INSERT INTO expiring_albums ({', '.join(insert_parts)})
    SELECT {', '.join(select_parts)}
    FROM expiring_albums_old_backup
    """
    
    logger.info(f"Executing migration SQL...")
    cursor.execute(sql)
    
    # Drop backup
    cursor.execute("DROP TABLE expiring_albums_old_backup")
    
    # 5. Create indexes
    logger.info("Recreating indexes...")
    cursor.execute("DROP INDEX IF EXISTS idx_expiring_albums_status")
    cursor.execute("DROP INDEX IF EXISTS idx_expiring_albums_last_seen")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_expiring_albums_status ON expiring_albums(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_expiring_albums_last_seen ON expiring_albums(last_seen)")
    
    logger.info("Table expiring_albums recreated successfully.")

def fix_album_tracks_table(conn, dry_run=False):
    """Recreate album_tracks table with correct schema."""
    cursor = conn.cursor()
    
    logger.info("Checking album_tracks schema...")
    
    try:
        cursor.execute("PRAGMA table_info(album_tracks)")
        columns = {row[1]: row for row in cursor.fetchall()}
        logger.info(f"Existing columns: {list(columns.keys())}")
    except Exception as e:
        logger.error(f"Could not inspect table: {e}")
        return

    if dry_run:
        logger.info("🧪 DRY RUN - Would recreate album_tracks table")
        return

    logger.info("Migrating album_tracks table...")
    
    # 1. Rename existing
    try:
        cursor.execute("DROP TABLE IF EXISTS album_tracks_old_backup")
        cursor.execute("ALTER TABLE album_tracks RENAME TO album_tracks_old_backup")
    except Exception as e:
        logger.error(f"Error renaming table: {e}")
        return

    # 2. Create correct schema
    create_sql = """
    CREATE TABLE album_tracks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        album_id INTEGER NOT NULL,
        file_path TEXT NOT NULL,
        file_name TEXT NOT NULL,
        track_title TEXT,
        track_number INTEGER,
        track_artist TEXT,
        file_size_mb REAL NOT NULL,
        days_old INTEGER NOT NULL,
        last_modified TIMESTAMP NOT NULL,
        is_starred BOOLEAN DEFAULT FALSE,
        navidrome_id TEXT,
        year INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (album_id) REFERENCES expiring_albums(id) ON DELETE CASCADE,
        UNIQUE(album_id, file_path)
    )
    """
    cursor.execute(create_sql)

    # 4. Copy data
    target_cols = [
        'id', 'album_id', 'file_path', 'file_name', 'track_title', 
        'track_number', 'track_artist', 'file_size_mb', 'days_old', 
        'last_modified', 'is_starred', 'navidrome_id', 'year',
        'created_at', 'updated_at'
    ]
    
    available_cols = [c for c in target_cols if c in columns]
    
    select_parts = []
    insert_parts = []
    
    for col in target_cols:
        if col in available_cols:
            select_parts.append(col)
            insert_parts.append(col)
        elif col in ['is_starred']:
            select_parts.append('0')
            insert_parts.append(col)
        # Add basic defaults for other text/int columns if missing, usually NULL is fine but implicit constraints might bite
        # For simplicity we let others be NULL if not NOT NULL
        
    sql = f"""
    INSERT INTO album_tracks ({', '.join(insert_parts)})
    SELECT {', '.join(select_parts)}
    FROM album_tracks_old_backup
    """
    
    logger.info(f"Executing migration SQL for album_tracks...")
    cursor.execute(sql)
    
    # Drop backup
    cursor.execute("DROP TABLE album_tracks_old_backup")
    
    # 5. Create indexes
    logger.info("Recreating indexes for album_tracks...")
    cursor.execute("DROP INDEX IF EXISTS idx_album_tracks_album_id")
    cursor.execute("DROP INDEX IF EXISTS idx_album_tracks_days_old")
    cursor.execute("DROP INDEX IF EXISTS idx_album_tracks_is_starred")
    cursor.execute("DROP INDEX IF EXISTS idx_album_tracks_navidrome_id")
    
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_album_tracks_album_id ON album_tracks(album_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_album_tracks_days_old ON album_tracks(days_old)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_album_tracks_is_starred ON album_tracks(is_starred)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_album_tracks_navidrome_id ON album_tracks(navidrome_id)")

    logger.info("Table album_tracks recreated successfully.")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    
    if not args.dry_run:
        backup_database()
        
    try:
        conn = sqlite3.connect(DB_PATH, timeout=60.0)
        conn.row_factory = sqlite3.Row
        
        fix_expiring_albums_table(conn, args.dry_run)
        fix_album_tracks_table(conn, args.dry_run)
        
        # Verify schema version
        cursor = conn.cursor()
        cursor.execute("PRAGMA user_version = 9") # Force to 9 to match current codebase
        conn.commit()
        
        conn.close()
        logger.info("Schema fix completed.")
        
    except Exception as e:
        logger.critical(f"Failed to fix database: {e}", exc_info=True)

if __name__ == "__main__":
    main()
