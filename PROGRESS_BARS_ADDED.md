# Progress Bars Added to Scripts

## Summary

Added `tqdm` progress bars to `process_downloads.py` and `organise_files.py` to provide visual feedback during long-running operations.

## Changes Made

### 1. Updated `requirements.txt`
- Added `tqdm>=4.65.0` dependency for progress bar support

### 2. Modified `scripts/process_downloads.py`

**Import Section:**
- Added optional import of `tqdm` with fallback if not available
- Set `TQDM_AVAILABLE` flag to control progress bar display

**Progress Bars Added:**
- **Metadata Fixing Loop** (line ~1957): Shows progress when fixing metadata using MusicBrainz
  - Display format: `Fixing metadata: 45%|████▌     | 123/273 [02:15<02:45, 1.10s/file]`
  
- **Album Processing Loop** (line ~1987): Shows progress when processing albums
  - Display format: `Processing albums: 78%|███████▊  | 12/15 [01:30<00:25, 1.67s/album]`

### 3. Modified `scripts/organise_files.py`

**Import Section:**
- Added optional import of `tqdm` with fallback if not available
- Set `TQDM_AVAILABLE` flag to control progress bar display

**Progress Bars Added:**
- **Metadata Structure Check** (line ~102): Shows progress when checking folder structure based on metadata
  - Display format: `Checking metadata: 62%|██████▏   | 1234/2000 [01:45<01:05, 11.73file/s]`

- **Owned Albums Check** (line ~753): Shows progress when checking owned directory for missing tracks
  - Display format: `Checking owned albums: 34%|███▍      | 45/132 [00:55<01:45, 1.21s/album]`

- **Not_Owned Albums Organization** (line ~1382): Shows progress when organizing Not_Owned directory
  - Display format: `Organizing albums: 56%|█████▌    | 78/139 [02:10<01:50, 1.81s/album]`

- **Incomplete Albums Check** (line ~1443): Shows progress when checking incomplete directory
  - Display format: `Checking incomplete: 89%|████████▉ | 23/26 [00:34<00:04, 1.50s/album]`

- **Duplicate Removal** (line ~1512): Shows progress when removing duplicates only
  - Display format: `Removing duplicates: 45%|████▌     | 67/150 [01:23<01:43, 1.25s/album]`

## Features

### Graceful Degradation
If `tqdm` is not installed, the scripts will:
- Continue to work normally without progress bars
- Fall back to the original iteration behavior
- Log a warning message about missing tqdm

### Progress Bar Information
Each progress bar shows:
- **Percentage complete**: Visual bar and numeric percentage
- **Current/Total items**: e.g., `123/273`
- **Time elapsed**: e.g., `[02:15<02:45]` (elapsed<remaining)
- **Processing rate**: e.g., `1.10s/file` or `11.73file/s`

### Clean Display
- Progress bars use `ncols=100` for consistent width
- Descriptive labels for each operation
- Appropriate units (file, album, etc.)
- Does not interfere with existing logging

## Usage

### With Progress Bars (Recommended)
```bash
# Install tqdm if not already installed
pip install tqdm

# Run scripts normally
python scripts/process_downloads.py
python scripts/organise_files.py
```

### Without Progress Bars (Fallback)
```bash
# Scripts work without tqdm, just no visual progress
python scripts/process_downloads.py
python scripts/organise_files.py
```

## Benefits

1. **Better User Experience**: Visual feedback during long operations
2. **Time Estimation**: See estimated time remaining for each task
3. **Performance Monitoring**: See processing rate (items/second)
4. **No Disruption**: Existing logging output still works
5. **Optional**: Scripts continue to work if tqdm isn't available

## Docker Integration

The progress bars will automatically work in Docker containers since `tqdm` is now included in `requirements.txt`. During Docker build:
```dockerfile
RUN pip install -r requirements.txt  # Includes tqdm now
```

## Examples

### Process Downloads Script
```
Scanning directory: /downloads/completed
Found 273 music files
Fixing metadata: 100%|██████████| 273/273 [05:12<00:00, 1.14s/file]
Metadata fixing complete: 268 files fixed, 5 files failed/skipped
Grouped into 15 albums
Processing albums: 100%|██████████| 15/15 [03:45<00:00, 15.00s/album]
```

### Organise Files Script
```
Found 132 albums in Owned directory
Checking owned albums: 100%|██████████| 132/132 [02:34<00:00, 1.17s/album]

Found 139 album folders in Not_Owned to check
Organizing albums: 100%|██████████| 139/139 [04:22<00:00, 1.89s/album]

Found 26 albums in incomplete directory to check
Checking incomplete: 100%|██████████| 26/26 [00:39<00:00, 1.50s/album]
```

## Technical Notes

- Progress bars are created only when `TQDM_AVAILABLE` is `True`
- No performance overhead when tqdm is not installed
- Progress bars work in both interactive terminals and log files
- Compatible with existing dry-run functionality
- Does not affect error handling or logging behavior
