#!/usr/bin/env python3
"""
Action Logger for Soulseekarr
Centralized logging system for tracking all actions across scripts.
Stores actions in a JSON file that can be displayed in the Activity tab.
"""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

# Action log file location - try multiple locations
ACTION_LOG_LOCATIONS = [
    "/logs/action_history.json",  # Docker mount
    "logs/action_history.json",   # Relative to working directory
    "action_history.json"         # Fallback
]

ACTION_LOG_FILE = None
for location in ACTION_LOG_LOCATIONS:
    try:
        log_path = Path(location)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ACTION_LOG_FILE = location
        break
    except Exception:
        continue

if not ACTION_LOG_FILE:
    ACTION_LOG_FILE = "action_history.json"  # Ultimate fallback

ACTION_LOG_LOCK = threading.Lock()

class ActionLogger:
    """Centralized action logger for Soulseekarr."""
    
    def __init__(self):
        self.log_file = Path(ACTION_LOG_FILE)
        self.ensure_log_file_exists()
    
    def ensure_log_file_exists(self):
        """Ensure the action log file exists."""
        try:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            if not self.log_file.exists():
                with self.log_file.open('w') as f:
                    json.dump([], f)
            print(f"Action log file location: {self.log_file}")
        except Exception as e:
            print(f"Warning: Could not create action log file at {self.log_file}: {e}")
    
    def log_action(self, action_type, source, target=None, details=None, status="success", duration=None):
        """
        Log an action to the centralized action history.
        
        Args:
            action_type (str): Type of action (e.g., "script_execution", "file_rename", "api_call")
            source (str): Source of the action (e.g., script name, user action)
            target (str): Target of the action (e.g., file path, API endpoint)
            details (str): Additional details about the action
            status (str): Status of the action ("success", "error", "warning", "in_progress")
            duration (float): Duration of the action in seconds
        """
        action = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action_type": action_type,
            "source": source,
            "target": target or "N/A",
            "details": details or "N/A",
            "status": status,
            "duration": f"{duration:.2f}s" if duration else "N/A"
        }
        
        with ACTION_LOG_LOCK:
            try:
                # Read existing actions
                actions = []
                if self.log_file.exists() and self.log_file.stat().st_size > 0:
                    with self.log_file.open('r') as f:
                        actions = json.load(f)
                
                # Add new action at the beginning (newest first)
                actions.insert(0, action)
                
                # Keep only the last 1000 actions to prevent file from growing too large
                actions = actions[:1000]
                
                # Write back to file
                with self.log_file.open('w') as f:
                    json.dump(actions, f, indent=2)
                    
            except Exception as e:
                # Fallback - create new file if there's an issue
                print(f"Warning: Could not update action log: {e}")
                with self.log_file.open('w') as f:
                    json.dump([action], f, indent=2)
    
    def get_recent_actions(self, limit=100):
        """Get recent actions from the log."""
        with ACTION_LOG_LOCK:
            try:
                if not self.log_file.exists():
                    return []
                
                with self.log_file.open('r') as f:
                    actions = json.load(f)
                
                return actions[:limit]
                
            except Exception as e:
                print(f"Warning: Could not read action log: {e}")
                return []
    
    def log_script_start(self, script_name, parameters=None):
        """Log the start of a script execution."""
        details = f"Parameters: {parameters}" if parameters else "No parameters"
        self.log_action(
            action_type="script_start",
            source=script_name,
            details=details,
            status="in_progress"
        )
    
    def log_script_complete(self, script_name, duration=None, success=True, error=None):
        """Log the completion of a script execution."""
        status = "success" if success else "error"
        details = error if error else "Script completed successfully"
        self.log_action(
            action_type="script_complete",
            source=script_name,
            details=details,
            status=status,
            duration=duration
        )
    
    def log_file_operation(self, operation, source_path, target_path=None, status="success", details=None):
        """Log file operations like rename, move, delete."""
        self.log_action(
            action_type=f"file_{operation}",
            source=source_path,
            target=target_path,
            details=details,
            status=status
        )
    
    def log_api_call(self, service, endpoint, method="GET", status="success", details=None):
        """Log API calls to external services."""
        self.log_action(
            action_type="api_call",
            source=f"{service} API",
            target=f"{method} {endpoint}",
            details=details,
            status=status
        )
    
    def log_search(self, query, service, results_count=None, status="success"):
        """Log search operations."""
        details = f"Found {results_count} results" if results_count is not None else "Search completed"
        self.log_action(
            action_type="search",
            source=service,
            target=query,
            details=details,
            status=status
        )
    
    def log_download(self, item_name, source, status="success", details=None):
        """Log download operations."""
        self.log_action(
            action_type="download",
            source=source,
            target=item_name,
            details=details,
            status=status
        )

# Global instance
action_logger = ActionLogger()

# Convenience functions for easy importing
def log_action(action_type, source, target=None, details=None, status="success", duration=None):
    """Log an action using the global action logger."""
    action_logger.log_action(action_type, source, target, details, status, duration)

def log_script_start(script_name, parameters=None):
    """Log script start using the global action logger."""
    action_logger.log_script_start(script_name, parameters)

def log_script_complete(script_name, duration=None, success=True, error=None):
    """Log script completion using the global action logger."""
    action_logger.log_script_complete(script_name, duration, success, error)

def log_file_operation(operation, source_path, target_path=None, status="success", details=None):
    """Log file operation using the global action logger."""
    action_logger.log_file_operation(operation, source_path, target_path, status, details)

def log_api_call(service, endpoint, method="GET", status="success", details=None):
    """Log API call using the global action logger."""
    action_logger.log_api_call(service, endpoint, method, status, details)

def log_search(query, service, results_count=None, status="success"):
    """Log search using the global action logger."""
    action_logger.log_search(query, service, results_count, status)

def log_download(item_name, source, status="success", details=None):
    """Log download using the global action logger."""
    action_logger.log_download(item_name, source, status, details)

def get_recent_actions(limit=100):
    """Get recent actions using the global action logger."""
    return action_logger.get_recent_actions(limit)