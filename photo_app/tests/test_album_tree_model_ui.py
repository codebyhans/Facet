from __future__ import annotations

from photo_app.app.models.album_tree_model import AlbumTreeModel, AlbumTreeNode


def test_album_tree_construction() -> None:
    model = AlbumTreeModel()
    nodes = [
        AlbumTreeNode(
            node_id="f-family",
            name="Family",
            kind="folder",
            children=[
                AlbumTreeNode(node_id="v-1", name="Anna", kind="virtual", album_id=1),
                AlbumTreeNode(node_id="v-2", name="Jonas", kind="virtual", album_id=2),
            ],
        )
    ]
    model.set_nodes(nodes)

    assert model.rowCount() == 1
    family = model.index(0, 0)
    assert model.data(family) == "Family"
    assert model.rowCount(family) == 2


def test_album_tree_add_and_move() -> None:
    model = AlbumTreeModel()
    model.set_nodes([AlbumTreeNode(node_id="f-root", name="Root", kind="folder")])
    model.add_node(AlbumTreeNode(node_id="v-1", name="Album", kind="virtual", album_id=1), "f-root")

    parent_index = model.index_from_node_id("f-root")
    assert model.rowCount(parent_index) == 1

    moved = model.move_node("v-1", None)
    assert moved
    assert model.rowCount(parent_index) == 0
    assert model.rowCount() == 2


def test_album_tree_prevents_subtree_move() -> None:
    model = AlbumTreeModel()
    child = AlbumTreeNode(node_id="f-child", name="Child", kind="folder")
    parent = AlbumTreeNode(node_id="f-parent", name="Parent", kind="folder", children=[child])
    model.set_nodes([parent])

    assert not model.can_move("f-parent", "f-child")
