#!/usr/bin/env python3
"""
Database module for SoulSeekarr
Handles SQLite database operations for execution history, logs, and persistent storage.
"""

import sqlite3
import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

# Database file location
DB_PATH = Path("work/soulseekarr.db")

class DatabaseManager:
    """Manages SQLite database operations for SoulSeekarr."""
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or str(DB_PATH)
        self.ensure_database_exists()
    
    def ensure_database_exists(self):
        """Create database and tables if they don't exist."""
        # Ensure work directory exists
        Path(self.db_path).parent.mkdir(exist_ok=True)
        
        with self.get_connection() as conn:
            self.create_tables(conn)
    
    @contextmanager
    def get_connection(self):
        """Get a database connection with proper error handling."""
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row  # Enable dict-like access
            yield conn
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"Database error: {e}")
            raise
        finally:
            if conn:
                conn.close()
    
    def create_tables(self, conn: sqlite3.Connection):
        """Create all necessary database tables."""
        cursor = conn.cursor()
        
        # Check current schema version
        cursor.execute("PRAGMA user_version")
        current_version = cursor.fetchone()[0]
        
        # Script executions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS script_executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                script_id TEXT NOT NULL,
                script_name TEXT NOT NULL,
                start_time TIMESTAMP NOT NULL,
                end_time TIMESTAMP,
                duration_seconds REAL,
                status TEXT NOT NULL DEFAULT 'running',
                return_code INTEGER,
                dry_run BOOLEAN DEFAULT FALSE,
                pid INTEGER,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Script logs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS script_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                execution_id INTEGER NOT NULL,
                line_number INTEGER NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                content TEXT NOT NULL,
                log_level TEXT DEFAULT 'info',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (execution_id) REFERENCES script_executions (id) ON DELETE CASCADE
            )
        """)
        
        # Script configurations table (for dynamic discovery)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS script_configs (
                script_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                script_path TEXT NOT NULL,
                supports_dry_run BOOLEAN DEFAULT TRUE,
                section TEXT DEFAULT 'commands',
                status TEXT DEFAULT 'active',
                status_message TEXT,
                last_discovered TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                execution_count INTEGER DEFAULT 0,
                last_execution_time TIMESTAMP,
                avg_duration_seconds REAL
            )
        """)
        
        # Application settings table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                description TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Expiring albums table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS expiring_albums (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                album_key TEXT NOT NULL UNIQUE,
                artist TEXT NOT NULL,
                album TEXT NOT NULL,
                directory TEXT NOT NULL,
                oldest_file_days INTEGER NOT NULL,
                days_until_expiry INTEGER NOT NULL,
                file_count INTEGER NOT NULL,
                total_size_mb REAL NOT NULL,
                is_starred BOOLEAN DEFAULT FALSE,
                album_art_url TEXT,
                first_detected TIMESTAMP NOT NULL,
                last_seen TIMESTAMP NOT NULL,
                status TEXT DEFAULT 'pending',
                deleted_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Album expiry history table (tracks changes over time)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS album_expiry_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                album_id INTEGER NOT NULL,
                oldest_file_days INTEGER NOT NULL,
                days_until_expiry INTEGER NOT NULL,
                file_count INTEGER NOT NULL,
                total_size_mb REAL NOT NULL,
                is_starred BOOLEAN DEFAULT FALSE,
                status TEXT NOT NULL,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (album_id) REFERENCES expiring_albums (id) ON DELETE CASCADE
            )
        """)
        
        # Album tracks table (individual files in an album)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS album_tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                album_id INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                file_name TEXT NOT NULL,
                track_title TEXT,
                file_size_mb REAL NOT NULL,
                days_old INTEGER NOT NULL,
                last_modified TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (album_id) REFERENCES expiring_albums (id) ON DELETE CASCADE,
                UNIQUE(album_id, file_path)
            )
        """)
        
        # Create indexes for better performance (excluding new columns initially)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_script_executions_script_id ON script_executions(script_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_script_executions_start_time ON script_executions(start_time)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_script_executions_status ON script_executions(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_script_logs_execution_id ON script_logs(execution_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_script_logs_timestamp ON script_logs(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_expiring_albums_status ON expiring_albums(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_expiring_albums_days_until_expiry ON expiring_albums(days_until_expiry)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_expiring_albums_last_seen ON expiring_albums(last_seen)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_album_expiry_history_album_id ON album_expiry_history(album_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_album_expiry_history_recorded_at ON album_expiry_history(recorded_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_album_tracks_album_id ON album_tracks(album_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_album_tracks_days_old ON album_tracks(days_old)")
        # Note: Indexes for new columns (is_starred, navidrome_id) are created in migration section
        
        # Run migrations if needed
        if current_version == 0:
            # Initial version
            cursor.execute("PRAGMA user_version = 1")
        
        if current_version < 2:
            # Migration: Add album_art_url column to expiring_albums
            logger.info("Running migration to add album_art_url column...")
            try:
                cursor.execute("ALTER TABLE expiring_albums ADD COLUMN album_art_url TEXT")
                cursor.execute("PRAGMA user_version = 2")
                logger.info("Successfully added album_art_url column")
            except sqlite3.OperationalError as e:
                if "duplicate column" in str(e).lower():
                    # Column already exists, just update version
                    cursor.execute("PRAGMA user_version = 2")
                    logger.info("album_art_url column already exists, updating version")
                else:
                    raise
        if current_version < 3:
            # Migration: Add track-level starred tracking and metadata
            logger.info("Running migration to add track starred tracking...")
            try:
                # Add columns one by one with better error handling
                columns_to_add = [
                    "ALTER TABLE album_tracks ADD COLUMN is_starred BOOLEAN DEFAULT FALSE",
                    "ALTER TABLE album_tracks ADD COLUMN navidrome_id TEXT",
                    "ALTER TABLE album_tracks ADD COLUMN track_number INTEGER", 
                    "ALTER TABLE album_tracks ADD COLUMN track_artist TEXT"
                ]
                
                for sql in columns_to_add:
                    try:
                        cursor.execute(sql)
                    except sqlite3.OperationalError as e:
                        if "duplicate column" not in str(e).lower():
                            raise  # Re-raise if not a duplicate column error
                
                # Create indexes
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_album_tracks_is_starred ON album_tracks(is_starred)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_album_tracks_navidrome_id ON album_tracks(navidrome_id)")
                
                # Update schema version
                cursor.execute("PRAGMA user_version = 3")
                conn.commit()  # Commit immediately after migration
                
                logger.info("Successfully added track starred tracking and metadata columns")
            except sqlite3.OperationalError as e:
                if "duplicate column" in str(e).lower():
                    # Columns already exist, just update version
                    cursor.execute("PRAGMA user_version = 3")
                    conn.commit()
                    logger.info("Track starred tracking columns already exist, updating version")
                else:
                    logger.error(f"Migration failed: {e}")
                    raise
        
        if current_version < 4:
            # Migration: Remove cleanup_days column - policy is now frontend responsibility
            logger.info("Running migration to remove cleanup_days column...")
            try:
                # SQLite doesn't support DROP COLUMN directly, so we recreate the table
                # First, create the new table structure without cleanup_days
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS expiring_albums_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        album_key TEXT NOT NULL UNIQUE,
                        artist TEXT NOT NULL,
                        album TEXT NOT NULL,
                        directory TEXT NOT NULL,
                        oldest_file_days INTEGER NOT NULL,
                        days_until_expiry INTEGER NOT NULL,
                        file_count INTEGER NOT NULL,
                        total_size_mb REAL NOT NULL,
                        is_starred BOOLEAN DEFAULT FALSE,
                        album_art_url TEXT,
                        first_detected TIMESTAMP NOT NULL,
                        last_seen TIMESTAMP NOT NULL,
                        status TEXT DEFAULT 'pending',
                        deleted_at TIMESTAMP,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # Copy data from old table to new (excluding cleanup_days)
                cursor.execute("""
                    INSERT INTO expiring_albums_new 
                    (id, album_key, artist, album, directory, oldest_file_days, days_until_expiry,
                     file_count, total_size_mb, is_starred, album_art_url, first_detected, last_seen,
                     status, deleted_at, created_at, updated_at)
                    SELECT id, album_key, artist, album, directory, oldest_file_days, days_until_expiry,
                           file_count, total_size_mb, is_starred, album_art_url, first_detected, last_seen,
                           status, deleted_at, created_at, updated_at
                    FROM expiring_albums
                """)
                
                # Drop old table and rename new one
                cursor.execute("DROP TABLE expiring_albums")
                cursor.execute("ALTER TABLE expiring_albums_new RENAME TO expiring_albums")
                
                # Recreate indexes
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_expiring_albums_status ON expiring_albums(status)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_expiring_albums_days_until_expiry ON expiring_albums(days_until_expiry)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_expiring_albums_last_seen ON expiring_albums(last_seen)")
                
                # Update schema version
                cursor.execute("PRAGMA user_version = 4")
                conn.commit()  # Commit immediately after migration
                
                logger.info("Successfully removed cleanup_days column - policy now determined by frontend")
            except Exception as e:
                logger.error(f"Migration to remove cleanup_days failed: {e}")
                # Try to continue - if tables exist without cleanup_days, that's fine
                cursor.execute("PRAGMA user_version = 4")
                conn.commit()
                logger.info("Set schema version to 4, assuming cleanup_days already removed")
        
        conn.commit()
        
        # Verify final schema version
        cursor.execute("PRAGMA user_version")
        final_version = cursor.fetchone()[0]
        logger.info(f"Database tables created/verified successfully (schema version: {final_version})")
        
        # Verify critical columns exist
        try:
            cursor.execute("PRAGMA table_info(album_tracks)")
            columns = [row[1] for row in cursor.fetchall()]
            required_columns = ['is_starred', 'navidrome_id', 'track_number', 'track_artist']
            missing_columns = [col for col in required_columns if col not in columns]
            
            if missing_columns:
                logger.warning(f"Missing columns in album_tracks table: {missing_columns}")
            else:
                logger.info("All required columns verified in album_tracks table")
        except Exception as e:
            logger.warning(f"Could not verify table schema: {e}")
        
        # Clean up any executions that were running when container stopped
        self.cleanup_orphaned_executions(conn)
    
    def cleanup_orphaned_executions(self, conn: sqlite3.Connection):
        """Mark any running executions as stopped (for container restarts)."""
        cursor = conn.cursor()
        
        # Find executions that are still marked as running
        cursor.execute("SELECT id, script_name FROM script_executions WHERE status = 'running'")
        orphaned_executions = cursor.fetchall()
        
        if orphaned_executions:
            logger.info(f"Found {len(orphaned_executions)} orphaned running executions from previous session")
            
            # Mark them as stopped
            current_time = datetime.now()
            for execution in orphaned_executions:
                execution_id = execution['id']
                script_name = execution['script_name']
                
                # Calculate duration from start time
                cursor.execute("SELECT start_time FROM script_executions WHERE id = ?", (execution_id,))
                start_time_str = cursor.fetchone()['start_time']
                start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
                duration = (current_time - start_time).total_seconds()
                
                cursor.execute("""
                    UPDATE script_executions 
                    SET status = 'stopped', 
                        end_time = ?, 
                        duration_seconds = ?,
                        error_message = 'Execution interrupted by container restart',
                        updated_at = ?
                    WHERE id = ?
                """, (current_time, duration, current_time, execution_id))
                
                logger.info(f"Marked orphaned execution as stopped: {script_name} (ID: {execution_id})")
            
            conn.commit()
            logger.info(f"Cleaned up {len(orphaned_executions)} orphaned executions")
        else:
            logger.debug("No orphaned executions found")
    
    def start_execution(self, script_id: str, script_name: str, dry_run: bool = False, pid: int = None) -> int:
        """Record the start of a script execution."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO script_executions 
                (script_id, script_name, start_time, status, dry_run, pid)
                VALUES (?, ?, ?, 'running', ?, ?)
            """, (script_id, script_name, datetime.now(), dry_run, pid))
            execution_id = cursor.lastrowid
            conn.commit()
            
            # Update script config execution count
            self.update_script_stats(script_id)
            
            logger.info(f"Started execution tracking for {script_name} (ID: {execution_id})")
            return execution_id
    
    def finish_execution(self, execution_id: int, return_code: int, error_message: str = None):
        """Record the completion of a script execution."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Get start time to calculate duration
            cursor.execute("SELECT start_time FROM script_executions WHERE id = ?", (execution_id,))
            row = cursor.fetchone()
            if not row:
                logger.error(f"Execution ID {execution_id} not found")
                return
            
            start_time = datetime.fromisoformat(row['start_time'])
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            
            status = 'completed' if return_code == 0 else 'failed'
            
            cursor.execute("""
                UPDATE script_executions 
                SET end_time = ?, duration_seconds = ?, status = ?, return_code = ?, error_message = ?, updated_at = ?
                WHERE id = ?
            """, (end_time, duration, status, return_code, error_message, datetime.now(), execution_id))
            
            conn.commit()
            logger.info(f"Finished execution tracking for ID {execution_id} with status {status}")
    
    def stop_execution(self, execution_id: int, reason: str = "Manually stopped"):
        """Stop a running execution (useful for manual intervention)."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Check if execution is running
            cursor.execute("SELECT start_time, status FROM script_executions WHERE id = ?", (execution_id,))
            row = cursor.fetchone()
            if not row:
                logger.warning(f"Execution ID {execution_id} not found")
                return False
            
            if row['status'] != 'running':
                logger.warning(f"Execution ID {execution_id} is not running (status: {row['status']})")
                return False
            
            # Calculate duration
            start_time = datetime.fromisoformat(row['start_time'])
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            
            # Update execution
            cursor.execute("""
                UPDATE script_executions 
                SET status = 'stopped', 
                    end_time = ?, 
                    duration_seconds = ?,
                    error_message = ?,
                    updated_at = ?
                WHERE id = ?
            """, (end_time, duration, reason, end_time, execution_id))
            
            conn.commit()
            logger.info(f"Stopped execution ID {execution_id}: {reason}")
            return True
    
    def add_log_line(self, execution_id: int, content: str, log_level: str = 'info'):
        """Add a log line for a script execution."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Get current line number
            cursor.execute("SELECT COUNT(*) as count FROM script_logs WHERE execution_id = ?", (execution_id,))
            line_number = cursor.fetchone()['count'] + 1
            
            cursor.execute("""
                INSERT INTO script_logs (execution_id, line_number, timestamp, content, log_level)
                VALUES (?, ?, ?, ?, ?)
            """, (execution_id, line_number, datetime.now(), content, log_level))
            
            conn.commit()
    
    def get_execution_queue(self, limit: int = 50) -> List[Dict]:
        """Get recent script executions for the queue view."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM script_executions 
                ORDER BY start_time DESC 
                LIMIT ?
            """, (limit,))
            
            executions = []
            for row in cursor.fetchall():
                execution = dict(row)
                # Convert timestamps
                execution['start_time'] = datetime.fromisoformat(execution['start_time'])
                if execution['end_time']:
                    execution['end_time'] = datetime.fromisoformat(execution['end_time'])
                executions.append(execution)
            
            return executions
    
    def get_execution_logs(self, execution_id: int, limit: int = 1000) -> List[Dict]:
        """Get logs for a specific execution."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM script_logs 
                WHERE execution_id = ? 
                ORDER BY line_number ASC 
                LIMIT ?
            """, (execution_id, limit))
            
            logs = []
            for row in cursor.fetchall():
                log = dict(row)
                log['timestamp'] = datetime.fromisoformat(log['timestamp'])
                logs.append(log)
            
            return logs
    
    def get_script_logs(self, script_id: str, limit: int = 1000) -> List[str]:
        """Get recent logs for a script (for backwards compatibility)."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT sl.content, sl.timestamp
                FROM script_logs sl
                JOIN script_executions se ON sl.execution_id = se.id
                WHERE se.script_id = ?
                ORDER BY sl.timestamp DESC
                LIMIT ?
            """, (script_id, limit))
            
            logs = []
            for row in cursor.fetchall():
                timestamp = datetime.fromisoformat(row['timestamp']).strftime('%H:%M:%S')
                logs.append(f"[{timestamp}] {row['content']}")
            
            return list(reversed(logs))  # Return in chronological order
    
    def get_execution_logs(self, execution_id: int, limit: int = 10000) -> List[str]:
        """Get logs for a specific execution."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT content, timestamp
                FROM script_logs
                WHERE execution_id = ?
                ORDER BY timestamp ASC
                LIMIT ?
            """, (execution_id, limit))
            
            logs = []
            for row in cursor.fetchall():
                timestamp = datetime.fromisoformat(row['timestamp']).strftime('%H:%M:%S')
                logs.append(f"[{timestamp}] {row['content']}")
            
            return logs
    
    def clear_script_logs(self, script_id: str):
        """Clear all logs for a script."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM script_logs 
                WHERE execution_id IN (
                    SELECT id FROM script_executions WHERE script_id = ?
                )
            """, (script_id,))
            conn.commit()
            logger.info(f"Cleared logs for script {script_id}")
    
    def get_active_executions(self) -> Dict[str, Dict]:
        """Get currently running executions."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM script_executions 
                WHERE status = 'running'
                ORDER BY start_time DESC
            """)
            
            active = {}
            for row in cursor.fetchall():
                execution = dict(row)
                execution['start_time'] = datetime.fromisoformat(execution['start_time'])
                active[execution['script_id']] = {
                    'running': True,
                    'pid': execution['pid'],
                    'start_time': execution['start_time'],
                    'dry_run': execution['dry_run'],
                    'execution_id': execution['id']
                }
            
            return active
    
    def update_script_stats(self, script_id: str):
        """Update execution statistics for a script."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE script_configs 
                SET execution_count = (
                    SELECT COUNT(*) FROM script_executions WHERE script_id = ?
                ),
                last_execution_time = (
                    SELECT MAX(start_time) FROM script_executions WHERE script_id = ?
                ),
                avg_duration_seconds = (
                    SELECT AVG(duration_seconds) 
                    FROM script_executions 
                    WHERE script_id = ? AND duration_seconds IS NOT NULL
                )
                WHERE script_id = ?
            """, (script_id, script_id, script_id, script_id))
            conn.commit()
    
    def save_script_config(self, script_id: str, config: Dict):
        """Save or update a script configuration."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO script_configs 
                (script_id, name, description, script_path, supports_dry_run, section, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                script_id,
                config.get('name', script_id),
                config.get('description', ''),
                config.get('script', ''),
                config.get('supports_dry_run', True),
                config.get('section', 'commands'),
                config.get('status', 'active')
            ))
            conn.commit()
    
    def get_script_configs(self) -> Dict[str, Dict]:
        """Get all script configurations."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM script_configs WHERE status = 'active'")
            
            configs = {}
            for row in cursor.fetchall():
                config = dict(row)
                script_id = config.pop('script_id')
                configs[script_id] = config
            
            return configs
    
    def cleanup_old_data(self, days: int = 30):
        """Clean up old execution data."""
        cutoff_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff_date = cutoff_date.replace(day=cutoff_date.day - days)
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Delete old executions (logs will be deleted via CASCADE)
            cursor.execute("""
                DELETE FROM script_executions 
                WHERE start_time < ? AND status != 'running'
            """, (cutoff_date,))
            
            deleted_count = cursor.rowcount
            conn.commit()
            
            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} old execution records")
    
    def get_execution_stats(self) -> Dict:
        """Get overall execution statistics."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            stats = {}
            
            # Total executions
            cursor.execute("SELECT COUNT(*) as total FROM script_executions")
            stats['total_executions'] = cursor.fetchone()['total']
            
            # Success rate
            cursor.execute("""
                SELECT 
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as successful,
                    COUNT(*) as total
                FROM script_executions 
                WHERE status != 'running'
            """)
            result = cursor.fetchone()
            if result['total'] > 0:
                stats['success_rate'] = (result['successful'] / result['total']) * 100
            else:
                stats['success_rate'] = 0
            
            # Most used scripts
            cursor.execute("""
                SELECT script_name, COUNT(*) as count 
                FROM script_executions 
                GROUP BY script_id, script_name 
                ORDER BY count DESC 
                LIMIT 5
            """)
            stats['most_used_scripts'] = [dict(row) for row in cursor.fetchall()]
            
            return stats

    # Expiring Albums Management
    def upsert_expiring_album(self, album_data: Dict):
        """Insert or update an expiring album record."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            album_key = album_data['album_key']
            
            # Check if album already exists
            cursor.execute("SELECT id, first_detected FROM expiring_albums WHERE album_key = ?", (album_key,))
            existing = cursor.fetchone()
            
            now = datetime.now()
            
            if existing:
                # Update existing record (preserving first_detected)
                cursor.execute("""
                    UPDATE expiring_albums 
                    SET oldest_file_days = ?,
                        days_until_expiry = ?,
                        file_count = ?,
                        total_size_mb = ?,
                        is_starred = ?,
                        last_seen = ?,
                        status = ?,
                        album_art_url = ?,
                        updated_at = ?
                    WHERE album_key = ?
                """, (
                    album_data['oldest_file_days'],
                    album_data['days_until_expiry'],
                    album_data['file_count'],
                    album_data['total_size_mb'],
                    album_data['is_starred'],
                    now,
                    album_data['status'],
                    album_data.get('album_art_url'),
                    now,
                    album_key
                ))
                album_id = existing['id']
            else:
                # Insert new record
                cursor.execute("""
                    INSERT INTO expiring_albums 
                    (album_key, artist, album, directory, album_art_url, oldest_file_days, days_until_expiry, 
                     file_count, total_size_mb, is_starred, first_detected, last_seen, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    album_key,
                    album_data['artist'],
                    album_data['album'],
                    album_data['directory'],
                    album_data.get('album_art_url'),
                    album_data['oldest_file_days'],
                    album_data['days_until_expiry'],
                    album_data['file_count'],
                    album_data['total_size_mb'],
                    album_data['is_starred'],
                    now,
                    now,
                    album_data['status']
                ))
                album_id = cursor.lastrowid
            
            # Record in history
            cursor.execute("""
                INSERT INTO album_expiry_history 
                (album_id, oldest_file_days, days_until_expiry, file_count, 
                 total_size_mb, is_starred, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                album_id,
                album_data['oldest_file_days'],
                album_data['days_until_expiry'],
                album_data['file_count'],
                album_data['total_size_mb'],
                album_data['is_starred'],
                album_data['status']
            ))
            
            conn.commit()
            return album_id
    
    def get_expiring_albums(self, status: str = 'pending', include_starred: bool = False) -> List[Dict]:
        """Get expiring albums from database."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            query = "SELECT * FROM expiring_albums WHERE status = ?"
            params = [status]
            
            if not include_starred:
                query += " AND is_starred = FALSE"
            
            query += " ORDER BY days_until_expiry ASC, oldest_file_days DESC"
            
            cursor.execute(query, params)
            
            albums = []
            for row in cursor.fetchall():
                album = dict(row)
                album['first_detected'] = datetime.fromisoformat(album['first_detected'])
                album['last_seen'] = datetime.fromisoformat(album['last_seen'])
                if album['deleted_at']:
                    album['deleted_at'] = datetime.fromisoformat(album['deleted_at'])
                albums.append(album)
            
            return albums
    
    def get_expiring_albums_summary(self) -> Dict:
        """Get summary data for expiring albums (for web UI)."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Get active expiring albums (not starred, not deleted)
            cursor.execute("""
                SELECT * FROM expiring_albums 
                WHERE status = 'pending' AND is_starred = FALSE
                ORDER BY days_until_expiry ASC
            """)
            
            albums = {}
            
            for row in cursor.fetchall():
                album_dict = dict(row)
                album_key = album_dict['album_key']
                
                albums[album_key] = {
                    'artist': album_dict['artist'],
                    'album': album_dict['album'],
                    'directory': album_dict['directory'],
                    'album_art_url': album_dict.get('album_art_url'),
                    'oldest_file_days': album_dict['oldest_file_days'],
                    'days_until_expiry': album_dict['days_until_expiry'],
                    'file_count': album_dict['file_count'],
                    'total_size_mb': album_dict['total_size_mb'],
                    'is_starred': album_dict['is_starred'],
                    'will_expire': album_dict['days_until_expiry'] <= 0,
                    'sample_files': []  # Could be enhanced to track specific files
                }
            
            return {
                'generated_at': datetime.now().isoformat(),
                'total_albums': len(albums),
                'albums': albums
            }
    
    def mark_album_deleted(self, album_key: str):
        """Mark an album as deleted."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE expiring_albums 
                SET status = 'deleted', deleted_at = ?, updated_at = ?
                WHERE album_key = ?
            """, (datetime.now(), datetime.now(), album_key))
            conn.commit()
    
    def mark_album_starred(self, album_key: str, is_starred: bool = True):
        """Mark an album as starred/unstarred."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE expiring_albums 
                SET is_starred = ?, status = ?, updated_at = ?
                WHERE album_key = ?
            """, (is_starred, 'starred' if is_starred else 'pending', datetime.now(), album_key))
            conn.commit()
    
    def cleanup_old_album_data(self, days: int = 90):
        """Clean up old album expiry data."""
        cutoff_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff_date = cutoff_date.replace(day=cutoff_date.day - days)
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Delete albums that were deleted more than X days ago
            cursor.execute("""
                DELETE FROM expiring_albums 
                WHERE status = 'deleted' AND deleted_at < ?
            """, (cutoff_date,))
            
            deleted_count = cursor.rowcount
            
            # Clean up very old history entries
            cursor.execute("""
                DELETE FROM album_expiry_history 
                WHERE recorded_at < ?
            """, (cutoff_date,))
            
            history_deleted = cursor.rowcount
            
            conn.commit()
            
            if deleted_count > 0 or history_deleted > 0:
                logger.info(f"Cleaned up {deleted_count} old album records and {history_deleted} history entries")
    
    def get_album_expiry_history(self, album_key: str, limit: int = 30) -> List[Dict]:
        """Get historical data for an album."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT aeh.* 
                FROM album_expiry_history aeh
                JOIN expiring_albums ea ON aeh.album_id = ea.id
                WHERE ea.album_key = ?
                ORDER BY aeh.recorded_at DESC
                LIMIT ?
            """, (album_key, limit))
            
            history = []
            for row in cursor.fetchall():
                record = dict(row)
                record['recorded_at'] = datetime.fromisoformat(record['recorded_at'])
                history.append(record)
            
            return history

    def add_album_track(self, album_id: int, track_data: Dict):
        """Add or update a track for an album."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO album_tracks 
                (album_id, file_path, file_name, track_title, track_number, track_artist, 
                 file_size_mb, days_old, last_modified, is_starred, navidrome_id, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                album_id,
                track_data['file_path'],
                track_data['file_name'],
                track_data.get('track_title'),
                track_data.get('track_number'),
                track_data.get('track_artist'),
                track_data['file_size_mb'],
                track_data['days_old'],
                track_data['last_modified'],
                track_data.get('is_starred', False),
                track_data.get('navidrome_id'),
                datetime.now()
            ))
            conn.commit()
    
    def get_album_tracks(self, album_key: str) -> List[Dict]:
        """Get all tracks for an album."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT at.* 
                FROM album_tracks at
                JOIN expiring_albums ea ON at.album_id = ea.id
                WHERE ea.album_key = ?
                ORDER BY at.file_name ASC
            """, (album_key,))
            
            tracks = []
            for row in cursor.fetchall():
                track = dict(row)
                track['last_modified'] = datetime.fromisoformat(track['last_modified'])
                tracks.append(track)
            
            return tracks
    
    def clear_album_tracks(self, album_id: int):
        """Clear all tracks for an album (before re-scanning)."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM album_tracks WHERE album_id = ?", (album_id,))
            conn.commit()
    
    def update_track_starred_status(self, file_path: str, is_starred: bool, navidrome_id: str = None):
        """Update the starred status of a specific track."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            if navidrome_id:
                # Update by Navidrome ID if available
                cursor.execute("""
                    UPDATE album_tracks 
                    SET is_starred = ?, navidrome_id = ?, updated_at = ?
                    WHERE navidrome_id = ? OR file_path = ?
                """, (is_starred, navidrome_id, datetime.now(), navidrome_id, file_path))
            else:
                # Update by file path
                cursor.execute("""
                    UPDATE album_tracks 
                    SET is_starred = ?, updated_at = ?
                    WHERE file_path = ?
                """, (is_starred, datetime.now(), file_path))
            
            conn.commit()
            return cursor.rowcount > 0
    
    def get_starred_tracks(self) -> List[Dict]:
        """Get all starred tracks."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT at.*, ea.artist, ea.album, ea.directory
                FROM album_tracks at
                JOIN expiring_albums ea ON at.album_id = ea.id
                WHERE at.is_starred = TRUE
                ORDER BY ea.artist, ea.album, at.track_number, at.file_name
            """)
            
            tracks = []
            for row in cursor.fetchall():
                track = dict(row)
                track['last_modified'] = datetime.fromisoformat(track['last_modified'])
                tracks.append(track)
            
            return tracks
    
    def bulk_update_starred_tracks(self, starred_tracks: List[Dict]):
        """Bulk update starred status for multiple tracks.
        
        Args:
            starred_tracks: List of dicts with 'file_path' and 'is_starred' keys
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # First, reset all tracks to unstarred
            cursor.execute("UPDATE album_tracks SET is_starred = FALSE, updated_at = ?", (datetime.now(),))
            
            # Then set starred tracks
            for track in starred_tracks:
                if track.get('is_starred', False):
                    cursor.execute("""
                        UPDATE album_tracks 
                        SET is_starred = TRUE, 
                            navidrome_id = ?,
                            updated_at = ?
                        WHERE file_path = ?
                    """, (
                        track.get('navidrome_id'),
                        datetime.now(),
                        track['file_path']
                    ))
            
            conn.commit()
            logger.info(f"Bulk updated starred status for {len(starred_tracks)} tracks")

# Global database manager instance
_db_instance = None
_db_lock = threading.Lock()

def get_db() -> DatabaseManager:
    """Get the global database manager instance with lazy initialization."""
    global _db_instance
    
    if _db_instance is None:
        with _db_lock:
            # Double-check pattern
            if _db_instance is None:
                logger.info("Initializing database manager...")
                _db_instance = DatabaseManager()
                logger.info("Database manager initialization complete")
    
    return _db_instance