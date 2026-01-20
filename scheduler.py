#!/usr/bin/env python3
"""
Scheduler module for SoulSeekarr
Provides a Laravel-like scheduler for running scripts at configurable intervals.
"""

import os
import time
import threading
import logging
import subprocess
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from database import get_db

logger = logging.getLogger(__name__)

class SchedulerManager:
    """
    A Laravel-style scheduler for managing periodic script execution.
    Supports customizable intervals (minutes, hours, days) and persistent storage.
    """
    
    def __init__(self):
        self.db = get_db()
        self.running = False
        self.scheduler_thread = None
        self.lock = threading.Lock()
        self.check_interval = 60  # Check every 60 seconds
    
    def start(self):
        """Start the scheduler in a background thread."""
        with self.lock:
            if self.running:
                logger.warning("Scheduler is already running")
                return
            
            self.running = True
            self.scheduler_thread = threading.Thread(target=self._run_scheduler, daemon=True)
            self.scheduler_thread.start()
            logger.info("Scheduler started successfully")
    
    def stop(self):
        """Stop the scheduler."""
        with self.lock:
            if not self.running:
                return
            
            self.running = False
            if self.scheduler_thread:
                self.scheduler_thread.join(timeout=10)
            logger.info("Scheduler stopped")
    
    def _run_scheduler(self):
        """Main scheduler loop that runs in background thread."""
        logger.info("Scheduler loop started")
        
        while self.running:
            try:
                # Get jobs that are due to run
                due_jobs = self._get_due_jobs()
                
                for job in due_jobs:
                    if not self.running:
                        break
                    
                    self._execute_job(job)
                
                # Sleep for check interval
                time.sleep(self.check_interval)
                
            except Exception as e:
                logger.error(f"Error in scheduler loop: {e}")
                time.sleep(self.check_interval)  # Continue running even if there's an error
        
        logger.info("Scheduler loop stopped")
    
    def _get_due_jobs(self) -> List[Dict]:
        """Get all jobs that are due to run."""
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                
                # Get all enabled jobs that are due to run
                current_time = datetime.now()
                cursor.execute("""
                    SELECT id, script_id, script_name, script_path, interval_type, interval_value,
                           next_run, last_run, run_count, error_count
                    FROM scheduled_jobs 
                    WHERE enabled = TRUE 
                    AND (next_run IS NULL OR next_run <= ?)
                    ORDER BY next_run ASC
                """, (current_time,))
                
                jobs = [dict(row) for row in cursor.fetchall()]
                return jobs
                
        except Exception as e:
            logger.error(f"Error getting due jobs: {e}")
            return []
    
    def _execute_job(self, job: Dict):
        """Execute a scheduled job with integrated execution tracking."""
        job_id = job['id']
        script_id = job['script_id']
        script_name = job['script_name']
        script_path = job['script_path']
        
        logger.info(f"Executing scheduled job: {script_name} ({script_id})")
        
        # Start execution tracking (same as manual runs)
        execution_id = None
        try:
            execution_id = self.db.start_execution(script_id, f"{script_name} (Scheduled)", dry_run=False)
            logger.info(f"Started execution tracking for scheduled job: {execution_id}")
        except Exception as e:
            logger.error(f"Failed to start execution tracking: {e}")
            return
        
        start_time = datetime.now()
        success = False
        error_message = None
        return_code = 1
        
        try:
            # Make sure script path is absolute
            if not os.path.isabs(script_path):
                # If it's relative, make it relative to the application root
                app_root = os.path.dirname(os.path.abspath(__file__))
                script_path = os.path.join(app_root, script_path)
            
            # Execute the script
            if script_path.endswith('.py'):
                # Python script - use the same python executable as the current process
                import sys
                cmd = [sys.executable, '-u', script_path]
            elif script_path.endswith('.bat'):
                # Windows batch file
                cmd = ['cmd', '/c', script_path]
            elif script_path.endswith('.sh'):
                # Shell script (Linux/Unix)
                cmd = ['/bin/bash', script_path]
            else:
                # Try to execute directly
                cmd = [script_path]
            
            # Run with timeout
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=3600,  # 1 hour timeout
                cwd=os.path.dirname(script_path) if os.path.dirname(script_path) else None
            )
            
            return_code = result.returncode
            
            if result.returncode == 0:
                success = True
                logger.info(f"Scheduled job {script_name} completed successfully")
            else:
                error_message = f"Exit code {result.returncode}: {result.stderr}"
                logger.error(f"Scheduled job {script_name} failed: {error_message}")
                
        except subprocess.TimeoutExpired:
            return_code = -1
            error_message = "Job execution timeout (1 hour)"
            logger.error(f"Scheduled job {script_name} timed out")
        except Exception as e:
            return_code = -1
            error_message = str(e)
            logger.error(f"Scheduled job {script_name} execution error: {e}")
        
        # Finish execution tracking
        try:
            self.db.finish_execution(execution_id, return_code, error_message)
            logger.info(f"Finished execution tracking for scheduled job: {execution_id}")
        except Exception as e:
            logger.error(f"Failed to finish execution tracking: {e}")
        
        # Calculate next run time and update job statistics
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        next_run = self._calculate_next_run(job['interval_type'], job['interval_value'])
        
        # Update job statistics
        self._update_job_stats(job_id, success, duration, error_message, next_run)
    
    def _calculate_next_run(self, interval_type: str, interval_value: int) -> datetime:
        """Calculate the next run time based on interval."""
        now = datetime.now()
        
        if interval_type == 'minutes':
            return now + timedelta(minutes=interval_value)
        elif interval_type == 'hours':
            return now + timedelta(hours=interval_value)
        elif interval_type == 'days':
            return now + timedelta(days=interval_value)
        else:
            logger.warning(f"Unknown interval type: {interval_type}, defaulting to 1 hour")
            return now + timedelta(hours=1)
    
    def _update_job_stats(self, job_id: int, success: bool, duration: float, 
                         error_message: Optional[str], next_run: datetime):
        """Update job statistics after execution."""
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                
                # Update job stats
                cursor.execute("""
                    UPDATE scheduled_jobs 
                    SET last_run = CURRENT_TIMESTAMP,
                        last_run_status = ?,
                        last_run_duration = ?,
                        next_run = ?,
                        run_count = run_count + 1,
                        error_count = CASE WHEN ? THEN error_count ELSE error_count + 1 END,
                        last_error = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, ('success' if success else 'error', duration, next_run, 
                      success, error_message if not success else None, job_id))
                
                conn.commit()
                
        except Exception as e:
            logger.error(f"Error updating job stats for job {job_id}: {e}")
    
    def add_job(self, script_id: str, script_name: str, script_path: str,
                interval_type: str = 'hours', interval_value: int = 1,
                next_run: Optional[datetime] = None) -> Tuple[bool, str]:
        """Add a new scheduled job."""
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                
                # Calculate initial next run time
                # If next_run is not provided, default to running immediately (now)
                if next_run is None:
                    next_run = datetime.now()
                
                # Insert or update the job
                cursor.execute("""
                    INSERT OR REPLACE INTO scheduled_jobs 
                    (script_id, script_name, script_path, enabled, interval_type, interval_value, next_run)
                    VALUES (?, ?, ?, TRUE, ?, ?, ?)
                """, (script_id, script_name, script_path, interval_type, interval_value, next_run))
                
                conn.commit()
                
                logger.info(f"Added scheduled job: {script_name} (every {interval_value} {interval_type}, starting {next_run})")
                return True, f"Job scheduled to run every {interval_value} {interval_type}"
                
        except Exception as e:
            logger.error(f"Error adding scheduled job {script_id}: {e}")
            return False, str(e)
    
    def remove_job(self, script_id: str) -> Tuple[bool, str]:
        """Remove a scheduled job."""
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                
                # Delete the job
                cursor.execute("DELETE FROM scheduled_jobs WHERE script_id = ?", (script_id,))
                
                if cursor.rowcount > 0:
                    conn.commit()
                    logger.info(f"Removed scheduled job: {script_id}")
                    return True, "Job removed from scheduler"
                else:
                    return False, "Job not found"
                    
        except Exception as e:
            logger.error(f"Error removing scheduled job {script_id}: {e}")
            return False, str(e)
    
    def enable_job(self, script_id: str) -> Tuple[bool, str]:
        """Enable a scheduled job."""
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                
                # Enable the job
                cursor.execute("""
                    UPDATE scheduled_jobs 
                    SET enabled = TRUE, updated_at = CURRENT_TIMESTAMP 
                    WHERE script_id = ?
                """, (script_id,))
                
                if cursor.rowcount > 0:
                    conn.commit()
                    logger.info(f"Enabled scheduled job: {script_id}")
                    return True, "Job enabled"
                else:
                    return False, "Job not found"
                    
        except Exception as e:
            logger.error(f"Error enabling scheduled job {script_id}: {e}")
            return False, str(e)
    
    def disable_job(self, script_id: str) -> Tuple[bool, str]:
        """Disable a scheduled job."""
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                
                # Disable the job
                cursor.execute("""
                    UPDATE scheduled_jobs 
                    SET enabled = FALSE, updated_at = CURRENT_TIMESTAMP 
                    WHERE script_id = ?
                """, (script_id,))
                
                if cursor.rowcount > 0:
                    conn.commit()
                    logger.info(f"Disabled scheduled job: {script_id}")
                    return True, "Job disabled"
                else:
                    return False, "Job not found"
                    
        except Exception as e:
            logger.error(f"Error disabling scheduled job {script_id}: {e}")
            return False, str(e)
    
    def get_job_status(self, script_id: str) -> Optional[Dict]:
        """Get status of a specific job."""
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT script_id, script_name, enabled, interval_type, interval_value,
                           next_run, last_run, last_run_status, last_run_duration,
                           run_count, error_count, last_error
                    FROM scheduled_jobs 
                    WHERE script_id = ?
                """, (script_id,))
                
                row = cursor.fetchone()
                if row:
                    return dict(row)
                else:
                    return None
                    
        except Exception as e:
            logger.error(f"Error getting job status for {script_id}: {e}")
            return None
    
    def get_all_jobs(self) -> List[Dict]:
        """Get all scheduled jobs."""
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT script_id, script_name, enabled, interval_type, interval_value,
                           next_run, last_run, last_run_status, last_run_duration,
                           run_count, error_count, last_error, created_at, updated_at
                    FROM scheduled_jobs 
                    ORDER BY script_name
                """)
                
                return [dict(row) for row in cursor.fetchall()]
                
        except Exception as e:
            logger.error(f"Error getting all jobs: {e}")
            return []
    
    def update_job_schedule(self, script_id: str, interval_type: str, interval_value: int,
                           next_run: Optional[datetime] = None) -> Tuple[bool, str]:
        """Update the schedule for an existing job."""
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                
                # Calculate new next run time if not provided
                if next_run is None:
                    next_run = self._calculate_next_run(interval_type, interval_value)
                
                # Update the job
                cursor.execute("""
                    UPDATE scheduled_jobs 
                    SET interval_type = ?, interval_value = ?, next_run = ?, updated_at = CURRENT_TIMESTAMP 
                    WHERE script_id = ?
                """, (interval_type, interval_value, next_run, script_id))
                
                if cursor.rowcount > 0:
                    conn.commit()
                    logger.info(f"Updated schedule for job {script_id}: every {interval_value} {interval_type}")
                    return True, f"Schedule updated to every {interval_value} {interval_type}"
                else:
                    return False, "Job not found"
                    
        except Exception as e:
            logger.error(f"Error updating job schedule for {script_id}: {e}")
            return False, str(e)

# Global scheduler instance
scheduler = None

def get_scheduler() -> SchedulerManager:
    """Get the global scheduler instance."""
    global scheduler
    if scheduler is None:
        scheduler = SchedulerManager()
    return scheduler

def start_scheduler():
    """Start the global scheduler."""
    get_scheduler().start()

def stop_scheduler():
    """Stop the global scheduler."""
    if scheduler:
        scheduler.stop()