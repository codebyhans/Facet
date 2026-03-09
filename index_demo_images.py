#!/usr/bin/env python3
"""Index demo images into the database."""
from pathlib import Path
from sqlalchemy.orm import Session

from photo_app.config.settings import load_settings
from photo_app.infrastructure.db import create_sqlite_engine
from photo_app.infrastructure.repositories import (
    SqlAlchemyImageRepository,
    SqlAlchemyAlbumRepository,
    SqlAlchemySettingsRepository,
)
from photo_app.infrastructure.file_scanner import FileScanner
from photo_app.infrastructure.thumbnail_store import ThumbnailStore
from photo_app.services.image_index_service import ImageIndexService
from photo_app.services.album_query_cache_service import AlbumQueryCacheService
from photo_app.services.settings_service import SettingsService

def main():
    settings = load_settings()
    engine = create_sqlite_engine(str(settings.db_path))
    
    with Session(engine) as session:
        # Initialize services
        image_repo = SqlAlchemyImageRepository(session)
        album_repo = SqlAlchemyAlbumRepository(session)
        settings_repo = SqlAlchemySettingsRepository(session)
        
        settings_service = SettingsService(settings_repo, settings)
        runtime_settings = settings_service.get_runtime_settings()
        
        thumbnail_store = ThumbnailStore(
            settings.thumbnail_dir,
            max_size=runtime_settings.thumbnail_max_size,
        )
        query_cache_service = AlbumQueryCacheService(session, album_repo, image_repo)
        file_scanner = FileScanner()
        
        image_index_service = ImageIndexService(
            image_repo,
            file_scanner,
            thumbnail_store,
            query_cache_service=query_cache_service,
        )
        
        # Index demo images
        demo_folder = Path(__file__).parent / "demo_images"
        print(f"Indexing images from: {demo_folder}")
        
        def progress_callback(current, total):
            print(f"  Progress: {current}/{total}")
        
        result = image_index_service.index_folder(demo_folder, on_progress=progress_callback)
        
        print(f"\nIndexing complete!")
        print(f"  Scanned: {result.scanned}")
        print(f"  Inserted: {result.inserted}")
        print(f"  Skipped: {result.skipped}")
        
        # Commit the transaction
        session.commit()
        print("\nDatabase committed successfully!")
        
        # Verify images were added
        all_images = image_repo.list_all()
        print(f"\nTotal images now in database: {len(all_images)}")

if __name__ == "__main__":
    main()
