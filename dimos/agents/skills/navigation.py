# Copyright 2025-2026 Dimensional Inc.
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

import time
from typing import Any

from reactivex.disposable import Disposable

from dimos.agents.annotation import skill
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.core.stream import In
from dimos.mapping.relocalization.module_spec import RelocalizationSpec
from dimos.models.qwen.bbox import BBox
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Quaternion import Quaternion
from dimos.msgs.geometry_msgs.Transform import Transform
from dimos.msgs.geometry_msgs.Vector3 import make_vector3
from dimos.msgs.sensor_msgs.Image import Image
from dimos.navigation.base import NavigationState
from dimos.navigation.navigation_spec import NavigationInterfaceSpec
from dimos.navigation.visual.query import get_object_bbox_from_image
from dimos.perception.object_tracking_spec import ObjectTrackingSpec
from dimos.perception.spatial_memory_spec import SpatialMemorySpec
from dimos.types.robot_location import RobotLocation
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

COORDINATE_FRAME_KEY = "coordinate_frame"
MAP_FRAME = "map"
WORLD_FRAME = "world"
BASE_LINK_FRAME = "base_link"
SAVED_PLACE_FRAME = "saved_place"


def robot_location_from_transform(
    name: str,
    coordinate_frame: str,
    frame_to_place: Transform,
    *,
    description: str = "",
    map_file: str | None = None,
) -> RobotLocation:
    euler = frame_to_place.rotation.to_euler()
    metadata: dict[str, Any] = {
        COORDINATE_FRAME_KEY: coordinate_frame,
        "description": description or name,
    }
    if map_file is not None:
        metadata["map_file"] = map_file

    return RobotLocation(
        name=name,
        position=(
            frame_to_place.translation.x,
            frame_to_place.translation.y,
            frame_to_place.translation.z,
        ),
        rotation=(euler.x, euler.y, euler.z),
        metadata=metadata,
    )


def pose_from_robot_location(
    location: RobotLocation,
    *,
    world_to_map: Transform | None = None,
) -> PoseStamped | None:
    coordinate_frame = str(location.metadata.get(COORDINATE_FRAME_KEY, WORLD_FRAME))
    location_tf = Transform(
        translation=make_vector3(*location.position),
        rotation=Quaternion.from_euler(make_vector3(*location.rotation)),
        frame_id=coordinate_frame,
        child_frame_id=SAVED_PLACE_FRAME,
    )

    if coordinate_frame == MAP_FRAME:
        if world_to_map is None:
            return None
        return (world_to_map + location_tf).to_pose()

    return location_tf.to_pose()


class NavigationSkillContainer(Module):
    _latest_image: Image | None = None
    _latest_odom: PoseStamped | None = None
    _skill_started: bool = False
    _similarity_threshold: float = 0.23

    _spatial_memory: SpatialMemorySpec
    _navigation: NavigationInterfaceSpec
    _relocalization: RelocalizationSpec | None = None
    _object_tracking: ObjectTrackingSpec | None = None

    color_image: In[Image]
    odom: In[PoseStamped]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._skill_started = False

        # Here to prevent unwanted imports in the file.
        from dimos.models.vl.qwen import QwenVlModel

        self._vl_model = QwenVlModel()

    @rpc
    def start(self) -> None:
        super().start()
        self.register_disposable(Disposable(self.color_image.subscribe(self._on_color_image)))
        self.register_disposable(Disposable(self.odom.subscribe(self._on_odom)))
        self._skill_started = True

    @rpc
    def stop(self) -> None:
        super().stop()

    def _on_color_image(self, image: Image) -> None:
        self._latest_image = image

    def _on_odom(self, odom: PoseStamped) -> None:
        self._latest_odom = odom

    @skill
    def tag_location(self, location_name: str) -> str:
        """Tag this location in the spatial memory with a name.

        This associates the current location with the given name in the spatial memory, allowing you to navigate back to it.

        Args:
            location_name (str): the name for the location

        Returns:
            str: the outcome
        """

        return self._remember_place(location_name, description="", require_map_frame=False)

    @skill
    def remember_place(self, name: str, description: str = "") -> str:
        """Remember the current place in the saved map for future navigation.

        Use this when the user labels the current location, for example "this is the toilet".
        The robot must be relocalized against a saved map so the place survives restarts.

        Args:
            name: Short place name, such as "toilet", "front door", or "charger".
            description: Optional extra words or aliases that should match this place later.
        """
        return self._remember_place(name, description=description, require_map_frame=True)

    @skill
    def localization_status(self) -> str:
        """Report whether saved-map relocalization is configured and currently localized."""
        status = self._get_relocalization_status()
        if status is None:
            return "Saved-map relocalization is not configured in this blueprint."

        if not status.get("enabled"):
            return "Saved-map relocalization is disabled because no map file is configured."

        map_file = status.get("map_file")
        if not status.get("localized"):
            return f"Loaded map '{map_file}', but relocalization has not succeeded yet."

        fitness = status.get("last_fitness")
        age = status.get("last_update_age_sec")
        fitness_text = f"{fitness:.3f}" if isinstance(fitness, float) else "unknown"
        age_text = f"{age:.1f}s ago" if isinstance(age, float) else "unknown age"
        return f"Localized on map '{map_file}' with fitness {fitness_text}; last update {age_text}."

    def _remember_place(
        self,
        name: str,
        *,
        description: str,
        require_map_frame: bool,
    ) -> str:
        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")

        status = self._get_relocalization_status()
        if status and status.get("localized"):
            map_to_base = self.tf.get(MAP_FRAME, BASE_LINK_FRAME, time_tolerance=1.0)
            if map_to_base is not None:
                location = robot_location_from_transform(
                    name,
                    MAP_FRAME,
                    map_to_base,
                    description=description,
                    map_file=status.get("map_file"),
                )
                if not self._spatial_memory.tag_location(location):
                    return f"Error: Failed to store '{name}' in the spatial memory."
                logger.info(f"Remembered map-stable place {location}")
                return (
                    f"Remembered '{name}' in the saved map at "
                    f"({location.position[0]:.2f}, {location.position[1]:.2f})."
                )

        if require_map_frame:
            return (
                "I cannot remember this as a saved-map place yet because relocalization is not ready. "
                "Start the relocalized blueprint with a map file and wait until localization_status says localized."
            )

        if not self._latest_odom:
            return "No odometry data received yet, cannot tag location."

        world_to_base = Transform.from_pose(BASE_LINK_FRAME, self._latest_odom)
        location = robot_location_from_transform(
            name,
            self._latest_odom.frame_id or WORLD_FRAME,
            world_to_base,
            description=description,
        )

        if not self._spatial_memory.tag_location(location):
            return f"Error: Failed to store '{name}' in the spatial memory"

        logger.info(f"Tagged non-relocalized place {location}")
        return f"Tagged '{name}': ({location.position[0]:.2f},{location.position[1]:.2f})."

    @skill
    def navigate_with_text(self, query: str) -> str:
        """Navigate to a location by querying the existing semantic map using natural language.

        First attempts to locate an object in the robot's camera view using vision.
        If the object is found, navigates to it. If not, falls back to querying the
        semantic map for a location matching the description.
        CALL THIS SKILL FOR ONE SUBJECT AT A TIME. For example: "Go to the person wearing a blue shirt in the living room",
        you should call this skill twice, once for the person wearing a blue shirt and once for the living room.
        Args:
            query: Text query to search for in the semantic map
        """

        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")
        success_msg = self._navigate_by_tagged_location(query)
        if success_msg:
            return success_msg

        logger.info(f"No tagged location found for {query}")

        success_msg = self._navigate_to_object(query)
        if success_msg:
            return success_msg

        logger.info(f"No object in view found for {query}")

        success_msg = self._navigate_using_semantic_map(query)
        if success_msg:
            return success_msg

        return f"No tagged location called '{query}'. No object in view matching '{query}'. No matching location found in semantic map for '{query}'."

    def _navigate_by_tagged_location(self, query: str) -> str | None:
        robot_location = self._spatial_memory.query_tagged_location(query)

        if not robot_location:
            return None

        logger.info("Found tagged location", location=robot_location)
        goal_pose = self._goal_pose_from_robot_location(robot_location)
        if goal_pose is None:
            return (
                f"Found saved place '{robot_location.name}', but I am not relocalized in the saved map. "
                "Call localization_status and wait for relocalization before navigating to saved places."
            )

        return self._navigate_to(goal_pose, f"Found a tagged location called '{query}'.")

    @skill
    def go_to_place(self, query: str) -> str:
        """Navigate to a manually remembered place in saved-place memory.

        Args:
            query: Place name or description, such as "toilet" or "front door".
        """
        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")

        result = self._navigate_by_tagged_location(query)
        if result:
            return result
        return f"No remembered place matching '{query}'."

    def _navigate_to(self, pose: PoseStamped, message: str) -> str:
        logger.info(
            f"Navigating to pose: ({pose.position.x:.2f}, {pose.position.y:.2f}, {pose.position.z:.2f})"
        )
        self._navigation.set_goal(pose)

        return (
            f"{message}. Started navigating to that position. "
            f"To cancel movement call the 'stop_navigation' tool."
        )

    def _navigate_to_object(self, query: str) -> str | None:
        if self._object_tracking is None:
            return None

        try:
            bbox = self._get_bbox_for_current_frame(query)
        except Exception:
            logger.error(f"Failed to get bbox for {query}", exc_info=True)
            return None

        if bbox is None:
            return None

        logger.info(f"Found {query} at {bbox}")

        # Start tracking - BBoxNavigationModule automatically generates goals
        self._object_tracking.track(bbox)  # type: ignore[arg-type]

        start_time = time.time()
        timeout = 30.0
        goal_set = False

        while time.time() - start_time < timeout:
            # Check if navigator finished
            if self._navigation.get_state() == NavigationState.IDLE and goal_set:
                logger.info("Waiting for goal result")
                time.sleep(1.0)
                if not self._navigation.is_goal_reached():
                    logger.info(f"Goal cancelled, tracking '{query}' failed")
                    self._object_tracking.stop_track()
                    return None
                else:
                    logger.info(f"Reached '{query}'")
                    self._object_tracking.stop_track()
                    return f"Successfully arrived at '{query}'"

            # If goal set and tracking lost, just continue (tracker will resume or timeout)
            if goal_set and not self._object_tracking.is_tracking():
                continue

            # BBoxNavigationModule automatically sends goals when tracker publishes
            # Just check if we have any detections to mark goal_set
            if self._object_tracking.is_tracking():
                goal_set = True

            time.sleep(0.25)

        logger.warning(f"Navigation to '{query}' timed out after {timeout}s")
        self._object_tracking.stop_track()
        return None

    def _get_bbox_for_current_frame(self, query: str) -> BBox | None:
        if self._latest_image is None:
            return None

        return get_object_bbox_from_image(self._vl_model, self._latest_image, query)

    def _navigate_using_semantic_map(self, query: str) -> str:
        results = self._spatial_memory.query_by_text(query)

        if not results:
            return f"No matching location found in semantic map for '{query}'"

        best_match = results[0]

        goal_pose = self._get_goal_pose_from_result(best_match)

        logger.info("Goal pose for semantic nav", pose=goal_pose)
        if not goal_pose:
            return f"Found a result for '{query}' but it didn't have a valid position."

        message = f"Found a location in the semantic map matching '{query}'."
        return self._navigate_to(goal_pose, message)

    @skill
    def stop_navigation(self) -> str:
        """Immediatly stop moving."""

        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")

        self._cancel_goal_and_stop()

        return "Stopped"

    def _cancel_goal_and_stop(self) -> None:
        self._navigation.cancel_goal()

    def _get_goal_pose_from_result(self, result: dict[str, Any]) -> PoseStamped | None:
        similarity = 1.0 - (result.get("distance") or 1)
        if similarity < self._similarity_threshold:
            logger.warning(
                f"Match found but similarity score ({similarity:.4f}) is below threshold ({self._similarity_threshold})"
            )
            return None

        metadata = result.get("metadata")
        if not metadata:
            return None
        first = metadata[0]
        pos_x = first.get("pos_x", 0)
        pos_y = first.get("pos_y", 0)
        theta = first.get("rot_z", 0)

        return PoseStamped(
            position=make_vector3(pos_x, pos_y, 0),
            orientation=Quaternion.from_euler(make_vector3(0, 0, theta)),
            frame_id="map",
        )

    def _goal_pose_from_robot_location(self, location: RobotLocation) -> PoseStamped | None:
        world_to_map = None
        if location.metadata.get(COORDINATE_FRAME_KEY) == MAP_FRAME:
            world_to_map = self.tf.get(WORLD_FRAME, MAP_FRAME, time_tolerance=1.0)
        return pose_from_robot_location(location, world_to_map=world_to_map)

    def _get_relocalization_status(self) -> dict[str, Any] | None:
        if self._relocalization is None:
            return None
        return self._relocalization.get_status()
