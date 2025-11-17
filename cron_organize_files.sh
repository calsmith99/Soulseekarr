#!/bin/bash
#
# Navidrome Cleanup - Organize Files Cron Script
#
# This script runs the organize files script every hour to:
# - Move completed downloads to appropriate directories
# - Clean up database entries for deleted files
# - Maintain proper file organization
#

# Set working directory to the project root
cd "$(dirname "$0")"

# Log file with timestamp
LOG_DIR="logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/organize_files_cron_$(date +%Y%m%d).log"

# Function to log with timestamp
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

log "🔄 Starting hourly organize files cron job"

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    log "❌ Python3 not found"
    exit 1
fi

# Run the organize files script
log "📁 Running organize files script..."

python3 scripts/organise_files.py --auto-mode 2>&1 | tee -a "$LOG_FILE"
exit_code=${PIPESTATUS[0]}

if [ $exit_code -eq 0 ]; then
    log "✅ Organize files completed successfully"
else
    log "❌ Organize files failed with exit code: $exit_code"
fi

# Clean up old log files (keep last 7 days)
log "🧹 Cleaning up old log files..."
find "$LOG_DIR" -name "organize_files_cron_*.log" -mtime +7 -delete 2>/dev/null || true

log "🏁 Hourly organize files cron job completed"
echo ""