#!/usr/bin/env python3
# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Guide-robot blueprint stacked on the proven unitree-go2-agentic base.

Replaces three modules from the agentic stack:
    * WebInput   -> GuideWebInput          (adds agent_state SSE stream)
    * SpeakSkill -> (removed, owned by BlindAssistant's narrate)
    * McpClient  -> McpClient(BLIND_ASSISTANT_PROMPT)  (custom system prompt)

Adds BlindAssistantSkillContainer for narrate / ask_user / reply_user.

Usage:
    uv run dimos --replay run unitree-go2-guide-full --disable security-module
"""

from dimos.agents.guide_web_input import GuideWebInput
from dimos.agents.skills.blind_assistant_skills import BlindAssistantSkillContainer
from dimos.agents.skills.speak_skill import SpeakSkill
from dimos.agents.web_human_input import WebInput
from dimos.core.coordination.blueprints import autoconnect
from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic import unitree_go2_agentic

# We keep the agentic blueprint's McpClient (autoconnect's disabled_modules
# mechanism makes re-adding McpClient impossible without forking the base
# blueprint). The blind-assistant system prompt is wired in by editing
# dimos/agents/system_prompt.py to import from blind_assistant_prompt.
unitree_go2_guide_full = autoconnect(
    unitree_go2_agentic.disabled_modules(WebInput, SpeakSkill),
    BlindAssistantSkillContainer.blueprint(),
    GuideWebInput.blueprint(enable_stt=False),
)

__all__ = ["unitree_go2_guide_full"]
