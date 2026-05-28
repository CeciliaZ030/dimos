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

import pytest

from dimos.agents.skills.navigation import (
    MAP_FRAME,
    WORLD_FRAME,
    pose_from_robot_location,
    robot_location_from_transform,
)
from dimos.msgs.geometry_msgs.Quaternion import Quaternion
from dimos.msgs.geometry_msgs.Transform import Transform
from dimos.msgs.geometry_msgs.Vector3 import Vector3


def test_map_place_converts_to_world_goal() -> None:
    world_to_map = Transform(
        translation=Vector3(10.0, 1.0, 0.0),
        rotation=Quaternion(),
        frame_id=WORLD_FRAME,
        child_frame_id=MAP_FRAME,
    )
    map_to_place = Transform(
        translation=Vector3(2.0, 3.0, 0.0),
        rotation=Quaternion(),
        frame_id=MAP_FRAME,
        child_frame_id="base_link",
    )

    location = robot_location_from_transform("toilet", MAP_FRAME, map_to_place)
    goal = pose_from_robot_location(location, world_to_map=world_to_map)

    assert goal is not None
    assert goal.frame_id == WORLD_FRAME
    assert goal.position.x == pytest.approx(12.0)
    assert goal.position.y == pytest.approx(4.0)


def test_map_place_requires_world_to_map_transform() -> None:
    map_to_place = Transform(
        translation=Vector3(2.0, 3.0, 0.0),
        rotation=Quaternion(),
        frame_id=MAP_FRAME,
        child_frame_id="base_link",
    )

    location = robot_location_from_transform("toilet", MAP_FRAME, map_to_place)

    assert pose_from_robot_location(location, world_to_map=None) is None


def test_world_place_does_not_require_relocalization() -> None:
    world_to_place = Transform(
        translation=Vector3(2.0, 3.0, 0.0),
        rotation=Quaternion(),
        frame_id=WORLD_FRAME,
        child_frame_id="base_link",
    )

    location = robot_location_from_transform("temporary spot", WORLD_FRAME, world_to_place)
    goal = pose_from_robot_location(location)

    assert goal is not None
    assert goal.frame_id == WORLD_FRAME
    assert goal.position.x == pytest.approx(2.0)
    assert goal.position.y == pytest.approx(3.0)
