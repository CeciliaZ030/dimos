#!/usr/bin/env python3
# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Lightweight variant of unitree-go2-guide for network-pipe testing.

Same agent + skills + web stack, but without spatial perception (CLIP) or
person tracking (YOLO). The robot can speak / ask / reply but cannot navigate.
Use this to verify the Vercel <-> Tailscale <-> dimos HTTPS path end to end
before pulling the heavy ML model archives.

Usage:
    dimos --replay run unitree-go2-guide-lite
"""

from dimos.agents.blind_assistant_prompt import BLIND_ASSISTANT_PROMPT
from dimos.agents.guide_web_input import GuideWebInput
from dimos.agents.mcp.mcp_client import McpClient
from dimos.agents.mcp.mcp_server import McpServer
from dimos.agents.skills.blind_assistant_skills import BlindAssistantSkillContainer
from dimos.core.coordination.blueprints import autoconnect
from dimos.robot.unitree.go2.connection import GO2Connection

unitree_go2_guide_lite = autoconnect(
    GO2Connection.blueprint(),
    BlindAssistantSkillContainer.blueprint(),
    GuideWebInput.blueprint(enable_stt=False),
    McpServer.blueprint(),
    McpClient.blueprint(system_prompt=BLIND_ASSISTANT_PROMPT),
)

__all__ = ["unitree_go2_guide_lite"]
