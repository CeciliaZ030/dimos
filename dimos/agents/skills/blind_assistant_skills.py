# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Skills for guiding a blind / low-vision user.

Exposes three agent-callable skills:
    - narrate(text):         continuous spoken updates (the user can't see)
    - ask_user(question):    blocks the agent loop until any user reply arrives
    - reply_user(status, summary): task termination signal

Maintains a JSON state snapshot published to the `agent_state` output stream
on every change, for display in the debug web UI.

Wiring (in your blueprint):
    - `user_reply` <- the same text channel `submit_query` already feeds
                      (e.g. LCM /human_input). Both initial requests and
                      replies arrive here; ask_user just unblocks on the
                      next message.
    - `agent_state` -> a text_stream named "agent_state" on the web server,
                       which the Vercel app subscribes to over SSE.

This module owns its own TTS. Remove SpeakSkill from the blueprint when
using BlindAssistant to avoid two pipelines fighting over the audio device.
"""

import json
import threading
import time

from reactivex import Subject
from reactivex.disposable import Disposable

from dimos.agents.annotation import skill
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.core.stream import In, Out
from dimos.stream.audio.node_output import SounddeviceAudioOutput
from dimos.stream.audio.tts.node_openai import OpenAITTSNode, Voice
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

VALID_STATUSES = ("arrived", "failed", "stopped")


class BlindAssistantSkillContainer(Module):
    user_reply: In[str]
    agent_state: Out[str]

    _tts_node: OpenAITTSNode | None = None
    _audio_output: SounddeviceAudioOutput | None = None
    _audio_lock: threading.Lock = threading.Lock()
    _reply_event: threading.Event = threading.Event()
    _latest_reply: str = ""

    _intent: str = ""
    _phase: str = "idle"
    _current_skill: dict | None = None
    _last_observation: str = ""
    _last_narration: str = ""
    _awaiting_user: str | None = None

    @rpc
    def start(self) -> None:
        super().start()
        self._tts_node = OpenAITTSNode(speed=1.2, voice=Voice.ONYX)
        self._audio_output = SounddeviceAudioOutput(sample_rate=24000)
        self._audio_output.consume_audio(self._tts_node.emit_audio())

        self.register_disposable(
            Disposable(self.user_reply.subscribe(self._on_user_reply))
        )
        self._publish_state()

    @rpc
    def stop(self) -> None:
        if self._tts_node:
            self._tts_node.dispose()
            self._tts_node = None
        if self._audio_output:
            self._audio_output.stop()
            self._audio_output = None
        super().stop()

    def _on_user_reply(self, text: str) -> None:
        self._latest_reply = text
        self._awaiting_user = None
        if self._phase == "awaiting_user":
            self._phase = "searching"
        self._reply_event.set()
        self._publish_state()

    def _publish_state(self) -> None:
        snapshot = {
            "ts": time.time(),
            "intent": self._intent,
            "phase": self._phase,
            "current_skill": self._current_skill,
            "last_observation": self._last_observation,
            "last_narration": self._last_narration,
            "awaiting_user": self._awaiting_user,
        }
        try:
            self.agent_state.publish(json.dumps(snapshot))
        except Exception as e:
            logger.warning("agent_state publish failed: %s", e)

    def _tts(self, text: str, timeout: float | None = None) -> bool:
        if self._tts_node is None:
            logger.error("TTS not initialized")
            return False
        with self._audio_lock:
            text_subject: Subject[str] = Subject()
            audio_complete = threading.Event()
            self._tts_node.consume_text(text_subject)

            subscription = self._tts_node.emit_text().subscribe(
                on_next=lambda _: audio_complete.set(),
                on_error=lambda _: audio_complete.set(),
            )
            text_subject.on_next(text)
            text_subject.on_completed()

            wait = timeout if timeout is not None else max(5, len(text) * 0.1)
            ok = audio_complete.wait(timeout=wait)
            subscription.dispose()
            if ok:
                time.sleep(0.3)
            return ok

    @skill
    def narrate(self, text: str) -> str:
        """Speak a short status update to the user.

        The user is blind. Narrate BEFORE acting, and whenever anything
        noteworthy happens (turning, pausing, seeing a sign, approaching an
        obstacle, losing track of the destination). One sentence per call.

        Example:
            narrate("I see a hallway with two doors, looking for a bathroom sign.")

        Args:
            text: one short sentence describing the current action or observation.
        """
        self._last_narration = text
        self._publish_state()
        ok = self._tts(text)
        return f"Narrated: {text}" if ok else f"TTS timeout: {text}"

    @skill
    def ask_user(self, question: str, timeout_s: float = 30.0) -> str:
        """Ask the user a question and BLOCK until they reply.

        Use this when you need a decision (e.g. 'should I look around?').
        The question is spoken aloud, the state phase becomes
        'awaiting_user', and this call returns only when a new user message
        arrives or the timeout expires.

        Args:
            question: one sentence, yes/no preferred.
            timeout_s: seconds to wait before giving up. Default 30.

        Returns:
            The text of the user's reply, or 'TIMEOUT' if none arrived.

        Example:
            answer = ask_user("I don't see a bathroom from here. Should I look around?")
        """
        self._awaiting_user = question
        self._phase = "awaiting_user"
        self._reply_event.clear()
        self._latest_reply = ""
        self._publish_state()

        self._tts(question)

        got_reply = self._reply_event.wait(timeout=timeout_s)
        if not got_reply:
            self._awaiting_user = None
            self._phase = "searching"
            self._publish_state()
            return "TIMEOUT"
        return self._latest_reply

    @skill
    def reply_user(self, status: str, summary: str) -> str:
        """Terminate the current task and report its outcome.

        Call ONLY when the task is fully complete or has unrecoverably failed.
        Do not call mid-task — use `narrate` for progress updates.

        Args:
            status: one of 'arrived', 'failed', 'stopped'.
            summary: one sentence describing the outcome.

        Example:
            reply_user(status="arrived", summary="We're at the bathroom entrance.")
        """
        if status not in VALID_STATUSES:
            return f"Error: invalid status '{status}'. Use one of {VALID_STATUSES}."

        self._phase = {"arrived": "done", "failed": "failed", "stopped": "idle"}[status]
        self._last_narration = summary
        self._publish_state()
        self._tts(summary)
        return f"Task ended: status={status}"

    @rpc
    def set_intent(self, intent: str) -> None:
        """Called by the planner / agent at the start of a new task."""
        self._intent = intent
        self._phase = "searching"
        self._publish_state()

    @rpc
    def set_observation(self, observation: str) -> None:
        """Called by the VLM loop with a one-line scene description."""
        self._last_observation = observation
        self._publish_state()

    @rpc
    def set_current_skill(self, name: str, args: dict, state: str) -> None:
        """Called from the agent loop when a skill is dispatched / completes."""
        self._current_skill = {"name": name, "args": args, "state": state}
        self._publish_state()
