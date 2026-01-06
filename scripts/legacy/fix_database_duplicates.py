#!/usr/bin/env python3
"""
Name: Fix Database Duplicates
Description: Identifies and merges duplicate album entries in the database.
Author: Assistant
Version: 1.0
"""

import os
import sys
import logging
import re
from collections import defaultdict
from datetime import datetime

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import get_db_connection

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

def is_new_format_key(key):
    """Check if key looks like a UUID (MBID) or local hash."""
    # Check for local hash prefix
    if key.startswith('local-'):
        return True
    
    # Check for UUID format (8-4-4-4-12 hex digits)
    uuid_pattern = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)
    if uuid_pattern.match(key):
        return True
        
    return False

def fix_duplicates():
    logger.info("🔧 Starting database duplicate cleanup...")
    
    db = get_db()
    
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            
            # Fetch all albums
            cursor.execute("SELECT * FROM expiring_albums")
            rows = [dict(row) for row in cursor.fetchall()]
            
            logger.info(f"📊 Found {len(rows)} total album records")
            
            # Group by normalized artist/album
            grouped = defaultdict(list)
            for row in rows:
                key = (row['artist'].lower().strip(), row['album'].lower().strip())
                grouped[key].append(row)
            
            duplicates_groups = {k: v for k, v in grouped.items() if len(v) > 1}
            logger.info(f"🔍 Found {len(duplicates_groups)} groups with duplicates")
            
            merged_count = 0
            deleted_count = 0
            
            for (artist, album), records in duplicates_groups.items():
                logger.info(f"\nProcessing duplicates for: {artist} - {album}")
                
                # 1. Determine the Keeper Record
                # Prioritize new format keys
                new_format_records = [r for r in records if is_new_format_key(r['album_key'])]
                legacy_records = [r for r in records if not is_new_format_key(r['album_key'])]
                
                keeper = None
                
                if new_format_records:
                    # If multiple new format records, keep the one seen most recently
                    new_format_records.sort(key=lambda x: x['last_seen'], reverse=True)
                    keeper = new_format_records[0]
                    logger.info(f"  ✅ Keeping new format record: {keeper['album_key']}")
                elif legacy_records:
                    # If only legacy records, keep the most recent one
                    legacy_records.sort(key=lambda x: x['last_seen'], reverse=True)
                    keeper = legacy_records[0]
                    logger.info(f"  ⚠️  Keeping legacy record (no new format found): {keeper['album_key']}")
                
                if not keeper:
                    continue
                
                # 2. Aggregate Data
                all_first_detected = [datetime.fromisoformat(str(r['first_detected'])) for r in records]
                earliest_detected = min(all_first_detected)
                
                is_starred = any(r['is_starred'] for r in records)
                
                # 3. Identify records to delete
                to_delete = [r for r in records if r['id'] != keeper['id']]
                
                # 4. Update Keeper
                logger.info(f"  🔄 Merging data:")
                logger.info(f"     First Detected: {keeper['first_detected']} -> {earliest_detected}")
                logger.info(f"     Is Starred: {keeper['is_starred']} -> {is_starred}")
                
                cursor.execute("""
                    UPDATE expiring_albums 
                    SET first_detected = ?, is_starred = ?
                    WHERE id = ?
                """, (earliest_detected, is_starred, keeper['id']))
                
                # 5. Delete others
                for record in to_delete:
                    logger.info(f"  🗑️  Deleting duplicate: {record['album_key']} (ID: {record['id']})")
                    cursor.execute("DELETE FROM expiring_albums WHERE id = ?", (record['id'],))
                    deleted_count += 1
                
                merged_count += 1
            
            conn.commit()
            logger.info("\n" + "="*50)
            logger.info(f"✅ Cleanup complete!")
            logger.info(f"   Merged groups: {merged_count}")
            logger.info(f"   Deleted records: {deleted_count}")
            logger.info("="*50)

    except Exception as e:
        logger.error(f"❌ Error during cleanup: {e}")
        raise

if __name__ == "__main__":
    fix_duplicates()
