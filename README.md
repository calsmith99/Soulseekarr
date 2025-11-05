# Soulseekarr
A bridge between Lidarr, Slskd and Subsonic server

This project contains a variety of scripts to help with organising your music library.

# --- WARNING ---
This project currently makes a lot of assumptions about how you manage your music library. Please read this carefully to ensure you don't lose any media
- The starred or favourited feature in subsonic/navidrome is used to determine which songs or albums are wanted and should be kept.
- Starred albums will automatically get their missing songs requested and downloaded
- Lidarr should be set to NOT automatically move any files, as this interferes with sorting between Incomplete and Not Owned

# Features
### Slskd Download Monitoring
Displays the progress and status of slskd downloads
### Slskd search results
Displays the search results from slskd and which files were selected for download
### Activity monitor
Displays a list of specific actions run by various scripts
### Library Expiry
Displays how long each album in your library that is not starred has until it is removed

# Scripts
### Owned / Not Owned / Incomplete Library manager
Automatically organises media files based on music you own, and music you haven't bought yet but still want available.
An additional organisation layer can be added to keep albums that have every song separate from albums containing just a few songs from playlists

I use this to keep 3 different libraries available in my subsonic player. So I can easily see full albums in my library when deciding what to listen to

### Spotify playlist sync
Gets tracks from a spotify playlist and requests them for download from Slskd, and ensures the playlist is synced to your subsonic library

### File cleanup
Remove tracks/albums after x days if they are not starred in subsonic. 
Expiry time can be managed with the env variable CLEANUP_DAYS

This is useful for downloading new releases to check out, or recommended albums without hoarding a bunch of un-needed music. Think of it like renting a new album

---
More info to add later
