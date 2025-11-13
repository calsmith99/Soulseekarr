# Bug Fix: Multiple Song Versions Download Issue

## Problem Description

The Lidarr download scripts (`queue_lidarr_monitored.py` and `smart_queue_downloads.py`) were downloading ALL versions of a song when multiple versions existed, rather than selecting the most appropriate version.

### Root Cause

In the `attempt_selective_download` function, when matching tracks from a user's folder to the missing tracks list, the code would:

1. Loop through all files in the user's directory
2. For each file, check if it matched ANY of the missing tracks
3. If it matched, immediately add it to the download queue
4. Continue checking for more matches

This meant if a track like "Song Name" had multiple versions:
- `01 - Song Name.flac`
- `01 - Song Name (Remix).mp3`
- `01 - Song Name (Live).mp3`

**ALL THREE files would be queued** instead of just the best one.

## Solution

The fix changes the matching logic to:

1. For each missing track, find ALL files that match it
2. Score each matching file based on quality factors:
   - **Penalties (-50)**: Remixes, live versions, acoustic, instrumental, karaoke, edits, demos, covers
   - **Bonuses (+30)**: FLAC format
   - **Bonuses (+20)**: 320kbps MP3
   - **Bonuses (+25)**: "original" or "album version" indicators
   - **Bonuses (+15)**: Track number in filename
   - **Bonuses (+10)**: Reasonable file size (3-50MB)
3. Sort by quality score (highest first)
4. Select ONLY the best matching file for each track

### Detailed Scoring System

The quality scoring system ensures we get the best version:

```python
# Unwanted version patterns (heavy penalty)
unwanted_patterns = [
    'remix', 'mix)', 'live', 'acoustic', 'instrumental', 
    'karaoke', 'edit)', 'demo', 'cover', 'tribute'
]
has_unwanted = any(pattern in filename) → quality_score -= 50

# Quality indicators (bonuses)
FLAC format → +30 points
320kbps MP3 → +20 points
192kbps MP3 → +10 points
Track number in filename → +15 points
"original" in filename → +25 points
"album version" in filename → +25 points
Reasonable size (3-50MB) → +10 points
```

## Changes Made

### Files Modified

1. **`/scripts/queue_lidarr_monitored.py`** - Lines ~760-850
2. **`/scripts/smart_queue_downloads.py`** - Lines ~680-770

### Key Code Changes

**Before:**
```python
for file_info in user_files:
    file_path = file_info['filename']
    file_basename = file_path.split('/')[-1].lower()
    
    for track_title in missing_track_titles:
        if matches(file_basename, track_title):
            needed_files.append(file_info)  # ❌ Adds ALL matches
            break
```

**After:**
```python
for track in missing_tracks:
    matching_files = []
    
    # Find all files that match this track
    for file_info in user_files:
        if matches(file_info, track):
            quality_score = calculate_quality_score(file_info)
            matching_files.append({'file_info': file_info, 'quality_score': quality_score})
    
    # Sort by quality and take the best
    matching_files.sort(key=lambda x: x['quality_score'], reverse=True)
    
    if matching_files:
        needed_files.append(matching_files[0]['file_info'])  # ✅ Only the best match
```

## Benefits

1. **Prevents duplicate downloads**: Only one version per track is queued
2. **Quality preference**: FLAC and high-bitrate files are preferred
3. **Avoids unwanted versions**: Remixes, live versions, etc. are automatically filtered out
4. **Better logging**: Shows which versions were found and which was selected
5. **Maintains fallback**: If no specific tracks match, still queues all files (safer approach)

## Example Output

When the script finds multiple versions, you'll see debug logs like:

```
🎯 Track 'California Love': Found 3 versions
   ✅ Selected: 01 - california love.flac (score: 45)
   ⏭️  Skipped: 01 - california love (remix).mp3 (score: -30)
   ⏭️  Skipped: 01 - california love (live).mp3 (score: -40)
```

## Testing

To test the fix:

1. Find an album with multiple versions of tracks in slskd search results
2. Run the queue script with debug logging enabled:
   ```bash
   python scripts/queue_lidarr_monitored.py --debug
   ```
3. Check the debug logs for track selection messages
4. Verify only one file per track is queued in slskd

## Backward Compatibility

- ✅ Maintains all existing functionality
- ✅ Fallback behavior unchanged (queues all files if matching fails)
- ✅ No API changes required
- ✅ No configuration changes needed

## Related Issues

This fix addresses the core issue of downloading multiple versions. The quality scoring system is similar to what's already used in the `find_best_candidates` function for search result filtering, providing consistency across the codebase.
