# Local ML Photo Manager

Local-first photo manager for Windows 11 with SQLite persistence, PySide6 UI, and swappable ML adapters.

## What This App Does

- Indexes images from a local photo folder tree.
- Extracts metadata (EXIF date + fallback from folder names).
- Generates WebP thumbnails.
- Stores image, face, person, and album data in SQLite.
- Keeps everything local (no cloud API calls).
- Supports stack-based face naming (name one matched person cluster at once).

## Prerequisites

- Windows 11
- Python 3.14.2
- [PDM](https://pdm-project.org/) 2.26+
- Visual C++ Build Tools (for some native wheels if needed)

Optional for face embeddings:

- Local ONNX face embedding model file (`.onnx`)
- `insightface` model assets (downloaded/available locally)

## Default ML Behavior

By default, the app uses `insightface` directly for face detection and embeddings.

- No custom ONNX model path is required for normal use.
- On first initialization, `insightface` may download its own local model assets.
- Everything still runs locally after assets are present.

Custom ONNX is now an advanced override in Settings.

## Install

From the repository root:

```powershell
pdm install -G dev
```

## Run

```powershell
pdm run python main.py
```

On first run the app will:

- Create `photo_app_data/`
- Run Alembic migrations automatically
- Create SQLite DB at `photo_app_data/photo_manager.sqlite3`
- Create thumbnails in `photo_app_data/thumbnails/`

## Where Photos Should Be Placed

Default photo root folder:

- `photo_app_data/photos/`

Expected folder style (recommended):

- `yyyy/yyyy-mm-dd/...`
- Example: `photo_app_data/photos/2024/2024-08-17/img001.jpg`

The image indexer scans recursively under the configured photo root.

## Settings Panel

Open the app and click `Settings`.

These settings are configurable in UI and persisted in SQLite:

- `Photo Root Folder`
- `ONNX Model (Advanced)`
- `ONNX Input Name`
- `Face Batch Size`
- `Age Penalty Weight`
- `Penalty Year Scale`
- `Min Cluster Size`
- `Detector Threshold` (ML threshold)
- `Thumbnail Max Size`

### Persistence of Settings

Settings are saved in DB table:

- `app_settings(key, value, updated_at)`

This means settings survive app restarts and are machine-local.

## Notes About Applying Settings

- `Photo Root Folder` and `Thumbnail Max Size` are applied in-app after save.
- ML pipeline parameters (like detector threshold / clustering params / ONNX model) are persisted immediately, and fully applied on next app start when services are rebuilt.

## Face ML Setup

Face indexing is enabled by default through `insightface`.

If you set `ONNX Model (Advanced)`, the app will use that file as embedding override.
If the override path is invalid, the app falls back to default `insightface` embeddings.

## Stack-Based Face Naming

After running `Index Faces`, use the `Stack Naming` button in the top toolbar.

In the Stack Naming view:

- Each thumbnail tile represents one detected person stack.
- The cover image prefers a photo where only that person is detected.
- Rename the selected stack to assign one name to that whole matched cluster.
- You can also open sample images, pick a specific face, then either:
  - `Set Face Name` (manual re-assign for that face only), or
  - `Remove Face` (exclude false detections from UI and future clustering).
- Editing is restricted to the selected stack only:
  - Faces from other clusters in multi-face images are hidden.
  - Their name tags cannot be changed from this stack editor.
- `Reindex Sample Image` re-runs detection for just that image and replaces all prior
  face rows for it (including previously removed faces), then reclusters.

In the main viewer `Detected Faces` panel, you can also use:

- `Save Name` to manually re-assign the selected face to a person name.
- `Remove Face` to exclude that face.
- `Reindex Image Faces` to rebuild face detection for only the current image.

Manual face edits are persisted and protected from later automatic re-clustering.

## Development Commands

Lint:

```powershell
pdm run ruff check .
```

Types:

```powershell
pdm run mypy .
```

Tests:

```powershell
pdm run pytest
```

Performance harness:

```powershell
pdm run python -m photo_app.tests.perf_harness --images 20000 --persons 1000 --embeddings 10000 --embedding-dim 512 --page-limit 200
```

## Data and Paths Summary

- DB: `photo_app_data/photo_manager.sqlite3`
- Thumbnails: `photo_app_data/thumbnails/`
- Default photos root: `photo_app_data/photos/`
- Migrations: `photo_app/migrations/versions/`

## Troubleshooting

If `pdm install` fails for native packages:

- Confirm Python is `3.14.2`
- Update pip/setuptools/wheel in the environment
- Ensure MSVC build tools are installed

If UI starts but face indexing is unavailable:

- Check startup logs for InsightFace initialization errors
- Verify optional ONNX override path if you set one
- Restart app after changing ML settings
