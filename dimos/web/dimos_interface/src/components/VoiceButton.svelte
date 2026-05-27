<!--
 Copyright 2025 Dimensional Inc.

 Licensed under the Apache License, Version 2.0 (the "License");
 you may not use this file except in compliance with the License.
 You may obtain a copy of the License at

     http://www.apache.org/licenses/LICENSE-2.0

 Unless required by applicable law or agreed to in writing, software
 distributed under the License is distributed on an "AS IS" BASIS,
 WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 See the License for the specific language governing permissions and
 limitations under the License.
-->

<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import { theme } from '../stores/theme';
  import { connectTextStream } from '../stores/stream';
  import {
    isSTTSupported,
    startSpeechSession,
    submitQuery,
    type SpeechSession,
  } from '../lib/dimos';

  const dispatch = createEventDispatcher();

  let isRecording = false;
  let isProcessing = false;
  let session: SpeechSession | null = null;
  // Held by callers that want to react to interim transcripts (e.g. show a
  // "hearing…" UI). Dispatched on each partial.
  let partial = '';

  // Tag toggle — defaults to user_speech. If a parent reactively flips this
  // (e.g. when an awaiting_user prompt is active), incoming text is sent as
  // a reply instead of a new request. Exposed as a Svelte prop.
  export let replyMode = false;

  async function toggleRecording() {
    if (isRecording && session) {
      // End the session — onFinal in startSpeechSession will fire after onend.
      session.stop();
      isRecording = false;
      isProcessing = true;
      return;
    }

    if (!isSTTSupported()) {
      dispatch('voiceCommand', { success: false, error: 'Speech recognition not supported in this browser' });
      return;
    }

    session = startSpeechSession({
      onPartial: (text) => {
        partial = text;
        dispatch('voicePartial', { text });
      },
      onFinal: async (text) => {
        isProcessing = true;
        try {
          await submitQuery(text, replyMode ? 'user_reply' : 'user_speech');
          // Subscribe to narrations (agent's spoken responses) too. The
          // agent_state stream is owned by a parent component.
          connectTextStream('agent_responses');
          dispatch('voiceCommand', { success: true, text });
        } catch (err) {
          dispatch('voiceCommand', {
            success: false,
            error: err instanceof Error ? err.message : 'submit_query failed',
          });
        } finally {
          isProcessing = false;
          partial = '';
        }
      },
      onError: (code) => {
        // 'no-speech' is benign — the user just didn't say anything.
        if (code !== 'no-speech') {
          dispatch('voiceCommand', { success: false, error: `stt: ${code}` });
        }
        isRecording = false;
        isProcessing = false;
        partial = '';
      },
    });

    isRecording = true;
  }

  // Keyboard shortcut support
  function handleKeyPress(event: KeyboardEvent) {
    // Ctrl+M or Cmd+M to toggle recording
    if ((event.ctrlKey || event.metaKey) && event.key === 'm') {
      event.preventDefault();
      toggleRecording();
    }
  }
</script>

<svelte:window on:keydown={handleKeyPress} />

<button
  class="voice-button-fab"
  class:recording={isRecording}
  class:processing={isProcessing}
  on:click={toggleRecording}
  disabled={isProcessing}
  style="--theme-color: {$theme.primary}"
  title={isRecording ? 'Stop recording (Ctrl+M)' : 'Start voice command (Ctrl+M)'}
>
  {#if isProcessing}
    <span class="processing-icon">⟳</span>
  {:else if isRecording}
    <span class="mic-icon recording">◉</span>
  {:else}
    <span class="mic-icon">🎤</span>
  {/if}
</button>

<style>
  .voice-button-fab {
    position: fixed;
    bottom: 30px;
    right: 30px;
    width: 120px; /* Increased from 60px to 2x */
    height: 120px; /* Increased from 60px to 2x */
    border-radius: 50%;
    background: #000;
    border: 2px solid var(--theme-color);
    color: var(--theme-color);
    cursor: pointer;
    font-size: 48px; /* Increased from 24px to 2x */
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.3s ease;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
    z-index: 1000;
  }

  .voice-button-fab:hover:not(:disabled) {
    background: var(--theme-color);
    color: #000;
    box-shadow: 0 6px 20px var(--theme-color);
    transform: scale(1.1);
  }

  .voice-button-fab:active:not(:disabled) {
    transform: scale(0.95);
  }

  .voice-button-fab:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .voice-button-fab.recording {
    animation: pulse 1.5s infinite;
    border-color: #ff0000;
    color: #ff0000;
    background: rgba(255, 0, 0, 0.1);
  }

  .voice-button-fab.recording:hover {
    background: #ff0000;
    color: #000;
  }

  .voice-button-fab.processing {
    border-style: dashed;
  }

  .mic-icon {
    display: inline-block;
    transition: transform 0.2s ease;
    font-size: 56px; /* Increased from 28px to 2x */
  }

  .mic-icon.recording {
    color: #ff0000;
    animation: blink 1s infinite;
  }

  .processing-icon {
    display: inline-block;
    animation: spin 1s linear infinite;
    font-size: 56px; /* Increased from 28px to 2x */
  }

  @keyframes pulse {
    0% {
      transform: scale(1);
      box-shadow: 0 4px 12px rgba(255, 0, 0, 0.4);
    }
    50% {
      transform: scale(1.05);
      box-shadow: 0 4px 20px rgba(255, 0, 0, 0.6);
    }
    100% {
      transform: scale(1);
      box-shadow: 0 4px 12px rgba(255, 0, 0, 0.4);
    }
  }

  @keyframes blink {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
  }

  @keyframes spin {
    from { transform: rotate(0deg); }
    to { transform: rotate(360deg); }
  }

  /* Terminal-style glow effect */
  .voice-button-fab.recording::after {
    content: '';
    position: absolute;
    top: -8px;
    left: -8px;
    right: -8px;
    bottom: -8px;
    border: 2px solid rgba(255, 0, 0, 0.5);
    border-radius: 50%;
    animation: ripple 1.5s infinite;
    pointer-events: none;
  }

  @keyframes ripple {
    0% {
      transform: scale(1);
      opacity: 0.8;
    }
    100% {
      transform: scale(1.3);
      opacity: 0;
    }
  }

  /* Mobile responsive - slightly smaller on mobile */
  @media (max-width: 640px) {
    .voice-button-fab {
      width: 100px; /* Increased from 50px to 2x */
      height: 100px; /* Increased from 50px to 2x */
      bottom: 20px;
      right: 20px;
    }

    .mic-icon, .processing-icon {
      font-size: 44px; /* Increased from 22px to 2x */
    }
  }
</style>
