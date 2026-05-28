#!/usr/bin/env python3
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

from dimos.agents.mcp.mcp_client import McpClient
from dimos.agents.mcp.mcp_server import McpServer
from dimos.core.coordination.blueprints import autoconnect
from dimos.core.global_config import global_config
from dimos.experimental.security_demo.security_module import SecurityModule
from dimos.perception.perceive_loop_skill import PerceiveLoopSkill
from dimos.perception.spatial_perception import SpatialMemory
from dimos.robot.unitree.go2.blueprints.agentic._common_agentic import _common_agentic
from dimos.robot.unitree.go2.blueprints.smart.unitree_go2 import unitree_go2_relocalization
from dimos.robot.unitree.go2.connection import GO2Connection

unitree_go2_agentic_relocalization = autoconnect(
    unitree_go2_relocalization,
    SpatialMemory.blueprint(new_memory=global_config.new_memory),
    PerceiveLoopSkill.blueprint(),
    SecurityModule.blueprint(camera_info=GO2Connection.camera_info_static),
    McpServer.blueprint(),
    McpClient.blueprint(),
    _common_agentic,
).global_config(n_workers=12, robot_model="unitree_go2")

__all__ = ["unitree_go2_agentic_relocalization"]
