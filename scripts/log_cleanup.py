#!/usr/bin/env python3
"""
Log Cleanup - Clean up old log files

This script removes log files older than 1 day from the logs directory to keep disk usage manageable.

Name: Log Cleanup
Author: SoulSeekarr
Version: 1.0
Section: maintenance
Tags: logs, cleanup, maintenance, disk-space
Supports dry run: true

Features:
- Removes log files older than 1 day (24 hours)
- Preserves recent logs for debugging
- Dry run support to preview what would be deleted
- Detailed reporting of cleaned files and space saved
- Safe operation with confirmation prompts
"""

import os
import sys
import time
import logging
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple

# Add parent directory to path to import database
sys.path.append(str(Path(__file__).parent.parent))
try:
    from database import get_db
except ImportError:
    # Fallback if running from root
    sys.path.append(str(Path(__file__).parent))
    try:
        from database import get_db
    except ImportError:
        print("Warning: Could not import database module - DB cleanup will be skipped")
        get_db = None

# Global statistics
STATS = {
    'files_found': 0,
    'files_deleted': 0,
    'files_skipped': 0,
    'bytes_saved': 0,
    'errors': 0
}

def setup_logging():
    """Set up logging with timestamps"""
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"log_cleanup_{timestamp}.log"
    
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(message)s',
        datefmt='%H:%M:%S',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    return log_file

def get_file_age_hours(file_path: Path) -> float:
    """Get the age of a file in hours"""
    try:
        # Use the more recent of creation time or modification time
        stat = file_path.stat()
        creation_time = stat.st_ctime
        modification_time = stat.st_mtime
        
        # Use the more recent timestamp
        file_time = max(creation_time, modification_time)
        current_time = time.time()
        
        age_seconds = current_time - file_time
        age_hours = age_seconds / 3600  # Convert to hours
        
        return age_hours
    except Exception as e:
        logging.warning(f"⚠️  Could not determine age of {file_path.name}: {e}")
        return 0

def format_file_size(size_bytes: int) -> str:
    """Format file size in human readable format"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"

def find_old_log_files(logs_dir: Path, max_age_hours: float = 24) -> List[Tuple[Path, float, int]]:
    """
    Find log files older than the specified age.
    
    Returns:
        List of tuples: (file_path, age_in_hours, size_in_bytes)
    """
    old_files = []
    
    if not logs_dir.exists():
        logging.info(f"📁 Logs directory does not exist: {logs_dir}")
        return old_files
    
    logging.info(f"🔍 Scanning logs directory: {logs_dir}")
    
    # Common log file extensions
    log_extensions = {'.log', '.txt', '.out', '.err'}
    
    try:
        for file_path in logs_dir.iterdir():
            if not file_path.is_file():
                continue
            
            # Check if it's a log file by extension
            if file_path.suffix.lower() not in log_extensions:
                continue
            
            STATS['files_found'] += 1
            
            age_hours = get_file_age_hours(file_path)
            file_size = file_path.stat().st_size
            
            if age_hours > max_age_hours:
                old_files.append((file_path, age_hours, file_size))
                logging.debug(f"   📄 Found old file: {file_path.name} (age: {age_hours:.1f}h, size: {format_file_size(file_size)})")
            else:
                STATS['files_skipped'] += 1
                logging.debug(f"   ✅ Keeping recent file: {file_path.name} (age: {age_hours:.1f}h)")
    
    except Exception as e:
        logging.error(f"❌ Error scanning logs directory: {e}")
        STATS['errors'] += 1
    
    return old_files

def delete_old_files(old_files: List[Tuple[Path, float, int]], dry_run: bool = False) -> None:
    """Delete the old log files"""
    if not old_files:
        logging.info("✅ No old log files found - nothing to clean up")
        return
    
    total_size = sum(size for _, _, size in old_files)
    logging.info(f"📋 Found {len(old_files)} old log files totaling {format_file_size(total_size)}")
    
    if dry_run:
        logging.info("🔍 DRY RUN MODE - Preview of files that would be deleted:")
        for file_path, age_hours, size in old_files:
            age_days = age_hours / 24
            logging.info(f"   🗑️  Would delete: {file_path.name} (age: {age_days:.1f} days, size: {format_file_size(size)})")
        
        logging.info(f"\n📊 DRY RUN SUMMARY:")
        logging.info(f"   🗑️  Would delete {len(old_files)} files")
        logging.info(f"   💾 Would free up {format_file_size(total_size)}")
        return
    
    # Actually delete the files
    logging.info("🗑️  Deleting old log files...")
    
    for i, (file_path, age_hours, size) in enumerate(old_files, 1):
        print(f"PROGRESS: {i}/{len(old_files)} - Deleting: {file_path.name}")
        try:
            age_days = age_hours / 24
            logging.info(f"   🗑️  Deleting: {file_path.name} (age: {age_days:.1f} days, size: {format_file_size(size)})")
            
            file_path.unlink()  # Delete the file
            
            STATS['files_deleted'] += 1
            STATS['bytes_saved'] += size
            
        except Exception as e:
            logging.error(f"   ❌ Failed to delete {file_path.name}: {e}")
            STATS['errors'] += 1

def print_summary():
    """Print cleanup summary"""
    logging.info("\n" + "="*60)
    logging.info("📊 LOG CLEANUP COMPLETE - SUMMARY")
    logging.info("="*60)
    
    logging.info(f"Files:")
    logging.info(f"   📄 Total found: {STATS['files_found']}")
    logging.info(f"   ✅ Kept (recent): {STATS['files_skipped']}")
    logging.info(f"   🗑️  Deleted (old): {STATS['files_deleted']}")
    logging.info(f"   ❌ Errors: {STATS['errors']}")
    
    if STATS['bytes_saved'] > 0:
        logging.info(f"\nDisk Space:")
        logging.info(f"   💾 Space freed: {format_file_size(STATS['bytes_saved'])}")
    
    # Performance summary
    if STATS['files_deleted'] > 0:
        logging.info(f"\n✅ Successfully cleaned up {STATS['files_deleted']} old log files!")
    elif STATS['files_found'] == 0:
        logging.info(f"\n✅ No log files found in logs directory")
    else:
        logging.info(f"\n✅ All {STATS['files_found']} log files are recent - nothing to clean")

def main():
    """Main function"""
    # Set up argument parser
    parser = argparse.ArgumentParser(
        description="Clean up old log files to free disk space",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview what would be deleted without actually deleting'
    )
    
    parser.add_argument(
        '--max-age-hours',
        type=float,
        default=24,
        help='Maximum age in hours for log files to keep (default: 24)'
    )
    
    parser.add_argument(
        '--logs-dir',
        type=str,
        default='logs',
        help='Path to logs directory (default: logs)'
    )
    
    # Check for dry run environment variable
    dry_run_env = os.environ.get('DRY_RUN', '').lower() in ('true', '1', 'yes')
    
    args = parser.parse_args()
    dry_run = args.dry_run or dry_run_env
    max_age_hours = args.max_age_hours
    logs_dir = Path(args.logs_dir)
    
    # Set up logging
    log_file = setup_logging()
    
    logging.info("🧹 LOG CLEANUP STARTED")
    logging.info("="*60)
    logging.info(f"📁 Logs directory: {logs_dir.absolute()}")
    logging.info(f"⏰ Max age: {max_age_hours} hours ({max_age_hours/24:.1f} days)")
    logging.info(f"🔍 Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    logging.info(f"📝 Log file: {log_file}")
    
    if dry_run:
        logging.info("⚠️  DRY RUN MODE - No files will actually be deleted")
    
    logging.info("\n" + "="*40)
    
    try:
        # Find old log files
        old_files = find_old_log_files(logs_dir, max_age_hours)
        
        # Delete old files (or show what would be deleted)
        delete_old_files(old_files, dry_run)
        
        # Clean up database logs
        if not dry_run and get_db:
            try:
                logging.info("🧹 Cleaning up old execution logs from database...")
                db = get_db()
                # Convert hours to days, minimum 1 day
                db_cleanup_days = max(1, int(max_age_hours / 24))
                db.cleanup_old_executions(days=db_cleanup_days)
            except Exception as e:
                logging.error(f"❌ Error cleaning up database logs: {e}")
        
        # Print summary
        print_summary()
        
    except KeyboardInterrupt:
        logging.info("\n⚠️  Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        logging.error(f"\n❌ Unexpected error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()