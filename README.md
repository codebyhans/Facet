# Photo Manager

A local-first desktop photo management application with ML-powered face recognition and identity clustering. Built with Python, PySide6, and SQLite.

---

## Features

### Photo Library
- **Filesystem indexing** — scan a root directory recursively; new files are detected incrementally without re-processing existing images
- **Thumbnail tile system** — thumbnails are packed into large tile images for fast bulk loading; the grid never blocks the UI
- **Virtualized photo grid** — paginated, lazy-loading grid that handles libraries of any size
- **Image detail view** — full-resolution viewer with fit/zoom modes, keyboard navigation, and metadata panel
- **Star ratings** — 0–5 star ratings written to the database and optionally synced back to EXIF
- **Flagging** — mark images as Keep / Undecided / Discard for culling workflows
- **Tags** — free-form tagging with per-image tag editor
- **EXIF sync** — ratings, tags, and notes written to image file metadata via `piexif`
- **Quality scoring** — automated blur/quality scoring using OpenCV, stored per image
- **HTML export** — export any album as a self-contained HTML gallery

### Albums & Filtering
- **Album tree** — nested folder/album hierarchy with drag-and-drop reordering, persisted across sessions
- **Virtual albums** — filter-based albums defined by query (people, date range, rating, flag, tags)
- **Filter bar** — live horizontal filter pills for rating, people, date range, and flag; combining filters with AND/OR logic
- **Advanced filter editor** — full-featured dialog for building complex album queries
- **Library mode** — view the entire indexed library with filters applied

### People & Face Recognition
- **Face detection** — [InsightFace](https://github.com/deepinsight/insightface) ONNX model detects and embeds all faces on index
- **Identity clustering** — age-aware HDBSCAN clustering with temporal smoothing; clusters update automatically as new photos are indexed
- **Temporal profiles** — per-cluster embeddings for child/adult/senior age buckets, weighted by recency, to handle the same person across decades
- **Person stacks view** — card grid showing one card per person, with cover thumbnail, name, and image count
- **Inline renaming** — type a name directly on a stack card without entering the detail view
- **Cluster detail view** — browse all images containing a given person
- **Cluster merge** — manually merge two identity clusters when auto-clustering splits the same person
- **People filter** — filter the photo grid by one or more named persons (AND/OR logic)
- **Approximate nearest neighbour index** — custom random-projection ANN index for fast similarity search at query time

---

## Architecture

```
photo_app/
├── domain/          # Pure Python entities, value objects, repository protocols
├── services/        # Use-case layer — no Qt, no SQLAlchemy
├── infrastructure/  # SQLAlchemy repositories, file scanner, EXIF handler, thumbnail tiles
├── ml/              # Face detector, embedding model, quality scorer, clustering
├── indexing/        # Image and face indexing pipelines
├── app/
│   ├── view_models/ # Qt-facing state: GalleryViewModel, AlbumViewModel
│   ├── widgets/     # All PySide6 widgets
│   └── workers/     # QRunnable background workers
└── config/          # Settings, theme, keyboard shortcuts
```

The service layer has no Qt or SQLAlchemy imports — it operates on domain models and calls repository protocols. Qt widgets call view models; view models call services. SQLAlchemy sessions are scoped per-operation (no long-lived sessions shared across threads).

---

## Requirements

- Python 3.14
- [PDM](https://pdm-project.org/) for dependency management
- The InsightFace ONNX model file (see Setup below)

---

## Setup

**1. Clone and install dependencies**

```bash
git clone <repo>
cd photo-app
pdm install
```

**2. Download the face recognition model**

The app uses InsightFace's `buffalo_l` model. On first run the model will be downloaded automatically to `photo_app_data/models/` if an internet connection is available. To download manually:

```bash
python -c "import insightface; insightface.app.FaceAnalysis(name='buffalo_l', root='photo_app_data/models').prepare(ctx_id=-1)"
```

**3. Run database migrations**

```bash
pdm run alembic upgrade head
```

**4. Launch**

```bash
pdm run python main.py
```

---

## First Use

1. Open the app — the album tree starts empty
2. Go to **File → Index Images** (or `Ctrl+I`) and select your photo root directory
3. Wait for indexing to complete — progress is shown in the status bar
4. Go to **File → Index Faces** (`Ctrl+Shift+I`) to run face detection and clustering
5. Switch to the **People** tab to review identity clusters and assign names

Subsequent indexing runs are incremental — only new files are processed.

---

## Keyboard Shortcuts

| Action | Shortcut |
|---|---|
| Index images | `Ctrl+I` |
| Index faces | `Ctrl+Shift+I` |
| New album | `Ctrl+N` |
| New folder | `Ctrl+Shift+N` |
| Rename selected | `Ctrl+R` |
| Delete selected | `Delete` |
| Add tag | `Ctrl+T` |
| Find album | `Ctrl+F` |
| Rate 0–5 stars | `0` – `5` |
| Flag: Keep | `P` |
| Flag: Discard | `X` |
| Flag: Undecided | `U` |
| Clear flag | `Backspace` |
| Navigate photos | `← →` `Home` `End` |
| Fullscreen | `F` |
| Zoom in / out | `Ctrl++` / `Ctrl+-` |
| Fit to window | `Ctrl+0` |
| Settings | `Ctrl+,` |
| Quit | `Ctrl+Q` |

---

## Configuration

All runtime settings live in `AppSettings` (`photo_app/config/settings.py`). Key parameters:

| Setting | Default | Description |
|---|---|---|
| `detector_confidence_threshold` | `0.55` | Minimum InsightFace detection confidence |
| `identity_match_threshold` | `0.52` | Cosine similarity threshold for assigning a face to an existing cluster |
| `identity_merge_threshold` | `0.70` | Threshold above which two clusters are auto-merged |
| `identity_variance_review_threshold` | `0.35` | Cluster variance above which a cluster is flagged for manual review |
| `identity_recency_weight` | `0.15` | Weight applied to recent photos when computing cluster centroids |
| `clustering_min_cluster_size` | `2` | Minimum faces to form an identity cluster |
| `clustering_age_penalty_weight` | `0.15` | How much age distance penalises face similarity during clustering |
| `thumbnail_size` | `128×128` | Thumbnail dimensions |
| `tile_size` | `1024×1024` | Tile atlas dimensions (holds 64 thumbnails per tile by default) |
| `face_review_threshold` | `3` | Minimum image count to show a cluster in the People tab |

Data is stored in `photo_app_data/` relative to the working directory:

```
photo_app_data/
├── photo_manager.sqlite3   # Main database
├── cache/tiles/            # Thumbnail tile images
├── models/                 # ONNX model files
└── thumbnails/             # Legacy per-image thumbnails (if any)
```

---

## Development

```bash
# Run tests
pdm run pytest

# Lint
pdm run ruff check .

# Type check
pdm run mypy photo_app/

# Run pre-commit hooks
pdm run pre-commit run --all-files
```

---

## Tech Stack

| Layer | Library |
|---|---|
| UI | [PySide6](https://doc.qt.io/qtforpython/) (Qt 6) |
| Database | [SQLAlchemy](https://www.sqlalchemy.org/) 2.0 + [Alembic](https://alembic.sqlalchemy.org/) |
| Face detection | [InsightFace](https://github.com/deepinsight/insightface) + [ONNX Runtime](https://onnxruntime.ai/) |
| Clustering | [HDBSCAN](https://hdbscan.readthedocs.io/) |
| Image processing | [Pillow](https://python-pillow.org/) + [OpenCV](https://opencv.org/) |
| File hashing | [BLAKE3](https://github.com/oconnor663/blake3-py) |
| EXIF | [piexif](https://piexif.readthedocs.io/) |
| Numerics | [NumPy](https://numpy.org/) |