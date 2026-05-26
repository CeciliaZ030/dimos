#!/usr/bin/env python3
# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Guide-robot blueprint: Unitree Go2 assisting a blind user via a Vercel webapp.

Differs from `unitree_go2_agentic`:
  - Uses `BlindAssistantSkillContainer` (narrate / ask_user / reply_user) instead of `SpeakSkill`.
  - Replaces `WebInput` with `GuideWebInput`, which exposes an `agent_state` SSE stream.
  - Loads `BLIND_ASSISTANT_PROMPT` into the MCP client so the agent obeys the guide protocol.

Usage:
    dimos run unitree-go2-guide
"""

from dimos.agents.blind_assistant_prompt import BLIND_ASSISTANT_PROMPT
from dimos.agents.guide_web_input import GuideWebInput
from dimos.agents.mcp.mcp_client import McpClient
from dimos.agents.mcp.mcp_server import McpServer
from dimos.agents.skills.blind_assistant_skills import BlindAssistantSkillContainer
from dimos.agents.skills.navigation import NavigationSkillContainer
from dimos.agents.skills.person_follow import PersonFollowSkillContainer
from dimos.core.coordination.blueprints import autoconnect
from dimos.robot.unitree.go2.blueprints.smart.unitree_go2_spatial import unitree_go2_spatial
from dimos.robot.unitree.go2.connection import GO2Connection
from dimos.robot.unitree.unitree_skill_container import UnitreeSkillContainer

unitree_go2_guide = autoconnect(
    unitree_go2_spatial,
    NavigationSkillContainer.blueprint(),
    PersonFollowSkillContainer.blueprint(camera_info=GO2Connection.camera_info_static),
    UnitreeSkillContainer.blueprint(),
    BlindAssistantSkillContainer.blueprint(),
    GuideWebInput.blueprint(),
    McpServer.blueprint(),
    McpClient.blueprint(system_prompt=BLIND_ASSISTANT_PROMPT),
).global_config(obstacle_avoidance=True)

__all__ = ["unitree_go2_guide"]
