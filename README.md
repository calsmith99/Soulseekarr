# SoulSeekarr

A modern Lidarr-style web interface for managing your music automation scripts with real-time monitoring, persistent execution history, and professional arr-style design.

## ✨ Features

- **🎨 Professional arr-style UI** - Clean, dark interface inspired by Lidarr/Sonarr/Radarr
- **📊 Persistent execution queue** - Survives container restarts with SQLite database
- **📝 Live log viewing** - Real-time script output with modal viewer
- **🔄 Dynamic script discovery** - Automatically detects scripts in `/scripts` folder
- **⚡ Real-time status updates** - Live progress tracking and status badges
- **� Download logs** - Export execution logs as timestamped files
- **🏷️ Script metadata system** - Rich descriptions, tags, and version info
- **🗂️ Organized sections** - Commands and Tests automatically categorized

## � Quick Start

1. **Start SoulSeekarr:**
   ```bash
   docker-compose up -d
   ```

2. **Access the web interface:**
   Open your browser and go to: `http://localhost:5000`

3. **Run scripts:**
   Click "Run" or "Dry Run" for any script. Click any queue item to view detailed logs.

## � Script Organization

Scripts are automatically discovered from the `/scripts` folder with support for metadata:

```python
#!/usr/bin/env python3
"""
Your Script Description

Name: Display Name
Author: SoulSeekarr
Version: 1.0
Section: commands  # or 'tests'
Tags: lidarr, automation, music
Supports dry run: true
"""
```

### 🏷️ Tag Complete Albums 🧪 **Testing**
Checks all albums in Lidarr for completeness and tags complete albums as "Complete". Also removes the tag from albums that become incomplete. *Recently fixed - currently being tested.*

### 🎯 Track Starred Albums in Lidarr ❓ **Untested**
Automatically tracks all favorited albums from Navidrome in Lidarr with Standard quality profile. Adds missing artists and albums. **Defaults to DRY-RUN mode for safety.** *New feature - needs testing.*

### 🎵 Monitor Queue for Multipart Songs 🚧 **Planned**
Monitors playback queue to automatically queue multipart songs together (Pt. 1/2, Part I/II, etc.). Ensures song suites play continuously without interruption. *Coming soon - placeholder only.*

### 🔧 Test API ❓ **Untested**
Tests Navidrome API connectivity and basic functions. *Basic API test - needs validation.*

### 📥 Process Downloads 🆕 **Enhanced**
Automatically processes downloaded music files with **audio fingerprinting** and MusicBrainz metadata fixing before matching with Lidarr. Uses acoustic analysis to identify tracks even with missing metadata, then groups files by album, checks completeness, and organizes into appropriate directories. Features dual-strategy identification (audio + metadata) for maximum accuracy. *Recently enhanced with AcoustID integration.*

## 🏷️ **Development Status Legend**

- ✅ **Tested** - Thoroughly tested and production ready
- 🧪 **Testing** - Functional but needs user testing 
- 🔧 **Refinement** - Works but could use improvements
- 🆕 **Enhanced** - Recently improved with new features
- ❓ **Untested** - Needs initial user testing and validation
- 🚧 **Planned** - Feature planned but not yet implemented
- ⚠️ **Experimental** - Use with caution, may have issues

## ⚙️ Configuration

All configuration is done through environment variables in the `docker-compose.yml` file:

```yaml
environment:
  - USER=your_navidrome_username
  - PASS=your_navidrome_password  
  - BASE_URL=http://your-navidrome-ip:4533/rest
  - MUSIC_DIR=/music
  - CLEANUP_DAYS=1
  - DRY_RUN=true
  # Lidarr Configuration
  - LIDARR_URL=http://your-lidarr-ip:8686
  - LIDARR_API_KEY=your_lidarr_api_key
```

### Key Settings:

- **USER/PASS**: Your Navidrome login credentials
- **BASE_URL**: Your Navidrome API endpoint
- **MUSIC_DIR**: Path to your music directory inside the container
- **CLEANUP_DAYS**: How many days old songs must be before cleanup
- **DRY_RUN**: Set to `false` to actually delete files (default: `true` for safety)
- **LIDARR_URL**: Your Lidarr base URL
- **LIDARR_API_KEY**: Your Lidarr API key (found in Settings → General)

## 🌐 Web Interface Features

- **🎮 One-Click Script Execution**: Run any script with a single button click
- **📊 Real-Time Status**: See which scripts are running, completed, or failed
- **📺 Live Output**: Watch script output in real-time with auto-scrolling
- **📁 Log Management**: Download and browse historical log files
- **📱 Responsive Design**: Works on desktop, tablet, and mobile devices
- **🔄 Auto-Refresh**: Status updates automatically every 2 seconds

## 🔧 Advanced Usage

### Running in Dry-Run Mode (Recommended)
Always test your configuration first:
```yaml
environment:
  - DRY_RUN=true
```

### Production Mode
Once you're confident in your setup:
```yaml
environment:
  - DRY_RUN=false
```

### Custom Port
To run on a different port:
```yaml
ports:
  - "8080:5000"  # Access via http://localhost:8080
```

## 📁 File Structure

```
navidrome-cleanup/
├── app.py                     # Flask web application
├── templates/
│   └── index.html            # Web interface template
├── requirements.txt          # Python dependencies
├── entrypoint.sh            # Container startup script
├── docker-compose.yml       # Container configuration
├── navidrome_cleanup_docker.sh        # Main cleanup script
├── import_playlists.sh                # Playlist import script
├── star_all_songs.sh                  # Star all songs script
├── monitor_artists.sh                 # Artist monitoring script
├── tag_complete_albums_mood.sh        # Complete album tagging script
├── navidrome_to_lidarr_track.sh       # Track starred albums in Lidarr
├── test_api.sh                        # API testing script
├── PLAYLIST_IMPORT.md                 # Playlist import documentation
├── TRACK_STARRED.md                   # Track starred albums documentation
└── logs/                              # Log files directory
```

## 🛠️ Troubleshooting

### Container Won't Start
1. Check Docker logs: `docker-compose logs music-management-tools`
2. Verify file permissions: `chmod +x *.sh`
3. Check Python dependencies in `requirements.txt`

### API Connection Issues
1. Verify your Navidrome is running and accessible
2. Check the `BASE_URL` setting
3. Confirm your `USER` and `PASS` credentials
4. Use the "Test API" script to diagnose connectivity

### Scripts Not Running
1. Check script permissions: `ls -la *.sh`
2. Verify paths in the scripts match your setup
3. Check logs in the web interface or container logs

### Web Interface Not Loading
1. Ensure port 5000 is available: `netstat -tulpn | grep :5000`
2. Check firewall settings
3. Try accessing via container IP directly

## 🔒 Security Notes

- The web interface runs on port 5000 by default
- No authentication is built-in - restrict network access as needed
- Scripts run with the same permissions as the container user
- Consider using Docker secrets for sensitive credentials in production

## 📝 Logs

All script outputs are logged to the `/logs` directory and can be:
- Viewed in real-time through the web interface
- Downloaded via the web interface
- Accessed directly from the host filesystem

Log files are automatically organized by script and timestamp for easy tracking.

---

**⚠️ Important**: Always run in dry-run mode first to verify your configuration before allowing actual file deletions!