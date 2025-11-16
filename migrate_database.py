#!/usr/bin/env python3
"""
Database Migration Script

Forces database schema updates to the latest version.
Run this if migrations don't run automatically on restart.
"""

import sqlite3
import logging
from pathlib import Path
from datetime import datetime

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def migrate_database():
    """Force database migration to latest schema version."""
    db_path = Path("work/soulseekarr.db")
    
    if not db_path.exists():
        logger.error(f"Database not found: {db_path}")
        return False
    
    logger.info(f"Migrating database: {db_path}")
    
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        
        # Check current version
        cursor.execute("PRAGMA user_version")
        current_version = cursor.fetchone()[0]
        logger.info(f"Current schema version: {current_version}")
        
        if current_version < 3:
            logger.info("Migrating to version 3 (track starred tracking)...")
            
            # Add new columns to album_tracks table
            columns_to_add = [
                ("is_starred", "BOOLEAN DEFAULT FALSE"),
                ("navidrome_id", "TEXT"),
                ("track_number", "INTEGER"),
                ("track_artist", "TEXT")
            ]
            
            for column_name, column_def in columns_to_add:
                try:
                    cursor.execute(f"ALTER TABLE album_tracks ADD COLUMN {column_name} {column_def}")
                    logger.info(f"  ✅ Added column: {column_name}")
                except sqlite3.OperationalError as e:
                    if "duplicate column" in str(e).lower():
                        logger.info(f"  ℹ️  Column already exists: {column_name}")
                    else:
                        logger.error(f"  ❌ Failed to add column {column_name}: {e}")
                        raise
            
            # Create indexes
            indexes = [
                "CREATE INDEX IF NOT EXISTS idx_album_tracks_is_starred ON album_tracks(is_starred)",
                "CREATE INDEX IF NOT EXISTS idx_album_tracks_navidrome_id ON album_tracks(navidrome_id)"
            ]
            
            for index_sql in indexes:
                try:
                    cursor.execute(index_sql)
                    logger.info(f"  ✅ Created index")
                except sqlite3.OperationalError as e:
                    logger.warning(f"  ⚠️  Index creation warning: {e}")
            
            # Update version
            cursor.execute("PRAGMA user_version = 3")
            logger.info("  ✅ Updated schema version to 3")
        
        conn.commit()
        conn.close()
        
        logger.info("🎉 Database migration completed successfully!")
        return True
        
    except Exception as e:
        logger.error(f"❌ Migration failed: {e}")
        if 'conn' in locals():
            conn.rollback()
            conn.close()
        return False

if __name__ == "__main__":
    print("🔄 Database Migration Tool")
    print("=" * 40)
    
    success = migrate_database()
    
    if success:
        print("\n✅ Migration completed successfully!")
        print("You can now restart the application.")
    else:
        print("\n❌ Migration failed!")
        print("Please check the logs and try again.")