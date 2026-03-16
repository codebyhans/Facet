from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QObject, Signal

from photo_app.app.models.album_tree_model import AlbumTreeNode


@dataclass(frozen=True)
class FilterPerson:
    """Person option exposed to filter editor."""

    person_id: int
    name: str


class AlbumViewModel(QObject):
    """UI bridge for album tree + album service operations."""

    tree_ready = Signal(list)
    error = Signal(str)

    def __init__(
        self,
        album_service: object,
        face_review_service: object,
        settings_path: Path,
    ) -> None:
        super().__init__()
        self._album_service = album_service
        self._face_review_service = face_review_service
        self._settings_path = settings_path
        self._state = self._load_state()

        # Store reference to image repository for flag updates
        if hasattr(album_service, "_image_repository"):
            self._image_repository = album_service._image_repository
        else:
            self._image_repository = None

    def load_album_tree(self) -> list[AlbumTreeNode]:
        """Load tree from local UI state and merge persisted virtual albums."""
        albums = self._safe_call(lambda: self._album_service.list_albums(), default=[])
        known_by_id = {album.id: album for album in albums if album.id is not None}
        nodes = self._deserialize_nodes(self._state.get("album_tree", []), known_by_id)

        seen_album_ids = {node.album_id for node in self._flatten(nodes) if node.album_id}
        for album_id, album in known_by_id.items():
            if album_id in seen_album_ids:
                continue
            nodes.append(
                AlbumTreeNode(
                    node_id=f"v-{album_id}",
                    name=album.name,
                    kind="virtual",
                    album_id=album_id,
                    query_definition=self._album_query_to_dict(album.query_definition),
                )
            )

        self.tree_ready.emit(nodes)
        self._persist_tree(nodes)
        return nodes

    def create_folder(self, name: str, parent_id: str | None) -> AlbumTreeNode:
        """Create folder node in UI state."""
        node = AlbumTreeNode(
            node_id=f"f-{uuid4().hex}",
            name=name.strip(),
            kind="folder",
            parent_id=parent_id,
            query_definition=None,
        )
        return node

    def create_virtual_album(
        self,
        name: str,
        parent_id: str | None,
        query_definition: dict[str, object],
    ) -> AlbumTreeNode:
        """Create virtual album through AlbumService and return tree node."""
        album = self._album_service.create_album(name.strip(), query_definition)
        album_id = album.id
        if album_id is None:
            raise RuntimeError("Album creation returned no id")
        return AlbumTreeNode(
            node_id=f"v-{album_id}",
            name=album.name,
            kind="virtual",
            album_id=album_id,
            parent_id=parent_id,
            query_definition=query_definition,
        )

    def rename_node(self, node: AlbumTreeNode, new_name: str) -> str:
        """Rename local node and backing album if needed."""
        cleaned = new_name.strip()
        if node.kind == "virtual" and node.album_id is not None:
            updated = self._album_service.rename_album(node.album_id, cleaned)
            if updated is None:
                raise RuntimeError("Could not rename album")
            return updated.name
        return cleaned

    def update_album_query(
        self,
        node: AlbumTreeNode,
        query_definition: dict[str, object],
    ) -> dict[str, object]:
        """Update query for one virtual album."""
        if node.kind != "virtual" or node.album_id is None:
            return query_definition
        updated = self._album_service.update_album_query(node.album_id, query_definition)
        if updated is None:
            raise RuntimeError("Could not update album query")
        return self._album_query_to_dict(updated.query_definition)

    def delete_node(self, node: AlbumTreeNode) -> None:
        """Delete one backing virtual album, folders are local state only."""
        if node.kind == "virtual" and node.album_id is not None:
            deleted = self._album_service.delete_album(node.album_id)
            if not deleted:
                raise RuntimeError("Could not delete album")

    def resolve_album_images(self, album_id: int, *, offset: int, limit: int, query_definition: dict[str, object] | None = None) -> object:
        """Fetch one page of images through AlbumService."""
        return self._album_service.list_album_images(album_id, offset=offset, limit=limit, query_definition=query_definition)

    def resolve_library_images(self, *, offset: int, limit: int, query_definition: dict[str, object] | None = None) -> object:
        """Fetch one page of all-library images."""
        return self._album_service.list_library_images(offset=offset, limit=limit, query_definition=query_definition)

    def list_filter_people(self) -> list[FilterPerson]:
        """Return named person options for the filter editor."""
        stacks = self._safe_call(lambda: self._face_review_service.person_stacks(), default=[])
        people: list[FilterPerson] = []
        for stack in stacks:
            # Only include clusters that have been given a name
            if not stack.person_name or not stack.person_name.strip():
                continue
            people.append(
                FilterPerson(
                    person_id=int(stack.person_id),
                    name=stack.person_name.strip(),
                )
            )
        return people

    def list_filter_years(self) -> list[int]:
        """Return available indexed years from AlbumService."""
        years = self._safe_call(lambda: self._album_service.list_years(), default=[])
        return [int(value) for value in years]

    def list_person_stacks(self) -> list[object]:
        """Return person stack summaries for naming workflows."""
        return self._safe_call(
            lambda: self._face_review_service.person_stacks(),
            default=[],
        )

    def rename_person_stack(self, person_id: int, name: str) -> None:
        """Rename one person stack."""
        self._face_review_service.rename_person_stack(person_id, name)

    def faces_for_image_path(self, file_path: str) -> list[object]:
        """Return detected faces for one image path."""
        return self._safe_call(
            lambda: self._face_review_service.faces_for_image_path(file_path),
            default=[],
        )

    def assign_face_name(self, face_id: int, name: str) -> None:
        """Assign one face to a person name."""
        self._face_review_service.assign_name(face_id, name)

    def persist_tree(self, root_nodes: list[AlbumTreeNode]) -> None:
        """Persist current album tree UI state."""
        self._persist_tree(root_nodes)

    def get_serialized_tree(self) -> list[dict[str, object]] | None:
        """Return the currently serialized album_tree from in-memory state."""
        return self._state.get("album_tree")

    def move_album(self, _node_id: str, _new_parent_id: str | None) -> bool:
        """Hook for tree move operation kept in UI state layer."""
        return True

    def _safe_call(self, fn: object, *, default: list[object]) -> list[object]:
        try:
            return fn()  # type: ignore[operator]
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))
            return default

    def _load_state(self) -> dict[str, object]:
        if not self._settings_path.exists():
            return {}
        try:
            return json.loads(self._settings_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}

    def _persist_tree(self, root_nodes: list[AlbumTreeNode]) -> None:
        self._state["album_tree"] = self._serialize_nodes(root_nodes)
        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        self._settings_path.write_text(
            json.dumps(self._state, indent=2),
            encoding="utf-8",
        )

    def _serialize_nodes(self, nodes: list[AlbumTreeNode]) -> list[dict[str, object]]:
        serialized: list[dict[str, object]] = []
        for node in nodes:
            serialized.append(
                {
                    "node_id": node.node_id,
                    "name": node.name,
                    "kind": node.kind,
                    "album_id": node.album_id,
                    "query_definition": node.query_definition,
                    "children": self._serialize_nodes(node.children),
                }
            )
        return serialized

    def _deserialize_nodes(
        self,
        payload: object,
        albums_by_id: dict[int, object],
    ) -> list[AlbumTreeNode]:
        if not isinstance(payload, list):
            return []
        nodes: list[AlbumTreeNode] = []
        for raw in payload:
            if not isinstance(raw, dict):
                continue
            node_id = raw.get("node_id")
            name = raw.get("name")
            kind = raw.get("kind")
            if not isinstance(node_id, str) or not isinstance(name, str) or not isinstance(kind, str):
                continue
            album_id = raw.get("album_id")
            if kind == "virtual":
                if not isinstance(album_id, int):
                    continue
                album = albums_by_id.get(album_id)
                if album is None:
                    continue
                query = self._album_query_to_dict(album.query_definition)
                item = AlbumTreeNode(
                    node_id=node_id,
                    name=album.name,
                    kind="virtual",
                    album_id=album_id,
                    query_definition=query,
                )
            else:
                item = AlbumTreeNode(
                    node_id=node_id,
                    name=name,
                    kind="folder",
                    query_definition=None,
                )
            children = self._deserialize_nodes(raw.get("children"), albums_by_id)
            for child in children:
                child.parent_id = item.node_id
            item.children = children
            nodes.append(item)
        return nodes

    def _flatten(self, nodes: list[AlbumTreeNode]) -> list[AlbumTreeNode]:
        output: list[AlbumTreeNode] = []
        stack = list(nodes)
        while stack:
            node = stack.pop()
            output.append(node)
            stack.extend(node.children)
        return output

    def _album_query_to_dict(self, query: object) -> dict[str, object]:
        """Convert an AlbumQuery to a plain dict, preserving all filter fields."""

        def _get(attr: str, default: object = None) -> object:
            return getattr(query, attr, default)

        # Dates: convert Python date -> ISO string so JSON serialisation is safe
        date_from = _get("date_from")
        date_to = _get("date_to")
        if date_from is not None and hasattr(date_from, "isoformat"):
            date_from = date_from.isoformat()
        if date_to is not None and hasattr(date_to, "isoformat"):
            date_to = date_to.isoformat()

        # Flags: stored as a tuple on AlbumQuery, dialogs expect a list
        flags = _get("flags", ())
        if not isinstance(flags, (list, tuple)):
            flags = []

        return {
            "person_ids": list(_get("person_ids") or []),
            "cluster_ids": list(_get("cluster_ids") or []),
            "tag_names": list(_get("tag_names") or []),
            "flags": list(flags),
            "rating_min": _get("rating_min"),
            "quality_min": _get("quality_min"),
            "camera_models": list(_get("camera_models") or []),
            "date_from": date_from,
            "date_to": date_to,
            "location_name": _get("location_name"),
            "gps_radius_km": _get("gps_radius_km"),
        }
