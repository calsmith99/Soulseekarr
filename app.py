#!/usr/bin/env python3
"""
SoulSeekarr - Music Management Tools Web Interface
A Flask web application providing a Lidarr-style interface for managing music automation scripts.
"""

import os
import sys
import subprocess
import threading
import time
import json
import re
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, jsonify, request, send_from_directory, Response
from werkzeug.serving import make_server
import logging
import requests
from urllib.parse import urljoin
from database import get_db
import settings

app = Flask(__name__)
app.config['SECRET_KEY'] = 'soulseekarr-music-tools-secret-key-2025'

# Configure logging
logging.basicConfig(level=logging.INFO)  # Balanced logging level
logger = logging.getLogger(__name__)

# Disable Werkzeug access logging to reduce noise
logging.getLogger('werkzeug').setLevel(logging.WARNING)

# Global variables to track script execution
running_scripts = {}
script_outputs = {}
script_lock = threading.Lock()

# Database instance
db = get_db()

# Script execution tracking
script_execution_history = {}
script_history_lock = threading.Lock()

# Global variables for cron queue management
cron_queue = []
cron_running = False
cron_queue_lock = threading.Lock()
cron_runner_thread = None

# Global variable for service status cache
service_status_cache = {
    'navidrome': {'status': 'unknown', 'last_check': None, 'error': None},
    'lidarr': {'status': 'unknown', 'last_check': None, 'error': None},
    'slskd': {'status': 'unknown', 'last_check': None, 'error': None}
}
service_status_lock = threading.Lock()

def test_navidrome_service():
    """Test Navidrome service connectivity."""
    try:
        navidrome_url = os.environ.get('NAVIDROME_URL', 'http://localhost:4533')
        username = os.environ.get('NAVIDROME_USERNAME', '')
        password = os.environ.get('NAVIDROME_PASSWORD', '')
        
        if not navidrome_url or not username or not password:
            return False, "Missing Navidrome configuration"
        
        # Try to authenticate with Navidrome
        auth_url = urljoin(navidrome_url, '/auth/login')
        auth_data = {
            'username': username,
            'password': password
        }
        
        response = requests.post(auth_url, json=auth_data, timeout=10)
        if response.status_code == 200:
            return True, "Connected"
        else:
            return False, f"Authentication failed ({response.status_code})"
            
    except requests.exceptions.ConnectionError:
        return False, "Connection refused"
    except requests.exceptions.Timeout:
        return False, "Connection timeout"
    except Exception as e:
        return False, str(e)

def test_lidarr_service():
    """Test Lidarr service connectivity."""
    try:
        lidarr_url = os.environ.get('LIDARR_URL', 'http://localhost:8686')
        api_key = os.environ.get('LIDARR_API_KEY', '')
        
        if not lidarr_url or not api_key:
            return False, "Missing Lidarr configuration"
        
        # Test Lidarr API with system status endpoint
        status_url = urljoin(lidarr_url, '/api/v1/system/status')
        headers = {'X-Api-Key': api_key}
        
        response = requests.get(status_url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            version = data.get('version', 'Unknown')
            return True, f"Connected (v{version})"
        else:
            return False, f"API error ({response.status_code})"
            
    except requests.exceptions.ConnectionError:
        return False, "Connection refused"
    except requests.exceptions.Timeout:
        return False, "Connection timeout"
    except Exception as e:
        return False, str(e)

def test_slskd_service():
    """Test slskd service connectivity."""
    try:
        slskd_url = os.environ.get('SLSKD_URL', 'http://localhost:5030')
        username = os.environ.get('SLSKD_USERNAME', '')
        password = os.environ.get('SLSKD_PASSWORD', '')
        
        if not slskd_url or not username or not password:
            return False, "Missing slskd configuration"
        
        # Test slskd API with session endpoint
        session_url = urljoin(slskd_url, '/api/v0/session')
        auth = (username, password)
        
        response = requests.get(session_url, auth=auth, timeout=10)
        if response.status_code == 200:
            data = response.json()
            state = data.get('state', 'Unknown')
            return True, f"Connected ({state})"
        else:
            return False, f"API error ({response.status_code})"
            
    except requests.exceptions.ConnectionError:
        return False, "Connection refused"
    except requests.exceptions.Timeout:
        return False, "Connection timeout"
    except Exception as e:
        return False, str(e)

def update_service_status():
    """Update the status of all services."""
    with service_status_lock:
        # Test Navidrome
        success, message = test_navidrome_service()
        service_status_cache['navidrome'] = {
            'status': 'online' if success else 'offline',
            'last_check': datetime.now().isoformat(),
            'error': None if success else message
        }
        
        # Test Lidarr
        success, message = test_lidarr_service()
        service_status_cache['lidarr'] = {
            'status': 'online' if success else 'offline',
            'last_check': datetime.now().isoformat(),
            'error': None if success else message
        }
        
        # Test slskd
        success, message = test_slskd_service()
        service_status_cache['slskd'] = {
            'status': 'online' if success else 'offline',
            'last_check': datetime.now().isoformat(),
            'error': None if success else message
        }

def get_script_status(script_id):
    """Get the current status of a script."""
    with script_lock:
        # Check in-memory status first (for currently running scripts)
        status = running_scripts.get(script_id, {'running': False, 'pid': None, 'dry_run': False})
        
        # If not running, check database for last execution status
        if not status['running']:
            active_executions = db.get_active_executions()
            if script_id in active_executions:
                # Script is marked as running in database but not in memory (app restart case)
                db_status = active_executions[script_id]
                status.update({
                    'running': True,
                    'pid': db_status.get('pid'),
                    'start_time': db_status.get('start_time'),
                    'dry_run': db_status.get('dry_run', False),
                    'execution_id': db_status.get('execution_id')
                })
                # Update in-memory status
                running_scripts[script_id] = status
        
        # Parse progress from output for long-running scripts
        if status.get('running') and script_id in script_outputs:
            output_lines = script_outputs[script_id]
            logger.info(f"Checking progress for {script_id}, found {len(output_lines)} output lines")
            progress_info = parse_progress(output_lines, script_id)
            if progress_info:
                status['progress'] = progress_info
        
        return status

def parse_progress(output_lines, script_id):
    """Parse progress information from script output."""
    # Look for progress patterns in recent output lines
    main_progress = None
    sub_progress = None
    
    for line in reversed(output_lines[-50:]):  # Check last 50 lines
        # Strip timestamp prefix if present (e.g., "[19:43:50] PROGRESS: ...")
        clean_line = line
        if line.startswith('[') and '] ' in line:
            clean_line = line.split('] ', 1)[1] if '] ' in line else line
        
        # Main progress pattern: PROGRESS: [123/456] 67% - Processing: Artist - Album
        if clean_line.startswith('PROGRESS: [') and '%' in clean_line and ' - Processing:' in clean_line:
            try:
                # Extract [current/total] percentage
                bracket_part = clean_line.split('[')[1].split(']')[0]
                current, total = bracket_part.split('/')
                percentage_part = clean_line.split('] ')[1].split('%')[0]
                percentage = int(percentage_part)
                
                # Extract current item being processed
                processing_part = clean_line.split(' - Processing: ')[1] if ' - Processing: ' in clean_line else 'Processing...'
                
                main_progress = {
                    'current': int(current),
                    'total': int(total),
                    'percentage': percentage,
                    'current_item': processing_part.strip()
                }
                break
            except (IndexError, ValueError):
                continue
        
        # Sub-progress pattern: PROGRESS_SUB: Getting track listing for Album Name...
        elif clean_line.startswith('PROGRESS_SUB: ') and not sub_progress:
            try:
                sub_progress = {
                    'message': clean_line.replace('PROGRESS_SUB: ', '').strip()
                }
            except:
                continue
        
        # Legacy pattern: [123/456] Processing: Artist Name
        elif '[' in clean_line and '/' in clean_line and '] Processing:' in clean_line and not main_progress:
            try:
                # Extract numbers like [123/456]
                parts = clean_line.split('[')[1].split(']')[0].split('/')
                current = int(parts[0])
                total = int(parts[1])
                percentage = int((current / total) * 100)
                main_progress = {
                    'current': current,
                    'total': total,
                    'percentage': percentage,
                    'current_item': 'Processing...'
                }
                break
            except (IndexError, ValueError):
                continue
    
    # Return combined progress information
    if main_progress or sub_progress:
        result = {}
        if main_progress:
            result.update(main_progress)
        if sub_progress:
            result['sub_progress'] = sub_progress
        
        # Temporary debug log
        logger.info(f"Progress parsed for {script_id}: {result}")
        return result
    
    return None

def find_script_config(script_id):
    """Find script configuration from discovered scripts only."""
    # Only check discovered scripts from the scripts folder
    discovered_scripts = scan_scripts_folder()
    if script_id in discovered_scripts:
        return discovered_scripts[script_id]
    
    return None

def scan_scripts_folder():
    """Scan the scripts folder and return information about available Python scripts."""
    # Scripts folder path
    scripts_dir = os.path.join(os.getcwd(), 'scripts')
    
    discovered_scripts = {}
    
    try:
        if not os.path.exists(scripts_dir):
            logger.warning(f"Scripts directory not found: {scripts_dir}")
            return discovered_scripts
        
        logger.debug(f"Using scripts directory: {scripts_dir}")
        
        for filename in os.listdir(scripts_dir):
            # Skip system files, hidden files, and Python cache files
            if (filename.endswith('.py') and 
                not filename.startswith('__') and 
                not filename.startswith('._') and 
                not filename.startswith('.') and
                filename != '__pycache__'):
                script_id = filename[:-3]  # Remove .py extension
                script_path = os.path.join(scripts_dir, filename)
                
                # Extract metadata from script
                metadata = extract_script_metadata(script_path)
                
                discovered_scripts[script_id] = {
                    'name': metadata.get('name', script_id.replace('_', ' ').title()),
                    'description': metadata.get('description', 'Python script'),
                    'script': f'python3 -u scripts/{filename}',
                    'supports_dry_run': metadata.get('supports_dry_run', True),
                    'section': metadata.get('section', 'commands'),
                    'author': metadata.get('author', ''),
                    'version': metadata.get('version', ''),
                    'tags': metadata.get('tags', [])
                }
                
        logger.debug(f"Discovered {len(discovered_scripts)} scripts in {scripts_dir}")
        
    except Exception as e:
        logger.error(f"Error scanning scripts folder: {e}")
    
    return discovered_scripts

def extract_script_metadata(script_path):
    """Extract metadata from a Python script's docstring and comments."""
    metadata = {
        'name': None,
        'description': 'Python script',
        'supports_dry_run': True,
        'section': 'commands',
        'author': '',
        'version': '',
        'tags': []
    }
    
    try:
        with open(script_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # Look for module docstring
        if '"""' in content:
            start = content.find('"""')
            if start != -1:
                end = content.find('"""', start + 3)
                if end != -1:
                    docstring = content[start + 3:end].strip()
                    lines = docstring.split('\n')
                    
                    # First line is usually the description
                    if lines:
                        first_line = lines[0].strip()
                        if first_line:
                            metadata['description'] = first_line
                    
                    # Look for metadata in docstring
                    for line in lines:
                        line = line.strip()
                        if line.startswith('Name:'):
                            metadata['name'] = line[5:].strip()
                        elif line.startswith('Author:'):
                            metadata['author'] = line[7:].strip()
                        elif line.startswith('Version:'):
                            metadata['version'] = line[8:].strip()
                        elif line.startswith('Section:'):
                            metadata['section'] = line[8:].strip().lower()
                        elif line.startswith('Tags:'):
                            tags = line[5:].strip()
                            metadata['tags'] = [tag.strip() for tag in tags.split(',') if tag.strip()]
                        elif line.startswith('Supports dry run:'):
                            supports = line[17:].strip().lower()
                            metadata['supports_dry_run'] = supports in ['true', 'yes', '1']
        
        # Check for specific patterns in code
        if '--dry-run' in content or 'DRY_RUN' in content:
            metadata['supports_dry_run'] = True
        elif 'dry_run' not in content.lower():
            metadata['supports_dry_run'] = False
            
        # Determine section based on filename patterns
        filename = os.path.basename(script_path).lower()
        if 'test' in filename or 'validate' in filename:
            metadata['section'] = 'tests'
        elif 'monitor' in filename or 'queue' in filename or 'process' in filename:
            metadata['section'] = 'commands'
            
    except Exception as e:
        logger.warning(f"Could not extract metadata from {script_path}: {e}")
    
    return metadata

def get_script_execution_history(script_id):
    """Get execution history for a script."""
    with script_history_lock:
        return script_execution_history.get(script_id, {
            'last_execution': None,
            'last_duration': None,
            'execution_count': 0,
            'last_status': None
        })

def update_script_execution_history(script_id, start_time, end_time, status):
    """Update execution history for a script."""
    with script_history_lock:
        if script_id not in script_execution_history:
            script_execution_history[script_id] = {
                'execution_count': 0,
                'last_execution': None,
                'last_duration': None,
                'last_status': None
            }
        
        duration = (end_time - start_time).total_seconds()
        script_execution_history[script_id].update({
            'last_execution': end_time.isoformat(),
            'last_duration': duration,
            'execution_count': script_execution_history[script_id]['execution_count'] + 1,
            'last_status': status
        })

def run_script_thread(script_id, script_path, input_value=None, script_env=None):
    """Run a script in a separate thread and capture output."""
    start_time = datetime.now()
    execution_id = None
    
    try:
        # Import action logger
        from action_logger import log_script_start, log_script_complete
        
        # Log script start
        discovered_scripts = scan_scripts_folder()
        script_config = discovered_scripts.get(script_id, {})
        script_name = script_config.get('name', script_id)
        log_script_start(script_name, input_value)
        
        with script_lock:
            # Determine if this is a dry run
            is_dry_run = script_env and script_env.get('DRY_RUN') == 'true'
            
            running_scripts[script_id] = {
                'running': True,
                'pid': None,
                'start_time': start_time,
                'end_time': None,
                'dry_run': is_dry_run
            }
            script_outputs[script_id] = []

        logger.debug(f"Starting script: {script_path}")
        
        # Handle Python scripts differently from shell scripts
        if script_path.startswith('python'):
            # For Python commands like "python -u script.py" or "python -u script.py --flag"
            cmd = script_path.split()
            # Don't try to make Python scripts executable or use shell wrapper
        else:
            # For shell scripts on Windows, we'll use shell=True in Popen
            cmd = [script_path]
        
        # Use provided environment or copy current one
        if script_env is None:
            script_env = os.environ.copy()
        
        # Build command with input if provided
        if input_value:
            if script_path.startswith('python'):
                # Insert the music directory argument before any existing flags
                # Find where to insert (after the .py file)
                insert_index = 2  # After "python" and "-u" and "script.py"
                if len(cmd) > 2 and cmd[2].endswith('.py'):
                    insert_index = 3
                cmd.insert(insert_index, input_value)
                
                # Add --dry-run flag if supported and enabled
                if script_env.get('DRY_RUN') == 'true':
                    cmd.append('--dry-run')
            else:
                cmd = [script_path, input_value]
        else:
            if not script_path.startswith('python'):
                cmd = [script_path]
            else:
                # For Python scripts without input, just add dry-run if needed
                if script_env.get('DRY_RUN') == 'true':
                    cmd.append('--dry-run')
        
        # Start the process (reduced logging)
        # Use shell=True on Windows for better compatibility
        shell_needed = not script_path.startswith('python') and os.name == 'nt'
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            bufsize=1,
            cwd=os.getcwd(),
            env=script_env,
            shell=shell_needed
        )
        
        # Start execution tracking in database
        execution_id = db.start_execution(script_id, script_name, is_dry_run, process.pid)
        
        with script_lock:
            running_scripts[script_id]['pid'] = process.pid
            running_scripts[script_id]['execution_id'] = execution_id

        # Read output line by line
        for line in iter(process.stdout.readline, ''):
            if line:
                timestamp = datetime.now().strftime('%H:%M:%S')
                output_line = f"[{timestamp}] {line.rstrip()}"
                
                # Determine log level
                log_level = 'info'
                line_lower = line.lower()
                if 'error' in line_lower or 'exception' in line_lower or 'failed' in line_lower:
                    log_level = 'error'
                elif 'warning' in line_lower or 'warn' in line_lower:
                    log_level = 'warning'
                
                # Store in database
                if execution_id:
                    db.add_log_line(execution_id, line.rstrip(), log_level)
                
                with script_lock:
                    script_outputs[script_id].append(output_line)
                    
                    # Temporary debug: Log progress lines when captured
                    if 'PROGRESS:' in output_line or 'PROGRESS_SUB:' in output_line:
                        logger.info(f"Captured progress line for {script_id}: {output_line.strip()}")
                    
                    # Keep only last 1000 lines to prevent memory issues
                    if len(script_outputs[script_id]) > 1000:
                        script_outputs[script_id] = script_outputs[script_id][-1000:]

        # Wait for process to complete
        process.wait()
        return_code = process.returncode
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        # Add completion message
        timestamp = end_time.strftime('%H:%M:%S')
        completion_msg = f"[{timestamp}] Script completed with exit code: {return_code}"
        
        # Store completion in database
        if execution_id:
            db.add_log_line(execution_id, f"Script completed with exit code: {return_code}")
            db.finish_execution(execution_id, return_code)
        
        with script_lock:
            script_outputs[script_id].append(completion_msg)
            running_scripts[script_id]['running'] = False
            running_scripts[script_id]['end_time'] = end_time
            running_scripts[script_id]['return_code'] = return_code

        # Log script completion
        success = return_code == 0
        error_msg = None if success else f"Script failed with exit code {return_code}"
        log_script_complete(script_name, duration, success, error_msg)
        
        # Update execution history
        status = 'success' if success else 'error'
        update_script_execution_history(script_id, start_time, end_time, status)

        logger.debug(f"Script {script_id} completed with return code: {return_code}")

    except Exception as e:
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        error_msg = f"[ERROR] Failed to run script: {str(e)}"
        logger.error(error_msg)
        
        # Store error in database
        if execution_id:
            db.add_log_line(execution_id, f"Failed to run script: {str(e)}", 'error')
            db.finish_execution(execution_id, -1, str(e))
        
        # Log script failure
        try:
            from action_logger import log_script_complete
            log_script_complete(script_name, duration, False, str(e))
        except:
            pass  # Don't fail if action logger fails
        
        # Update execution history
        update_script_execution_history(script_id, start_time, end_time, 'error')
        
        with script_lock:
            script_outputs[script_id].append(error_msg)
            running_scripts[script_id]['running'] = False
            running_scripts[script_id]['end_time'] = end_time
            running_scripts[script_id]['error'] = str(e)

@app.route('/')
def index():
    """Main page with script buttons."""
    # Get scripts from scripts folder only
    discovered_scripts = scan_scripts_folder()
    
    # Group scripts by section
    script_sections = {}
    for script_id, config in discovered_scripts.items():
        section = config.get('section', 'commands')
        if section not in script_sections:
            script_sections[section] = {}
        script_sections[section][script_id] = config
    
    # Ensure sections are in the right order
    ordered_sections = {}
    if 'commands' in script_sections:
        ordered_sections['commands'] = script_sections['commands']
    if 'tests' in script_sections:
        ordered_sections['tests'] = script_sections['tests']
    
    return render_template('index.html', 
                         script_sections=ordered_sections,
                         scripts=discovered_scripts)  # Keep for compatibility

@app.route('/run_script/<script_id>', methods=['POST'])
def run_script_api(script_id):
    """API endpoint to run a script (for the new UI)."""
    script_config = find_script_config(script_id)
    
    if not script_config:
        logger.error(f"❌ Script not found: {script_id}")
        return jsonify({'success': False, 'error': 'Script not found'}), 404
    
    script_path = script_config['script']
    
    # Check if script is already running
    status = get_script_status(script_id)
    if status['running']:
        return jsonify({'success': False, 'error': 'Script is already running'}), 400
    
    # Get dry run setting from request body
    request_data = request.get_json() or {}
    dry_run = request_data.get('dry_run', False)
    
    # Check if script file exists
    if script_path.startswith('python'):
        script_parts = script_path.split()
        actual_script_file = None
        for part in script_parts:
            if part.endswith('.py'):
                actual_script_file = part
                break
        
        if actual_script_file and not os.path.exists(actual_script_file):
            logger.error(f"❌ Python script file not found: {actual_script_file}")
            return jsonify({'success': False, 'error': f'Python script file not found: {actual_script_file}'}), 404
    else:
        if not os.path.exists(script_path):
            logger.error(f"❌ Shell script file not found: {script_path}")
            return jsonify({'success': False, 'error': f'Script file not found: {script_path}'}), 404
    
    # Set up environment with dry run setting
    script_env = os.environ.copy()
    if script_config.get('supports_dry_run'):
        script_env['DRY_RUN'] = 'true' if dry_run else 'false'
    
    # Start script in background thread
    thread = threading.Thread(target=run_script_thread, args=(script_id, script_path, None, script_env))
    thread.daemon = True
    thread.start()
    
    mode = "Dry-Run" if (dry_run and script_config.get('supports_dry_run')) else "Live"
    return jsonify({
        'success': True, 
        'message': f'Started {script_config["name"]} ({mode})', 
        'script_id': script_id
    })

@app.route('/run/<script_id>', methods=['GET', 'POST'])
def run_script(script_id):
    """Start running a script."""
    script_config = find_script_config(script_id)
    if script_config is None:
        logger.error(f"❌ Script not found: {script_id}")
        return jsonify({'error': 'Script not found'}), 404
    script_path = script_config['script']
    
    # Get dry_run parameter from request
    dry_run = request.args.get('dry_run', 'false').lower() == 'true'
    
    # Handle input parameters for POST requests
    input_value = None
    if request.method == 'POST' and script_config.get('has_input'):
        input_value = request.json.get('input') if request.is_json else request.form.get('input')
        if not input_value:
            return jsonify({'error': 'Input parameter required'}), 400
        # Also check for dry_run in POST body
        if request.is_json:
            dry_run = request.json.get('dry_run', False)
    
    # Check if script file exists (handle Python commands differently)
    if script_path.startswith('python'):
        # For Python commands, extract the actual script file name
        script_parts = script_path.split()
        # Find the .py file in the command (should be the first .py file found)
        actual_script_file = None
        for part in script_parts:
            if part.endswith('.py'):
                actual_script_file = part
                break
        
        if actual_script_file and not os.path.exists(actual_script_file):
            logger.error(f"❌ Python script file not found: {actual_script_file}")
            return jsonify({'error': f'Python script file not found: {actual_script_file}'}), 404
        elif not actual_script_file:
            logger.error(f"❌ No .py file found in command: {script_path}")
            return jsonify({'error': f'No .py file found in command: {script_path}'}), 404
    else:
        # For shell scripts, check the path directly
        if not os.path.exists(script_path):
            logger.error(f"❌ Shell script file not found: {script_path}")
            return jsonify({'error': f'Script file not found: {script_path}'}), 404
    
    # Check if script is already running
    status = get_script_status(script_id)
    if status['running']:
        return jsonify({'error': 'Script is already running'}), 400
    
    # Set DRY_RUN environment variable for the script
    script_env = os.environ.copy()
    if script_config.get('supports_dry_run'):
        script_env['DRY_RUN'] = 'true' if dry_run else 'false'
    
    # Start script in background thread
    thread = threading.Thread(target=run_script_thread, args=(script_id, script_path, input_value, script_env))
    thread.daemon = True
    thread.start()
    
    mode = "Dry-Run" if dry_run else "Live"
    return jsonify({'message': f'Started {script_config["name"]} ({mode})', 'script_id': script_id})

@app.route('/stop/<script_id>')
def stop_script(script_id):
    """Stop a running script."""
    script_config = find_script_config(script_id)
    if script_config is None:
        return jsonify({'error': 'Script not found'}), 404
    
    status = get_script_status(script_id)
    if not status['running']:
        return jsonify({'error': 'Script is not running'}), 400
    
    pid = status.get('pid')
    if pid:
        try:
            # First try SIGTERM (graceful shutdown)
            logger.info(f"Sending SIGTERM to script {script_id} (PID: {pid})")
            os.kill(pid, 15)  # SIGTERM
            
            # Wait a moment to see if process terminates gracefully
            import time
            time.sleep(1)
            
            # Check if process is still running
            try:
                os.kill(pid, 0)  # This doesn't kill, just checks if process exists
                # Process still exists, try SIGKILL
                logger.warning(f"Script {script_id} didn't respond to SIGTERM, sending SIGKILL")
                os.kill(pid, 9)  # SIGKILL
            except ProcessLookupError:
                # Process already terminated from SIGTERM
                pass
            
            with script_lock:
                running_scripts[script_id]['running'] = False
                running_scripts[script_id]['end_time'] = datetime.now()
            return jsonify({'message': f'Stopped script {script_id}'})
        except ProcessLookupError:
            # Process already terminated
            with script_lock:
                running_scripts[script_id]['running'] = False
                running_scripts[script_id]['end_time'] = datetime.now()
            return jsonify({'message': f'Script {script_id} was already stopped'})
        except Exception as e:
            logger.error(f"Failed to stop script {script_id}: {str(e)}")
            return jsonify({'error': f'Failed to stop script: {str(e)}'}), 500
    
    return jsonify({'error': 'No process ID found'}), 400

@app.route('/status/<script_id>')
def get_script_status_api(script_id):
    """Get the status of a script."""
    script_config = find_script_config(script_id)
    if script_config is None:
        return jsonify({'error': 'Script not found'}), 404
    
    status = get_script_status(script_id)
    return jsonify(status)

@app.route('/output/<script_id>')
def get_script_output(script_id):
    """Get the output of a script."""
    script_config = find_script_config(script_id)
    if script_config is None:
        return jsonify({'error': 'Script not found'}), 404
    
    lines_param = request.args.get('lines', '100')
    try:
        max_lines = int(lines_param)
    except ValueError:
        max_lines = 100
    
    with script_lock:
        output_lines = script_outputs.get(script_id, [])
        # Return last N lines
        if max_lines > 0:
            output_lines = output_lines[-max_lines:]
    
    return jsonify({'output': output_lines})

@app.route('/clear/<script_id>')
def clear_script_output(script_id):
    """Clear the output of a script."""
    script_config = find_script_config(script_id)
    if script_config is None:
        return jsonify({'error': 'Script not found'}), 404
    
    with script_lock:
        script_outputs[script_id] = []
    
    return jsonify({'message': 'Output cleared'})

# Cron Job Queue Management Routes
@app.route('/cron/queue', methods=['GET'])
def get_cron_queue():
    """Get the current cron queue."""
    with cron_queue_lock:
        return jsonify({
            'queue': cron_queue.copy(),
            'running': cron_running
        })

@app.route('/cron/add', methods=['POST'])
def add_to_cron_queue():
    """Add a script to the cron queue."""
    data = request.get_json()
    script_id = data.get('script_id')
    input_value = data.get('input')
    
    script_config = find_script_config(script_id)
    if script_config is None:
        return jsonify({'error': 'Script not found'}), 404
    queue_item = {
        'id': f"{script_id}_{int(time.time())}",
        'script_id': script_id,
        'name': script_config['name'],
        'script_path': script_config['script'],
        'input_value': input_value,
        'added_at': datetime.now().isoformat(),
        'status': 'queued'
    }
    
    with cron_queue_lock:
        cron_queue.append(queue_item)
    
    return jsonify({'message': f'Added {script_config["name"]} to queue', 'queue_item': queue_item})

@app.route('/cron/remove', methods=['POST'])
def remove_from_cron_queue():
    """Remove a script from the cron queue."""
    data = request.get_json()
    queue_id = data.get('queue_id')
    
    with cron_queue_lock:
        global cron_queue
        cron_queue = [item for item in cron_queue if item['id'] != queue_id]
    
    return jsonify({'message': 'Removed from queue'})

@app.route('/cron/reorder', methods=['POST'])
def reorder_cron_queue():
    """Reorder the cron queue."""
    data = request.get_json()
    new_order = data.get('queue_ids', [])
    
    with cron_queue_lock:
        global cron_queue
        # Reorder based on provided queue_ids order
        ordered_queue = []
        for queue_id in new_order:
            for item in cron_queue:
                if item['id'] == queue_id:
                    ordered_queue.append(item)
                    break
        cron_queue = ordered_queue
    
    return jsonify({'message': 'Queue reordered'})

@app.route('/cron/start', methods=['POST'])
def start_cron_queue():
    """Start processing the cron queue."""
    global cron_runner_thread, cron_running
    
    if cron_running:
        return jsonify({'error': 'Cron queue is already running'}), 400
    
    with cron_queue_lock:
        if not cron_queue:
            return jsonify({'error': 'Queue is empty'}), 400
    
    cron_runner_thread = threading.Thread(target=run_cron_queue)
    cron_runner_thread.daemon = True
    cron_runner_thread.start()
    
    return jsonify({'message': 'Cron queue started'})

@app.route('/cron/stop', methods=['POST'])
def stop_cron_queue():
    """Stop processing the cron queue."""
    global cron_running
    cron_running = False
    return jsonify({'message': 'Cron queue will stop after current script'})

def run_cron_queue():
    """Run scripts in the cron queue sequentially."""
    global cron_running
    cron_running = True
    
    # Get start delay from environment variable (in minutes)
    start_delay = int(os.environ.get('CRON_START_DELAY_MINUTES', 0))
    
    if start_delay > 0:
        logger.info(f"Cron queue starting in {start_delay} minutes...")
        time.sleep(start_delay * 60)
    
    try:
        while cron_running:
            with cron_queue_lock:
                if not cron_queue:
                    break
                
                current_item = cron_queue[0]
                current_item['status'] = 'running'
                current_item['started_at'] = datetime.now().isoformat()
            
            logger.info(f"Running cron job: {current_item['name']}")
            
            # Run the script
            script_path = current_item['script_path']
            input_value = current_item.get('input_value')
            
            try:
                # Make script executable
                os.chmod(script_path, 0o755)
                
                # Build command
                if input_value:
                    cmd = ['/bin/sh', script_path, input_value]
                else:
                    cmd = ['/bin/sh', script_path]
                
                # Run the script and wait for completion
                process = subprocess.run(
                    cmd,
                    cwd='/data',
                    capture_output=True,
                    text=True,
                    timeout=3600  # 1 hour timeout
                )
                
                current_item['status'] = 'completed' if process.returncode == 0 else 'failed'
                current_item['return_code'] = process.returncode
                current_item['completed_at'] = datetime.now().isoformat()
                
                logger.info(f"Cron job {current_item['name']} {'completed' if process.returncode == 0 else 'failed'}")
                
            except subprocess.TimeoutExpired:
                current_item['status'] = 'timeout'
                current_item['completed_at'] = datetime.now().isoformat()
                logger.error(f"Cron job {current_item['name']} timed out")
            except Exception as e:
                current_item['status'] = 'error'
                current_item['error'] = str(e)
                current_item['completed_at'] = datetime.now().isoformat()
                logger.error(f"Cron job {current_item['name']} error: {e}")
            
            # Remove completed item from queue
            with cron_queue_lock:
                if cron_queue and cron_queue[0]['id'] == current_item['id']:
                    cron_queue.pop(0)
            
            # Small delay between jobs
            time.sleep(2)
    
    finally:
        cron_running = False
        logger.info("Cron queue stopped")

@app.route('/activity/history')
def get_activity_history():
    """Get activity history for the Activity tab."""
    try:
        from action_logger import get_recent_actions
        actions = get_recent_actions(limit=200)
        return jsonify({'success': True, 'actions': actions})
    except Exception as e:
        logger.error(f"Failed to get activity history: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/scripts/available')
def get_available_scripts():
    """Get all available scripts with execution history."""
    try:
        logger.info(f"Getting available scripts. Current working directory: {os.getcwd()}")
        
        # Get scripts from scripts folder only
        discovered_scripts = scan_scripts_folder()
        
        logger.info(f"Total available scripts: {len(discovered_scripts)} (discovered from scripts folder)")
        
        # Add execution history to each script
        scripts_with_history = {}
        for script_id, config in discovered_scripts.items():
            history = get_script_execution_history(script_id)
            status = get_script_status(script_id)
            
            scripts_with_history[script_id] = {
                **config,
                'execution_history': history,
                'current_status': status
            }
        
        logger.info(f"Returning {len(scripts_with_history)} scripts")
        return jsonify(scripts_with_history)
    except Exception as e:
        logger.error(f"Error getting available scripts: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/library/expiring-albums')
def get_expiring_albums():
    """Get albums that will expire soon from database."""
    try:
        # Get from database
        summary = db.get_expiring_albums_summary()
        return jsonify(summary)
    except Exception as e:
        logger.error(f"Error getting expiring albums: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/library/album/<path:album_key>/tracks')
def get_album_tracks(album_key):
    """Get tracks for a specific album."""
    try:
        tracks = db.get_album_tracks(album_key)
        
        # Format tracks for display
        formatted_tracks = []
        for track in tracks:
            formatted_tracks.append({
                'file_name': track['file_name'],
                'track_title': track['track_title'],
                'file_size_mb': round(track['file_size_mb'], 2),
                'days_old': track['days_old'],
                'last_modified': track['last_modified'].isoformat()
            })
        
        return jsonify({
            'album_key': album_key,
            'tracks': formatted_tracks,
            'total_tracks': len(formatted_tracks)
        })
    except Exception as e:
        logger.error(f"Error getting album tracks: {e}")
        return jsonify({'error': str(e)}), 500

# Helper function to ensure playlists have active flags
def ensure_playlist_active_flags():
    """Ensure all playlists have an 'active' flag (default to True)"""
    try:
        playlists_file = Path('work/spotify_playlists.json')
        
        if not playlists_file.exists():
            return
        
        with open(playlists_file, 'r') as f:
            playlists = json.load(f)
        
        # Add active flag to any playlists that don't have it
        updated = False
        for playlist in playlists:
            if 'active' not in playlist:
                playlist['active'] = True
                updated = True
        
        if updated:
            with open(playlists_file, 'w') as f:
                json.dump(playlists, f, indent=2)
            logger.info(f"Added 'active' flags to playlist configurations")
        
    except Exception as e:
        logger.error(f"Error ensuring playlist active flags: {e}")

# Playlist Management Routes
@app.route('/settings/playlists', methods=['GET'])
def get_playlists():
    """Get all configured playlists."""
    try:
        playlists_file = Path('work/spotify_playlists.json')
        
        if not playlists_file.exists():
            return jsonify([])
        
        with open(playlists_file, 'r') as f:
            playlists = json.load(f)
        
        return jsonify(playlists)
    except Exception as e:
        logger.error(f"Error getting playlists: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/settings/playlists', methods=['POST'])
def add_playlist():
    """Add a new playlist."""
    try:
        data = request.get_json()
        url = data.get('url', '').strip()
        name = data.get('name', '').strip()
        
        if not url:
            return jsonify({'error': 'URL is required'}), 400
        
        if 'spotify.com/playlist/' not in url:
            return jsonify({'error': 'Invalid Spotify playlist URL'}), 400
        
        # Load existing playlists
        playlists_file = Path('work/spotify_playlists.json')
        playlists_file.parent.mkdir(exist_ok=True)
        
        playlists = []
        if playlists_file.exists():
            with open(playlists_file, 'r') as f:
                playlists = json.load(f)
        
        # Check for duplicates
        for playlist in playlists:
            if playlist['url'] == url:
                return jsonify({'error': 'Playlist already exists'}), 400
        
        # Add new playlist
        new_playlist = {
            'url': url,
            'name': name or None,
            'added_at': datetime.now().isoformat(),
            'last_sync': None
        }
        
        playlists.append(new_playlist)
        
        # Save updated playlists
        with open(playlists_file, 'w') as f:
            json.dump(playlists, f, indent=2)
        
        # Update script configuration for dynamic discovery
        ensure_playlist_active_flags()
        
        logger.info(f"Added playlist: {url}")
        return jsonify({'message': 'Playlist added successfully', 'playlist': new_playlist})
        
    except Exception as e:
        logger.error(f"Error adding playlist: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/settings/playlists', methods=['DELETE'])
def delete_playlist():
    """Delete a playlist by index."""
    try:
        data = request.get_json()
        index = data.get('index')
        
        if index is None:
            return jsonify({'error': 'Index is required'}), 400
        
        playlists_file = Path('work/spotify_playlists.json')
        
        if not playlists_file.exists():
            return jsonify({'error': 'No playlists found'}), 404
        
        with open(playlists_file, 'r') as f:
            playlists = json.load(f)
        
        if index < 0 or index >= len(playlists):
            return jsonify({'error': 'Invalid playlist index'}), 400
        
        removed_playlist = playlists.pop(index)
        
        # Save updated playlists
        with open(playlists_file, 'w') as f:
            json.dump(playlists, f, indent=2)
        
        # Update script configurations
        ensure_playlist_active_flags()
        
        logger.info(f"Deleted playlist: {removed_playlist['url']}")
        return jsonify({'message': 'Playlist deleted successfully'})
        
    except Exception as e:
        logger.error(f"Error deleting playlist: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/settings/playlists/sync-all', methods=['POST'])
def sync_all_playlists():
    """Sync all configured playlists."""
    try:
        playlists_file = Path('work/spotify_playlists.json')
        
        if not playlists_file.exists():
            return jsonify({'error': 'No playlists found'}), 404
        
        with open(playlists_file, 'r') as f:
            playlists = json.load(f)
        
        if not playlists:
            return jsonify({'error': 'No playlists configured'}), 400
        
        def run_sync_all():
            try:
                script_path = Path('scripts/spotify_playlist_monitor.py')
                # Run the main script without arguments - it will process all active playlists
                cmd = [sys.executable, str(script_path)]
                
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=os.environ.copy())
                
                if result.returncode == 0:
                    logger.info("Successfully completed playlist monitoring for all active playlists")
                else:
                    logger.error(f"Playlist monitoring failed: {result.stderr}")
                    
            except Exception as e:
                logger.error(f"Error running playlist monitoring: {e}")
        
        # Start sync in background thread
        sync_thread = threading.Thread(target=run_sync_all)
        sync_thread.daemon = True
        sync_thread.start()
        
        return jsonify({'message': f'Started syncing {len(playlists)} playlists', 'total_artists_added': 0})  # Placeholder
        
    except Exception as e:
        logger.error(f"Error syncing all playlists: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/settings/playlists/clear', methods=['DELETE'])
def clear_all_playlists():
    """Clear all configured playlists."""
    try:
        playlists_file = Path('work/spotify_playlists.json')
        
        if playlists_file.exists():
            playlists_file.unlink()
        
        logger.info("Cleared all playlists")
        return jsonify({'message': 'All playlists cleared successfully'})
        
    except Exception as e:
        logger.error(f"Error clearing playlists: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/logs/<script_id>')
def get_script_logs(script_id):
    """Get logs for a specific script."""
    script_config = find_script_config(script_id)
    if script_config is None:
        return jsonify({'error': 'Script not found'}), 404
    
    # Try to get logs from database first, fall back to memory
    try:
        db_logs = db.get_script_logs(script_id, limit=1000)
        if db_logs:
            return jsonify({
                'logs': db_logs,
                'script_id': script_id,
                'script_name': script_config.get('name', script_id),
                'source': 'database'
            })
    except Exception as e:
        logger.error(f"Error getting logs from database: {e}")
    
    # Fallback to in-memory logs
    with script_lock:
        output_lines = script_outputs.get(script_id, [])
    
    return jsonify({
        'logs': output_lines,
        'script_id': script_id,
        'script_name': script_config.get('name', script_id),
        'source': 'memory'
    })

@app.route('/logs/<script_id>', methods=['DELETE'])
def clear_script_logs(script_id):
    """Clear logs for a specific script."""
    script_config = find_script_config(script_id)
    if script_config is None:
        return jsonify({'error': 'Script not found'}), 404
    
    try:
        # Clear from database
        db.clear_script_logs(script_id)
        
        # Clear from memory
        with script_lock:
            script_outputs[script_id] = []
        
        return jsonify({'message': 'Logs cleared successfully'})
    except Exception as e:
        logger.error(f"Error clearing logs: {e}")
        return jsonify({'error': 'Failed to clear logs'}), 500

@app.route('/logs/<script_id>/download')
def download_script_logs(script_id):
    """Download logs for a specific script as a text file."""
    script_config = find_script_config(script_id)
    if script_config is None:
        return jsonify({'error': 'Script not found'}), 404
    
    # Get logs from database first, fall back to memory
    try:
        db_logs = db.get_script_logs(script_id, limit=10000)
        output_lines = db_logs if db_logs else script_outputs.get(script_id, [])
    except Exception as e:
        logger.error(f"Error getting logs from database: {e}")
        with script_lock:
            output_lines = script_outputs.get(script_id, [])
    
    if not output_lines:
        return jsonify({'error': 'No logs available'}), 404
    
    # Create log content
    log_content = '\n'.join(output_lines)
    
    # Create filename
    script_name = script_config.get('name', script_id)
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', script_name.lower())
    filename = f"{safe_name}_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    
    # Return as file
    return Response(
        log_content,
        mimetype='text/plain',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )

@app.route('/api/execution-queue')
def get_execution_queue_api():
    """Get the execution queue from database."""
    try:
        executions = db.get_execution_queue(limit=100)
        
        # Format for frontend
        queue_items = []
        for execution in executions:
            queue_items.append({
                'scriptId': execution['script_id'],
                'name': execution['script_name'],
                'startTime': execution['start_time'].isoformat(),
                'endTime': execution['end_time'].isoformat() if execution['end_time'] else None,
                'status': execution['status'],
                'duration_seconds': execution['duration_seconds'],
                'dry_run': execution['dry_run'],
                'execution_id': execution['id']
            })
        
        return jsonify({'queue': queue_items})
    except Exception as e:
        logger.error(f"Error getting execution queue: {e}")
        return jsonify({'error': 'Failed to get execution queue'}), 500

@app.route('/api/execution-stats')
def get_execution_stats_api():
    """Get execution statistics."""
    try:
        stats = db.get_execution_stats()
        return jsonify(stats)
    except Exception as e:
        logger.error(f"Error getting execution stats: {e}")
        return jsonify({'error': 'Failed to get execution stats'}), 500

@app.route('/api/execution/<int:execution_id>/stop', methods=['POST'])
def stop_execution_api(execution_id):
    """Stop a running execution."""
    try:
        data = request.get_json() or {}
        reason = data.get('reason', 'Manually stopped via web interface')
        
        success = db.stop_execution(execution_id, reason)
        if success:
            return jsonify({
                'success': True,
                'message': f'Execution {execution_id} stopped successfully'
            })
        else:
            return jsonify({
                'success': False,
                'message': f'Failed to stop execution {execution_id} (not running or not found)'
            }), 400
    except Exception as e:
        logger.error(f"Error stopping execution {execution_id}: {e}")
        return jsonify({'error': 'Failed to stop execution'}), 500

@app.route('/logs')
def list_logs():
    """List available log files."""
    log_dir = os.path.join(os.getcwd(), 'logs')
    logs = []
    
    if os.path.exists(log_dir):
        for filename in os.listdir(log_dir):
            if filename.endswith(('.log', '.txt', '.csv')):
                filepath = os.path.join(log_dir, filename)
                stat = os.stat(filepath)
                logs.append({
                    'name': filename,
                    'size': stat.st_size,
                    'modified': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                })
    
    # Sort by modification time (newest first)
    logs.sort(key=lambda x: x['modified'], reverse=True)
    
    return jsonify({'logs': logs})

@app.route('/logs/<filename>')
def download_log(filename):
    """Download a log file."""
    log_dir = os.path.join(os.getcwd(), 'logs')
    return send_from_directory(log_dir, filename, as_attachment=True)

@app.route('/logs/<filename>/view')
def view_log(filename):
    """View a log file content."""
    log_dir = os.path.join(os.getcwd(), 'logs')
    filepath = os.path.join(log_dir, filename)
    
    if not os.path.exists(filepath):
        return jsonify({'error': 'Log file not found'}), 404
    
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        return jsonify({
            'content': content,
            'size': len(content),
            'last_modified': os.path.getmtime(filepath)
        })
    except Exception as e:
        return jsonify({'error': f'Failed to read log file: {str(e)}'}), 500

@app.route('/logs/<filename>/tail')
def tail_log(filename):
    """Get the last N lines of a log file with optional offset."""
    log_dir = os.path.join(os.getcwd(), 'logs')
    filepath = os.path.join(log_dir, filename)
    
    if not os.path.exists(filepath):
        return jsonify({'error': 'Log file not found'}), 404
    
    try:
        lines = int(request.args.get('lines', 100))  # Default to last 100 lines
        since_size = request.args.get('since_size', type=int)  # Get content since this file size
        
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            if since_size is not None:
                # Seek to the position we want to start from
                f.seek(since_size)
                new_content = f.read()
                current_size = since_size + len(new_content)
            else:
                # Get last N lines
                f.seek(0, 2)  # Go to end of file
                file_size = f.tell()
                
                # Read the file backwards to get last N lines
                lines_found = 0
                block_size = 1024
                blocks = []
                
                while lines_found < lines and file_size > 0:
                    read_size = min(block_size, file_size)
                    f.seek(file_size - read_size)
                    block = f.read(read_size)
                    blocks.append(block)
                    lines_found += block.count('\n')
                    file_size -= read_size
                
                # Join blocks and split into lines
                content = ''.join(reversed(blocks))
                all_lines = content.split('\n')
                new_content = '\n'.join(all_lines[-lines:]) if len(all_lines) >= lines else content
                
                f.seek(0, 2)  # Go back to end for size
                current_size = f.tell()
        
        return jsonify({
            'content': new_content,
            'size': current_size,
            'last_modified': os.path.getmtime(filepath),
            'is_new': since_size is not None
        })
    except Exception as e:
        return jsonify({'error': f'Failed to read log file: {str(e)}'}), 500

@app.route('/logs/delete-all', methods=['POST'])
def delete_all_logs():
    """Delete all log files."""
    log_dir = os.path.join(os.getcwd(), 'logs')
    
    if not os.path.exists(log_dir):
        return jsonify({'message': 'No logs directory found'}), 200
    
    deleted_count = 0
    errors = []
    
    for filename in os.listdir(log_dir):
        if filename.endswith(('.log', '.txt', '.csv')):
            try:
                filepath = os.path.join(log_dir, filename)
                os.remove(filepath)
                deleted_count += 1
                logger.info(f"Deleted log file: {filename}")
            except Exception as e:
                errors.append(f"Failed to delete {filename}: {str(e)}")
                logger.error(f"Failed to delete log file {filename}: {str(e)}")
    
    if errors:
        return jsonify({
            'message': f'Deleted {deleted_count} log files with {len(errors)} errors',
            'errors': errors
        }), 207  # Multi-Status
    else:
        return jsonify({
            'message': f'Successfully deleted {deleted_count} log files'
        }), 200

@app.route('/health')
def health():
    """Health check endpoint."""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'running_scripts': list(running_scripts.keys())
    })

@app.route('/api/services/status')
def get_services_status():
    """Get the status of all connected services."""
    with service_status_lock:
        return jsonify({
            'services': service_status_cache.copy(),
            'last_updated': datetime.now().isoformat()
        })

@app.route('/api/services/refresh', methods=['POST'])
def refresh_services_status():
    """Refresh the status of all services."""
    try:
        update_service_status()
        with service_status_lock:
            return jsonify({
                'success': True,
                'services': service_status_cache.copy(),
                'last_updated': datetime.now().isoformat()
            })
    except Exception as e:
        logger.error(f"Error refreshing service status: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# Settings and Configuration Routes
@app.route('/api/settings/connections', methods=['GET'])
def get_connection_settings():
    """Get current connection settings (without sensitive data)."""
    try:
        settings = {}
        
        # Get settings from database
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM app_settings WHERE key LIKE '%_connection_%'")
            for row in cursor.fetchall():
                key_parts = row['key'].split('_')
                if len(key_parts) >= 3:
                    service = key_parts[0]
                    setting = '_'.join(key_parts[2:])
                    
                    if service not in settings:
                        settings[service] = {}
                    
                    # Mask sensitive data but indicate it exists
                    if setting in ['password', 'api_key', 'token']:
                        settings[service][setting] = '********' if row['value'] else ''
                    else:
                        settings[service][setting] = row['value']
        
        return jsonify(settings)
    except Exception as e:
        logger.error(f"Error getting connection settings: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/settings/connections/<service>', methods=['POST'])
def save_connection_settings(service):
    """Save connection settings for a service."""
    try:
        data = request.get_json()
        
        if service not in ['navidrome', 'lidarr', 'slskd']:
            return jsonify({'error': 'Invalid service'}), 400
        
        # Save settings to database
        with db.get_connection() as conn:
            cursor = conn.cursor()
            
            for key, value in data.items():
                # Skip saving if value is the mask (unchanged sensitive field)
                if value == '********':
                    continue
                    
                if value:  # Only save non-empty values
                    setting_key = f"{service}_connection_{key}"
                    cursor.execute("""
                        INSERT OR REPLACE INTO app_settings (key, value, description, updated_at)
                        VALUES (?, ?, ?, ?)
                    """, (setting_key, value, f"{service.title()} {key}", datetime.now()))
            
            conn.commit()
        
        logger.info(f"Saved {service} connection settings")
        return jsonify({'success': True, 'message': f'{service.title()} settings saved'})
        
    except Exception as e:
        logger.error(f"Error saving {service} connection settings: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings/test-connection/<service>', methods=['POST'])
def test_connection_settings(service):
    """Test connection to a service with provided settings."""
    try:
        data = request.get_json()
        
        # Helper function to get actual value if masked
        def get_actual_value(key, provided_value):
            if provided_value == '********':
                # Get the actual stored value from database
                with db.get_connection() as conn:
                    cursor = conn.cursor()
                    setting_key = f"{service}_connection_{key}"
                    cursor.execute("SELECT value FROM app_settings WHERE key = ?", (setting_key,))
                    row = cursor.fetchone()
                    return row['value'] if row else ''
            return provided_value
        
        if service == 'navidrome':
            url = get_actual_value('url', data.get('url', '').rstrip('/'))
            username = get_actual_value('username', data.get('username', ''))
            password = get_actual_value('password', data.get('password', ''))
            
            if not all([url, username, password]):
                return jsonify({'success': False, 'error': 'URL, username, and password are required'})
            
            # Test Navidrome connection
            auth_url = f"{url}/auth/login"
            auth_data = {'username': username, 'password': password}
            
            response = requests.post(auth_url, json=auth_data, timeout=10)
            if response.status_code == 200:
                return jsonify({'success': True, 'message': 'Connection successful'})
            else:
                return jsonify({'success': False, 'error': f'Authentication failed (HTTP {response.status_code})'})
                
        elif service == 'lidarr':
            url = get_actual_value('url', data.get('url', '').rstrip('/'))
            api_key = get_actual_value('api_key', data.get('api_key', ''))
            
            if not all([url, api_key]):
                return jsonify({'success': False, 'error': 'URL and API key are required'})
            
            # Test Lidarr API
            status_url = f"{url}/api/v1/system/status"
            headers = {'X-Api-Key': api_key}
            
            response = requests.get(status_url, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                version = data.get('version', 'Unknown')
                return jsonify({'success': True, 'message': f'Connected to Lidarr v{version}'})
            else:
                return jsonify({'success': False, 'error': f'API error (HTTP {response.status_code})'})
                
        elif service == 'slskd':
            url = get_actual_value('url', data.get('url', '').rstrip('/'))
            api_key = get_actual_value('api_key', data.get('api_key', ''))
            
            if not all([url, api_key]):
                return jsonify({'success': False, 'error': 'URL and API key are required'})
            
            # Set up headers for API requests
            headers = {'X-API-Key': api_key}
            
            # Test slskd API - try multiple endpoints for better compatibility
            test_endpoints = [
                f"{url}/api/v0/session",
                f"{url}/api/v0/application",
                f"{url}/api/v0/system/info"
            ]
            
            last_error = None
            for endpoint in test_endpoints:
                try:
                    response = requests.get(endpoint, headers=headers, timeout=10)
                    if response.status_code == 200:
                        try:
                            # Try to parse JSON response
                            data = response.json()
                            if endpoint.endswith('/session'):
                                state = data.get('state', 'Connected')
                                return jsonify({'success': True, 'message': f'Connected to slskd ({state})'})
                            else:
                                return jsonify({'success': True, 'message': 'Connected to slskd (API responded)'})
                        except ValueError:
                            # If response is not JSON, but status is 200, consider it a success
                            return jsonify({'success': True, 'message': 'Connected to slskd (API available)'})
                    elif response.status_code == 401:
                        return jsonify({'success': False, 'error': 'Authentication failed - check API key'})
                    else:
                        last_error = f'HTTP {response.status_code}'
                        continue
                except requests.exceptions.RequestException as e:
                    last_error = str(e)
                    continue
            
            # If all endpoints failed
            return jsonify({'success': False, 'error': f'All API endpoints failed. Last error: {last_error}'})
        
        else:
            return jsonify({'success': False, 'error': 'Invalid service'}), 400
            
    except requests.exceptions.ConnectionError:
        return jsonify({'success': False, 'error': 'Connection refused - check URL and network'})
    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'error': 'Connection timeout'})
    except ValueError as e:
        return jsonify({'success': False, 'error': f'Invalid response format: {str(e)}'})
    except Exception as e:
        logger.error(f"Error testing {service} connection: {e}")
        return jsonify({'success': False, 'error': str(e)})

# OAuth Routes (placeholder - will need OAuth libraries)
@app.route('/api/oauth/spotify/status')
def get_spotify_oauth_status():
    """Get Spotify OAuth connection status."""
    try:
        # Check if we have stored Spotify credentials
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM app_settings WHERE key = 'spotify_oauth_token'")
            row = cursor.fetchone()
            
            if row and row['value']:
                # TODO: Validate token with Spotify API
                return jsonify({
                    'connected': True,
                    'user': {'name': 'Spotify User'}  # TODO: Get actual user info
                })
            else:
                return jsonify({'connected': False})
    except Exception as e:
        logger.error(f"Error checking Spotify OAuth status: {e}")
        return jsonify({'connected': False, 'error': str(e)})

@app.route('/api/oauth/spotify/authorize')
def spotify_oauth_authorize():
    """Initialize Spotify OAuth flow."""
    # TODO: Implement Spotify OAuth using spotipy or similar library
    return jsonify({
        'error': 'Spotify OAuth not yet implemented',
        'message': 'OAuth integration will be added in a future update'
    }), 501

@app.route('/api/oauth/spotify/disconnect', methods=['POST'])
def spotify_oauth_disconnect():
    """Disconnect Spotify OAuth."""
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM app_settings WHERE key LIKE 'spotify_oauth_%'")
            conn.commit()
        
        return jsonify({'success': True, 'message': 'Disconnected from Spotify'})
    except Exception as e:
        logger.error(f"Error disconnecting Spotify: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/oauth/tidal/status')
def get_tidal_oauth_status():
    """Get Tidal OAuth connection status."""
    try:
        # Check if we have stored Tidal credentials
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM app_settings WHERE key = 'tidal_oauth_token'")
            row = cursor.fetchone()
            
            if row and row['value']:
                # TODO: Validate token with Tidal API
                return jsonify({
                    'connected': True,
                    'user': {'name': 'Tidal User'}  # TODO: Get actual user info
                })
            else:
                return jsonify({'connected': False})
    except Exception as e:
        logger.error(f"Error checking Tidal OAuth status: {e}")
        return jsonify({'connected': False, 'error': str(e)})

@app.route('/api/oauth/tidal/authorize')
def tidal_oauth_authorize():
    """Initialize Tidal OAuth flow."""
    # TODO: Implement Tidal OAuth using tidalapi or similar library
    return jsonify({
        'error': 'Tidal OAuth not yet implemented',
        'message': 'OAuth integration will be added in a future update'
    }), 501

@app.route('/api/oauth/tidal/disconnect', methods=['POST'])
def tidal_oauth_disconnect():
    """Disconnect Tidal OAuth."""
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM app_settings WHERE key LIKE 'tidal_oauth_%'")
            conn.commit()
        
        return jsonify({'success': True, 'message': 'Disconnected from Tidal'})
    except Exception as e:
        logger.error(f"Error disconnecting Tidal: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/listenbrainz/save', methods=['POST'])
def save_listenbrainz_connection():
    """Save ListenBrainz credentials."""
    try:
        data = request.get_json()
        token = data.get('token', '').strip()
        username = data.get('username', '').strip()
        
        if not token or not username:
            return jsonify({'success': False, 'error': 'User token and username are required'}), 400
        
        # Use settings module to save (updates cache automatically)
        settings.set_setting('listenbrainz_connection_token', token)
        settings.set_setting('listenbrainz_connection_username', username)
        
        logger.info(f"Saved ListenBrainz credentials for user: {username}")
        return jsonify({'success': True, 'message': 'ListenBrainz credentials saved successfully'})
    except Exception as e:
        logger.error(f"Error saving ListenBrainz credentials: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/listenbrainz/test', methods=['GET'])
def test_listenbrainz_connection():
    """Test ListenBrainz connection."""
    try:
        config = settings.get_listenbrainz_config()
        token = config.get('token')
        username = config.get('username')
        
        logger.info(f"Testing ListenBrainz connection - Token present: {bool(token)}, Username: {username}")
        
        if not token or not username:
            return jsonify({'success': False, 'error': 'ListenBrainz not configured. Please save your credentials first.'}), 400
        
        # Test by validating token
        url = f'https://api.listenbrainz.org/1/validate-token'
        headers = {
            'Authorization': f'Token {token}'
        }
        
        logger.info(f"Making request to ListenBrainz API to validate token")
        response = requests.get(url, headers=headers, timeout=10)
        logger.info(f"ListenBrainz API response status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            logger.info(f"ListenBrainz API response: {data}")
            
            if data.get('valid'):
                token_username = data.get('user_name', username)
                return jsonify({
                    'success': True, 
                    'message': f"Connected to ListenBrainz as {token_username}"
                })
            else:
                return jsonify({'success': False, 'error': 'Invalid ListenBrainz token'}), 400
        else:
            error_text = response.text[:200] if response.text else 'No response body'
            logger.error(f"ListenBrainz API returned {response.status_code}: {error_text}")
            return jsonify({'success': False, 'error': f'ListenBrainz API returned {response.status_code}'}), 400
            
    except Exception as e:
        logger.error(f"Error testing ListenBrainz connection: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/listenbrainz/status')
def listenbrainz_status():
    """Get ListenBrainz connection status."""
    try:
        config = settings.get_listenbrainz_config()
        token = config.get('token')
        username = config.get('username')
        
        if token and username:
            return jsonify({
                'connected': True,
                'username': username
            })
        else:
            return jsonify({'connected': False})
    except Exception as e:
        logger.error(f"Error checking ListenBrainz status: {e}")
        return jsonify({'connected': False, 'error': str(e)}), 500

@app.route('/api/slskd/downloads')
def get_slskd_downloads():
    """Get current downloads from slskd."""
    try:
        slskd_url = os.environ.get('SLSKD_URL')
        slskd_api_key = os.environ.get('SLSKD_API_KEY')
        
        if not slskd_url or not slskd_api_key:
            return jsonify({'error': 'slskd not configured'}), 404
        
        headers = {'X-API-Key': slskd_api_key}
        
        # Get current downloads
        downloads_url = f"{slskd_url}/api/v0/transfers/downloads"
        response = requests.get(downloads_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            downloads_data = response.json()
            
            # Format downloads for display
            downloads = []
            
            # slskd API returns a list of user objects, not a dict
            if isinstance(downloads_data, list):
                for user_data in downloads_data:
                    username = user_data.get('username', 'Unknown')
                    directories = user_data.get('directories', [])
                    
                    for directory in directories:
                        files = directory.get('files', [])
                        for download in files:
                            # Extract artist and album from filename
                            filename = download.get('filename', '')
                            artist, album = extract_artist_album_from_filename(filename)
                            
                            downloads.append({
                                'artist': artist,
                                'album': album,
                                'filename': filename,
                                'status': download.get('state', 'unknown').title(),
                                'progress': download.get('percentComplete', 0),
                                'speed': format_speed(download.get('averageSpeed', 0)),
                                'eta': calculate_eta(download.get('bytesRemaining', 0), download.get('averageSpeed', 0)),
                                'source': username,
                                'size': format_bytes(download.get('size', 0))
                            })
            else:
                # Fallback for unexpected data structure
                logger.warning(f"Unexpected downloads data structure: {type(downloads_data)}")
            
            return jsonify({'downloads': downloads})
        else:
            return jsonify({'error': f'Failed to fetch downloads: HTTP {response.status_code}'}), 500
            
    except Exception as e:
        logger.error(f"Error getting slskd downloads: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/slskd/searches')
def get_slskd_searches():
    """Get recent searches from slskd."""
    try:
        slskd_url = os.environ.get('SLSKD_URL')
        slskd_api_key = os.environ.get('SLSKD_API_KEY')
        
        if not slskd_url or not slskd_api_key:
            return jsonify({'error': 'slskd not configured'}), 404
        
        headers = {'X-API-Key': slskd_api_key}
        
        # Get recent searches
        searches_url = f"{slskd_url}/api/v0/searches"
        response = requests.get(searches_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            searches_data = response.json()
            
            # Format searches for display (limit to last 20)
            searches = []
            for search in searches_data[-20:]:
                search_id = search.get('id')
                query = search.get('searchText', 'Unknown')
                file_count = search.get('fileCount', 0)
                is_complete = search.get('isComplete', False)
                
                # Get best match if search is complete and has results
                best_match = None
                if is_complete and file_count > 0:
                    best_match = get_search_best_match(slskd_url, headers, search_id)
                
                searches.append({
                    'query': query,
                    'results_found': file_count,
                    'best_match': best_match.get('filename', 'N/A') if best_match else 'N/A',
                    'quality': best_match.get('quality', 'N/A') if best_match else 'N/A',
                    'size': format_bytes(best_match.get('size', 0)) if best_match else 'N/A',
                    'status': 'Complete' if is_complete else 'In Progress',
                    'search_id': search_id
                })
            
            return jsonify({'searches': searches})
        else:
            return jsonify({'error': f'Failed to fetch searches: HTTP {response.status_code}'}), 500
            
    except Exception as e:
        logger.error(f"Error getting slskd searches: {e}")
        return jsonify({'error': str(e)}), 500

def extract_artist_album_from_filename(filename):
    """Extract artist and album from filename."""
    # Remove file extension
    name = os.path.splitext(filename)[0]
    
    # Common patterns for artist - album
    patterns = [
        r'^(.+?)\s*-\s*(.+)$',  # Artist - Album
        r'^(.+?)\s*–\s*(.+)$',  # Artist – Album (em dash)
        r'^(.+?)\s*/\s*(.+)$',  # Artist / Album
    ]
    
    for pattern in patterns:
        match = re.match(pattern, name)
        if match:
            return match.group(1).strip(), match.group(2).strip()
    
    # If no pattern matches, try to split on common separators
    for sep in [' - ', ' – ', ' / ']:
        if sep in name:
            parts = name.split(sep, 1)
            return parts[0].strip(), parts[1].strip()
    
    # Fallback: return filename as album with unknown artist
    return 'Unknown Artist', name

def get_search_best_match(slskd_url, headers, search_id):
    """Get the best match from a search."""
    try:
        responses_url = f"{slskd_url}/api/v0/searches/{search_id}/responses"
        response = requests.get(responses_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            results = response.json()
            
            if isinstance(results, list) and results:
                # Find best match based on quality and size
                best_file = None
                best_score = 0
                
                for user_result in results:
                    files = user_result.get('files', [])
                    for file in files:
                        filename = file.get('filename', '')
                        size = file.get('size', 0)
                        
                        # Calculate quality score
                        quality, score = calculate_file_quality_score(filename, size)
                        
                        if score > best_score:
                            best_score = score
                            best_file = {
                                'filename': filename,
                                'size': size,
                                'quality': quality
                            }
                
                return best_file
        
        return None
        
    except Exception as e:
        logger.error(f"Error getting best match for search {search_id}: {e}")
        return None

def calculate_file_quality_score(filename, size):
    """Calculate a quality score for a file based on filename and size."""
    filename_lower = filename.lower()
    score = 0
    quality = 'Unknown'
    
    # Quality indicators
    if 'flac' in filename_lower:
        quality = 'FLAC'
        score += 100
    elif '320' in filename_lower or 'v0' in filename_lower:
        quality = 'MP3 320kbps'
        score += 80
    elif 'mp3' in filename_lower:
        quality = 'MP3'
        score += 60
    elif 'm4a' in filename_lower or 'aac' in filename_lower:
        quality = 'AAC'
        score += 50
    
    # Size bonus (larger files are generally better quality)
    if size > 100 * 1024 * 1024:  # > 100MB
        score += 20
    elif size > 50 * 1024 * 1024:  # > 50MB
        score += 10
    
    # Penalty for low quality indicators
    if '128' in filename_lower:
        score -= 30
        quality = 'MP3 128kbps'
    
    return quality, score

def format_bytes(bytes_val):
    """Format bytes to human readable format."""
    if bytes_val == 0:
        return "0 B"
    
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_val < 1024.0:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.1f} TB"

def format_speed(bytes_per_second):
    """Format speed to human readable format."""
    if bytes_per_second == 0:
        return "0 B/s"
    
    return f"{format_bytes(bytes_per_second)}/s"

def calculate_eta(bytes_remaining, average_speed):
    """Calculate estimated time of arrival."""
    if bytes_remaining == 0 or average_speed == 0:
        return "N/A"
    
    seconds = bytes_remaining / average_speed
    
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds / 60)}m {int(seconds % 60)}s"
    else:
        hours = int(seconds / 3600)
        minutes = int((seconds % 3600) / 60)
        return f"{hours}h {minutes}m"

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    # Create logs directory if it doesn't exist
    logs_dir = os.path.join(os.getcwd(), 'logs')
    os.makedirs(logs_dir, exist_ok=True)
    
    # Initialize database and clean up old data (keep last 30 days)
    logger.info("Initializing database...")
    try:
        db.cleanup_old_data(days=30)
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Database initialization error: {e}")
    
    # Initialize playlist script configurations on startup
    ensure_playlist_active_flags()
    
    # Test service connectivity on startup
    logger.info("Testing service connectivity on startup...")
    update_service_status()
    with service_status_lock:
        for service_name, status in service_status_cache.items():
            if status['status'] == 'online':
                logger.info(f"✅ {service_name.capitalize()}: {status.get('error', 'Connected')}")
            else:
                logger.warning(f"❌ {service_name.capitalize()}: {status.get('error', 'Connection failed')}")
    
    # Start Flask development server
    port = int(os.environ.get('PORT', 5000))
    host = os.environ.get('HOST', '0.0.0.0')
    
    logger.info(f"Starting SoulSeekarr on {host}:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)