from __future__ import annotations

from photo_app.app.models.album_tree_model import AlbumTreeModel, AlbumTreeNode

ROOT_CHILD_COUNT = 1
FAMILY_CHILD_COUNT = 2
TOP_LEVEL_AFTER_MOVE = 2


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

    assert model.rowCount() == ROOT_CHILD_COUNT
    family = model.index(0, 0)
    assert model.data(family) == "Family"
    assert model.rowCount(family) == FAMILY_CHILD_COUNT


def test_album_tree_add_and_move() -> None:
    model = AlbumTreeModel()
    model.set_nodes([AlbumTreeNode(node_id="f-root", name="Root", kind="folder")])
    model.add_node(
        AlbumTreeNode(node_id="v-1", name="Album", kind="virtual", album_id=1), "f-root"
    )

    parent_index = model.index_from_node_id("f-root")
    assert model.rowCount(parent_index) == ROOT_CHILD_COUNT

    moved = model.move_node("v-1", None)
    assert moved
    assert model.rowCount(parent_index) == 0
    assert model.rowCount() == TOP_LEVEL_AFTER_MOVE


def test_album_tree_reorder_within_parent() -> None:
    model = AlbumTreeModel()
    model.set_nodes(
        [
            AlbumTreeNode(node_id="f-root", name="Root", kind="folder"),
        ]
    )
    model.add_node(
        AlbumTreeNode(node_id="v-1", name="Alpha", kind="virtual", album_id=1), "f-root"
    )
    model.add_node(
        AlbumTreeNode(node_id="v-2", name="Beta", kind="virtual", album_id=2), "f-root"
    )
    model.add_node(
        AlbumTreeNode(node_id="v-3", name="Gamma", kind="virtual", album_id=3), "f-root"
    )

    assert model.node_position("v-3") == ("f-root", 2)

    moved = model.move_node("v-3", "f-root", 0)
    assert moved
    assert model.node_position("v-3") == ("f-root", 0)
    assert model.node_position("v-1") == ("f-root", 1)
    assert model.node_position("v-2") == ("f-root", 2)


def test_album_tree_prevents_subtree_move() -> None:
    model = AlbumTreeModel()
    child = AlbumTreeNode(node_id="f-child", name="Child", kind="folder")
    parent = AlbumTreeNode(
        node_id="f-parent", name="Parent", kind="folder", children=[child]
    )
    model.set_nodes([parent])

    assert not model.can_move("f-parent", "f-child")


def _flat_model() -> AlbumTreeModel:
    model = AlbumTreeModel()
    model.set_nodes(
        [
            AlbumTreeNode(node_id="f-dest", name="Dest", kind="folder"),
            *(
                AlbumTreeNode(
                    node_id=f"v-{i}", name=f"A{i}", kind="virtual", album_id=i
                )
                for i in range(1, 6)
            ),
        ]
    )
    return model


def _child_ids(model: AlbumTreeModel, parent_id: str | None) -> list[str]:
    parent_index = (
        model.index_from_node_id(parent_id) if parent_id is not None else None
    )
    return [
        str(model.data(model.index(row, 0, parent_index), AlbumTreeModel.NodeIdRole))
        for row in range(model.rowCount(parent_index))
    ]


def test_album_tree_move_many_into_folder_keeps_order() -> None:
    model = _flat_model()

    # Selection order shouldn't matter; tree order is kept.
    moved = model.move_nodes(["v-4", "v-2"], "f-dest")

    assert moved == ["v-2", "v-4"]
    assert _child_ids(model, "f-dest") == ["v-2", "v-4"]
    assert _child_ids(model, None) == ["f-dest", "v-1", "v-3", "v-5"]


def test_album_tree_move_many_reorders_within_parent() -> None:
    model = _flat_model()

    # Drop v-4 and v-5 above v-2 (row 2 at root).
    model.move_nodes(["v-4", "v-5"], None, 2)
    assert _child_ids(model, None) == ["f-dest", "v-1", "v-4", "v-5", "v-2", "v-3"]

    # Drop v-1 and v-2 below v-3 (row 6), i.e. past earlier rows being removed.
    model.move_nodes(["v-1", "v-2"], None, 6)
    assert _child_ids(model, None) == ["f-dest", "v-4", "v-5", "v-3", "v-1", "v-2"]


def test_album_tree_move_many_skips_descendants_of_moved_folder() -> None:
    model = AlbumTreeModel()
    inner = AlbumTreeNode(node_id="v-1", name="Inner", kind="virtual", album_id=1)
    model.set_nodes(
        [
            AlbumTreeNode(node_id="f-a", name="A", kind="folder", children=[inner]),
            AlbumTreeNode(node_id="f-b", name="B", kind="folder"),
        ]
    )

    moved = model.move_nodes(["f-a", "v-1"], "f-b")

    assert moved == ["f-a"]
    assert _child_ids(model, "f-b") == ["f-a"]
    assert _child_ids(model, "f-a") == ["v-1"]
