#!/usr/bin/env python3
"""
Database module for SoulSeekarr
Handles SQLite database operations for execution history, logs, and persistent storage.
"""

import sqlite3
import json
import logging
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
        
        # Create indexes for better performance
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_script_executions_script_id ON script_executions(script_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_script_executions_start_time ON script_executions(start_time)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_script_executions_status ON script_executions(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_script_logs_execution_id ON script_logs(execution_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_script_logs_timestamp ON script_logs(timestamp)")
        
        # Run migrations if needed
        if current_version == 0:
            # Initial version
            cursor.execute("PRAGMA user_version = 1")
        
        conn.commit()
        logger.info(f"Database tables created/verified successfully (schema version: {max(1, current_version)})")
        
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

# Global database manager instance
db = DatabaseManager()

def get_db() -> DatabaseManager:
    """Get the global database manager instance."""
    return db