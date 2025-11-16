#!/usr/bin/env python3
"""
Cleanup script to remove orphaned album entries from the database.

This script finds albums in the expiring_albums table that have no corresponding
tracks in the album_tracks table and removes them.

Usage:
    python cleanup_orphaned_albums.py [--dry-run]
"""

import sys
import argparse
from pathlib import Path

# Add the parent directory to the path to import modules
sys.path.append(str(Path(__file__).parent.parent))

from database import DatabaseManager
import logging

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('logs/cleanup_orphaned_albums.log')
    ]
)
logger = logging.getLogger(__name__)


def cleanup_orphaned_albums(dry_run: bool = False) -> int:
    """
    Find and remove orphaned album entries.
    
    Args:
        dry_run: If True, only report what would be removed without making changes
        
    Returns:
        Number of albums that were (or would be) removed
    """
    db = DatabaseManager()
    
    try:
        with db.get_connection() as conn:
            logger.info("🔍 Searching for orphaned albums...")
            
            # Find albums that have no tracks
            cursor = conn.execute("""
                SELECT ea.id, ea.artist, ea.album, ea.directory, ea.file_count
                FROM expiring_albums ea
                LEFT JOIN album_tracks at ON ea.id = at.album_id
                WHERE at.album_id IS NULL
                ORDER BY ea.artist, ea.album
            """)
            
            orphaned_albums = cursor.fetchall()
            
            if not orphaned_albums:
                logger.info("✅ No orphaned albums found - database is clean!")
                return 0
            
            logger.info(f"🗑️  Found {len(orphaned_albums)} orphaned albums")
            
            if dry_run:
                logger.info("🔍 DRY RUN - showing what would be removed:")
            
            removed_count = 0
            total_file_count = 0
            
            for album_id, artist, album, directory, file_count in orphaned_albums:
                removed_count += 1
                total_file_count += file_count or 0
                
                # Show first 10 albums in detail, then summarize
                if removed_count <= 10:
                    status = "Would remove" if dry_run else "Removing"
                    logger.info(f"   🗑️  {status}: {artist} - {album} ({file_count} files)")
                    if directory:
                        logger.info(f"      📁 Directory: {directory}")
                
                # Actually remove if not dry run
                if not dry_run:
                    try:
                        conn.execute("DELETE FROM expiring_albums WHERE id = ?", (album_id,))
                    except Exception as e:
                        logger.error(f"      ❌ Failed to remove album {artist} - {album}: {e}")
                        removed_count -= 1
            
            if removed_count > 10:
                logger.info(f"   ... and {removed_count - 10} more albums")
            
            # Commit changes if not dry run
            if not dry_run:
                conn.commit()
                logger.info(f"✅ Successfully removed {removed_count} orphaned albums ({total_file_count} total files)")
            else:
                logger.info(f"🔍 DRY RUN: Would remove {removed_count} orphaned albums ({total_file_count} total files)")
                logger.info("💡 Run without --dry-run to actually remove these albums")
            
            return removed_count
            
    except Exception as e:
        logger.error(f"❌ Error during cleanup: {e}")
        return 0


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Cleanup orphaned album entries from database")
    parser.add_argument("--dry-run", action="store_true", 
                      help="Show what would be removed without making changes")
    
    args = parser.parse_args()
    
    logger.info("🧹 Starting orphaned albums cleanup")
    
    if args.dry_run:
        logger.info("🔍 Running in DRY RUN mode - no changes will be made")
    
    removed_count = cleanup_orphaned_albums(dry_run=args.dry_run)
    
    if removed_count > 0:
        if args.dry_run:
            logger.info(f"🔍 DRY RUN complete: Found {removed_count} orphaned albums")
        else:
            logger.info(f"✅ Cleanup complete: Removed {removed_count} orphaned albums")
    else:
        logger.info("✅ No orphaned albums found - database is already clean")


if __name__ == "__main__":
    main()