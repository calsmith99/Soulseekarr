#!/usr/bin/env python3
"""
Delete Expired Albums v1.0

Deletes albums from the Not_Owned directory that have exceeded the cleanup retention period
and are NOT starred in Navidrome.

Relies on the database populated by 'Scan Library Age'.

Name: Delete Expired Albums
Author: SoulSeekarr
Version: 1.0
Section: commands
Tags: cleanup, expiry, delete
Supports dry run: true
"""

import os
import sys
import shutil
import time
import signal
import logging
import argparse
from pathlib import Path
from datetime import datetime

# Import dependencies with error handling
try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

# Import project modules
from settings import get_setting, get_lidarr_config
from action_logger import log_script_start, log_script_complete, log_action, log_file_operation
from lidarr_utils import LidarrClient

try:
    from database import get_db
    DATABASE_AVAILABLE = True
    db = get_db()
except ImportError:
    DATABASE_AVAILABLE = False
    db = None

# Setup logging
log_dir = Path('/logs') if Path('/logs').exists() else Path(__file__).parent.parent / 'logs'
log_dir.mkdir(parents=True, exist_ok=True)
timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
log_file = log_dir / f'delete_expired_albums_{timestamp}.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Graceful interruption
interrupted = False

def signal_handler(signum, frame):
    global interrupted
    interrupted = True
    logger.warning("\n⚠️ Script interrupted by user")

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# Initialize Lidarr Client (Lazy load in main or global?)
# Let's do global for simplicity but protect against bad config
lidarr_client = None

def get_lidarr_client(dry_run=False):
    """Get or initialize Lidarr client."""
    global lidarr_client
    if lidarr_client:
        return lidarr_client
        
    try:
        config = get_lidarr_config()
        if config and config.get('url') and config.get('api_key'):
            lidarr_client = LidarrClient(
                config['url'], 
                config['api_key'], 
                logger, 
                dry_run=dry_run
            )
            return lidarr_client
    except Exception as e:
        logger.warning(f"Could not initialize Lidarr client: {e}")
    return None

def unmonitor_in_lidarr(artist_name, album_name, dry_run=False):
    """Unmonitor an album in Lidarr."""
    client = get_lidarr_client(dry_run)
    if not client:
        return False
        
    try:
        # 1. Find Artist
        artist = client.get_artist_by_name(artist_name)
        if not artist:
            logger.warning(f"  Lidarr: Artist '{artist_name}' not found. Cannot unmonitor album.")
            return False
            
        # 2. Find Album
        albums = client.get_artist_albums(artist['id'])
        target_album = None
        
        # Normalize for matching
        norm_target = client.normalize_artist_name(album_name)
        
        for alb in albums:
            if client.normalize_artist_name(alb.get('title', '')) == norm_target:
                target_album = alb
                break
                
        if not target_album:
            logger.warning(f"  Lidarr: Album '{album_name}' not found for artist. Cannot unmonitor.")
            return False
            
        if not target_album.get('monitored'):
            logger.info(f"  Lidarr: Album '{album_name}' is already unmonitored.")
            return True
            
        # 3. Unmonitor
        logger.info(f"  Lidarr: Unmonitoring '{album_name}'...")
        return client.set_album_monitored(target_album, monitored=False)
        
    except Exception as e:
        logger.error(f"  Lidarr Error: {e}")
        return False

def delete_album(album_data, dry_run=False):
    """Delete an album directory and update database."""
    directory = Path(album_data['directory'])
    artist = album_data['artist']
    album_name = album_data['album']
    
    if not directory.exists():
        logger.warning(f"Directory not found (already deleted?): {directory}")
        if not dry_run and db:
             mark_as_deleted(album_data['id'])
        return False

    logger.info(f"Deleting expired album: {artist} - {album_name}")
    logger.info(f"  Path: {directory}")
    logger.info(f"  Age: {(datetime.now() - album_data['first_detected']).days} days")

    # Unmonitor in Lidarr
    unmonitor_in_lidarr(artist, album_name, dry_run=dry_run)

    if dry_run:
        logger.info("[DRY RUN] Would delete directory tree")
        return True
    
    try:
        # Delete directory
        shutil.rmtree(directory)
        
        # Log action
        log_file_operation(
            operation="delete",
            source_path=str(directory),
            target_path=None,
            status="success",
            details=f"Deleted expired album: {artist} - {album_name}"
        )
        
        # Update Database
        if db:
            mark_as_deleted(album_data['id'])
            
        return True
        
    except Exception as e:
        logger.error(f"Failed to delete {directory}: {e}")
        log_file_operation(
            operation="delete",
            source_path=str(directory),
            target_path=None,
            status="failed",
            details=f"Error deleting: {e}"
        )
        return False

def mark_as_deleted(album_id):
    """Mark album as deleted in database."""
    with db.get_connection() as conn:
        conn.execute("""
            UPDATE expiring_albums 
            SET status = 'deleted', deleted_at = ?
            WHERE id = ?
        """, (datetime.now(), album_id))
        conn.commit()

def get_expired_albums(cleanup_days):
    """Fetch expired albums from database."""
    if not db:
        return []
        
    with db.get_connection() as conn:
        cursor = conn.cursor()
        
        # Calculate cutoff date
        # first_detected must be BEFORE (now - cleanup_days)
        # e.g. if cleanup_days=30, and now is Jan 31, cutoff is Jan 1.
        # album detected Jan 2 (29 days ago) -> Not expired
        # album detected Dec 31 (31 days ago) -> Expired
        
        cursor.execute("SELECT datetime('now', '-' || ? || ' days')", (str(cleanup_days),))
        cutoff_date = cursor.fetchone()[0]
        
        logger.info(f"Cleanup Policy: {cleanup_days} days")
        logger.info(f"Cutoff Date: {cutoff_date}")
        
        query = """
            SELECT * FROM expiring_albums 
            WHERE status != 'deleted' 
            AND is_starred = 0
            AND first_detected < datetime('now', '-' || ? || ' days')
        """
        
        cursor.execute(query, (str(cleanup_days),))
        
        albums = []
        for row in cursor.fetchall():
            album = dict(row)
            # Convert timestamp strings to datetime objects
            if isinstance(album['first_detected'], str):
                album['first_detected'] = datetime.fromisoformat(album['first_detected'])
            albums.append(album)
            
        return albums

def main():
    """Main script execution."""
    parser = argparse.ArgumentParser(description='Delete Expired Albums')
    parser.add_argument('--dry-run', action='store_true', help='Run without deleting files')
    args = parser.parse_args()
    
    dry_run = args.dry_run
    
    logger.info("=" * 60)
    logger.info("DELETE EXPIRED ALBUMS v1.0")
    logger.info("=" * 60)
    logger.info("")
    
    if dry_run:
        logger.info("🧪 DRY RUN MODE - No files will be deleted")
        logger.info("")
        
    start_time = time.time()
    log_script_start("Delete Expired Albums", f"Parameters: {'--dry-run' if dry_run else 'normal'}")
    
    try:
        if not DATABASE_AVAILABLE or not db:
            logger.error("❌ Database not available. Cannot proceed.")
            sys.exit(1)
            
        # Get cleanup policy
        cleanup_days = int(get_setting('CLEANUP_DAYS', '30'))
        
        # Fetch targets
        expired_albums = get_expired_albums(cleanup_days)
        
        if not expired_albums:
            logger.info("✅ No expired albums found.")
            log_script_complete("Delete Expired Albums", time.time() - start_time, success=True)
            return

        logger.info(f"Found {len(expired_albums)} expired albums to delete.")
        logger.info("")
        
        # Confirm for user log
        if not dry_run:
            logger.info("Starting deletion in 5 seconds... Press Ctrl+C to cancel")
            time.sleep(5)
            
        # Process
        success_count = 0
        fail_count = 0
        
        if TQDM_AVAILABLE:
            iterator = tqdm(expired_albums, desc="Deleting albums")
        else:
            iterator = expired_albums
            
        for album in iterator:
            if interrupted:
                break
                
            if delete_album(album, dry_run=dry_run):
                success_count += 1
            else:
                fail_count += 1
                
        # Summary
        logger.info("")
        logger.info("=" * 60)
        logger.info(f"Processed: {len(expired_albums)}")
        logger.info(f"Deleted: {success_count}")
        logger.info(f"Failed: {fail_count}")
        logger.info("=" * 60)
        
        log_script_complete("Delete Expired Albums", time.time() - start_time, success=True)
        
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        log_script_complete("Delete Expired Albums", time.time() - start_time, success=False, error=str(e))
        raise

if __name__ == '__main__':
    main()
