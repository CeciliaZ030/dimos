# Copyright 2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from typing import Any

from dimos.agents_deprecated.memory.spatial_vector_db import SpatialVectorDB
from dimos.types.robot_location import RobotLocation


class _FakeImageCollection:
    def get(self, include: list[str] | None = None) -> dict[str, list[Any]]:
        return {
            "ids": ["near", "far"],
            "metadatas": [
                {"pos_x": 1.0, "pos_y": 1.0, "pos_z": 0.0},
                {"pos_x": 10.0, "pos_y": 10.0, "pos_z": 0.0},
            ],
        }


class _FakeLocationCollection:
    def __init__(self) -> None:
        self.deleted_where: dict[str, Any] | None = None
        self.added: dict[str, Any] | None = None

    def delete(self, where: dict[str, Any]) -> None:
        self.deleted_where = where

    def add(
        self,
        *,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        self.added = {"ids": ids, "documents": documents, "metadatas": metadatas}


def test_query_by_location_accepts_pos_metadata_keys() -> None:
    db = SpatialVectorDB.__new__(SpatialVectorDB)
    db.image_collection = _FakeImageCollection()

    results = db.query_by_location(1.0, 1.0, radius=1.0)

    assert [result["id"] for result in results] == ["near"]


def test_tag_location_stores_description_and_replaces_existing_name() -> None:
    db = SpatialVectorDB.__new__(SpatialVectorDB)
    fake_collection = _FakeLocationCollection()
    db.location_collection = fake_collection

    location = RobotLocation(
        name="toilet",
        position=(1.0, 2.0, 0.0),
        rotation=(0.0, 0.0, 0.0),
        metadata={
            "description": "bathroom restroom",
            "coordinate_frame": "map",
            "map_file": "office_twopass_map",
        },
    )

    db.tag_location(location)

    assert fake_collection.deleted_where == {"location_name": "toilet"}
    assert fake_collection.added is not None
    assert fake_collection.added["documents"] == ["toilet bathroom restroom"]
    metadata = fake_collection.added["metadatas"][0]
    assert metadata["coordinate_frame"] == "map"
    assert metadata["map_file"] == "office_twopass_map"
