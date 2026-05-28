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

import time

from dimos.mapping.relocalization.module import Config, RelocalizationModule
from dimos.msgs.geometry_msgs.Transform import Transform


def _module_for_status(map_file: str | None = None) -> RelocalizationModule:
    module = RelocalizationModule.__new__(RelocalizationModule)
    module.config = Config(map_file=map_file)
    module._last_world_to_map = None
    module._last_fitness = None
    module._last_relocalization_ts = None
    return module


def test_relocalization_status_disabled_by_default() -> None:
    module = _module_for_status()

    status = module.get_status()

    assert status["enabled"] is False
    assert status["localized"] is False
    assert status["map_file"] is None


def test_relocalization_status_reports_last_accepted_transform() -> None:
    module = _module_for_status(map_file="office_twopass_map")
    module._last_world_to_map = Transform(frame_id="world", child_frame_id="map")
    module._last_fitness = 0.67
    module._last_relocalization_ts = time.time()

    status = module.get_status()

    assert status["enabled"] is True
    assert status["localized"] is True
    assert status["map_file"] == "office_twopass_map"
    assert status["last_fitness"] == 0.67
    assert status["last_update_age_sec"] >= 0.0
