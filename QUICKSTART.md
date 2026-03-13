# Photo Browser - Quick Start Guide

## Starting the App

```bash
pdm run python main.py
```

The app will launch with an empty browser window.

## First-Time Setup

### Step 1: Select Your Photo Library Folder
1. Go to **File → Select Library Folder...**
2. Choose a folder containing your photos (JPG, PNG, HEIF, etc.)
   - **Subfolders are automatically scanned** (no need to select a specific subfolder)
   - Recommended: Choose a top-level folder like `~/Pictures` or `~/Photos`
3. Click **Yes** when asked "Index Now?" to automatically scan the folder

### Step 2: Wait for Indexing
- The status bar at the bottom shows progress
- You'll see: `Index complete: X new images (Y scanned, Z skipped)`
- Skipped files are ones that can't be read (corrupted, permission denied, etc.)
- This imports all photos into the database
- **Indexing is optimized for speed:**
  - File hashing uses fast sampling (not full file read)
  - Thumbnails are generated on-demand while browsing (not during indexing)
  - Database operations are batched
  - Progress is logged every 10 files if indexing takes a while

### Step 3: View Your Photos
- A **"Library"** album is created automatically (bottom left)
- **Center panel** shows thumbnail grid
- Click any thumbnail to see preview on the **right panel**

### Step 4 (Optional): Index Faces
If you want face recognition:
1. Go to **File → Index Faces**
2. Wait for face detection to complete
3. Faces are grouped into identity clusters (not shown in basic UI yet)

## Basic Workflow

### Rating Photos
- Select a photo in the center grid
- Use **keyboard shortcuts** (numbers 0-5) or star widget on right
- Ratings are saved to database

### Tagging Photos
- Click the **Tags** field on the right panel
- Type tag name and press Enter
- Tags are saved automatically

### Creating Albums
1. Right-click in the left panel → **New Album**
2. Set filters (by date, people, tags)
3. Photos matching the filters appear in center

### Exporting Gallery
1. Select an album in the left panel
2. **File → Export Gallery as HTML...**
3. Choose output folder
4. Opens in browser with lightbox viewer

## Keyboard Shortcuts

| Action | Shortcut |
|--------|----------|
| Rate 1-5 stars | 1-5 keys |
| Clear rating | 0 |
| New album | Ctrl+N |
| New folder | Ctrl+Shift+N |
| Index images | Ctrl+I |
| Next photo | Right arrow |
| Previous photo | Left arrow |