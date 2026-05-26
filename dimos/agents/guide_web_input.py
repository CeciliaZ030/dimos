# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Web input + state-stream variant for the guide-robot blueprint.

Same as `WebInput` but also exposes an `agent_state` text stream that the
Vercel webapp subscribes to over SSE. Auto-connects to
`BlindAssistantSkillContainer.agent_state: Out[str]`.
"""

from threading import Thread
from typing import TYPE_CHECKING

import reactivex as rx
import reactivex.operators as ops

from dimos.constants import DEFAULT_THREAD_JOIN_TIMEOUT
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.core.stream import In
from dimos.core.transport import pLCMTransport
from dimos.stream.audio.node_normalizer import AudioNormalizer
from dimos.utils.logging_config import setup_logger
from dimos.web.robot_web_interface import RobotWebInterface

if TYPE_CHECKING:
    from dimos.stream.audio.base import AudioEvent

logger = setup_logger()


class GuideWebInput(Module):
    """Web input + agent_state SSE for the guide blueprint.

    Set `enable_stt=False` to skip Whisper initialization (and its model
    download). Without STT, voice via `/upload_audio` is disabled, but text
    queries via `/submit_query` still work — useful for testing the network
    path without waiting for the model to land.
    """

    agent_state: In[str]

    _web_interface: RobotWebInterface | None = None
    _thread: Thread | None = None
    _human_transport: pLCMTransport[str] | None = None
    _agent_state_subject: rx.subject.Subject[str] | None = None

    def __init__(self, enable_stt: bool = True, **kwargs):  # type: ignore[no-untyped-def]
        super().__init__(**kwargs)
        self.enable_stt = enable_stt

    @rpc
    def start(self) -> None:
        super().start()

        self._human_transport = pLCMTransport("/human_input")
        self._agent_state_subject = rx.subject.Subject()

        audio_subject: rx.subject.Subject[AudioEvent] | None = (
            rx.subject.Subject() if self.enable_stt else None
        )

        self._web_interface = RobotWebInterface(
            port=5555,
            text_streams={
                "agent_responses": rx.subject.Subject(),
                "agent_state": self._agent_state_subject,
            },
            audio_subject=audio_subject,
        )

        if self.enable_stt and audio_subject is not None:
            normalizer = AudioNormalizer()
            from dimos.stream.audio.stt.node_whisper import WhisperNode

            stt_node = WhisperNode()
            normalizer.consume_audio(audio_subject.pipe(ops.share()))
            stt_node.consume_audio(normalizer.emit_audio())
            unsub = stt_node.emit_text().subscribe(self._human_transport.publish)
            self.register_disposable(unsub)
        else:
            logger.info("STT disabled — voice uploads will be ignored, text only.")

        # Browser → /human_input
        unsub = self._web_interface.query_stream.subscribe(self._human_transport.publish)
        self.register_disposable(unsub)

        # BlindAssistant.agent_state → SSE text_stream
        unsub = self.agent_state.subscribe(self._agent_state_subject.on_next)
        self.register_disposable(unsub)

        self._thread = Thread(target=self._web_interface.run, daemon=True)
        self._thread.start()

        logger.info("Guide web interface started at http://localhost:5555")

    @rpc
    def stop(self) -> None:
        if self._web_interface:
            self._web_interface.shutdown()
        if self._thread:
            self._thread.join(timeout=DEFAULT_THREAD_JOIN_TIMEOUT)
        if self._human_transport:
            self._human_transport.lcm.stop()
        super().stop()
