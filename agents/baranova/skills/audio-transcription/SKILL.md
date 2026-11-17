---
name: audio-transcription
description: "Use when transcribing or diarizing local audio."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [audio, transcription, asr, whisper, diarization, pyannote, ffmpeg]
    related_skills: [microsoft-office]
---

# Audio transcription & speaker diarization

## When to Use

Trigger when the user asks to transcribe an audio or video file, pull text from
speech, or split a recording by who said what. Do NOT offer to transcribe when
unrelated to the task.

Transcribe an audio/video file on this machine and, optionally, split it by
speaker. Everything below runs locally — nothing leaves the machine.

## Before you start — state limits honestly

The user wants limitations stated up front, before work begins, so time is not
wasted. Two facts to surface on every job:

- Plain Whisper transcribes the words but does NOT label who said them. Speaker
  labels require a separate diarization step (pyannote.audio).
- Getting diarization working needs a free HuggingFace account + accepted model
  license + access token (see below). Without that token, the diarization model
  will not download. Confirm the user has/is willing to make one before promising
  speaker labels.

Whisper itself is free and open-source (MIT); models are free. Only the hosted
OpenAI Whisper API is pay-per-minute — local use costs nothing.

## Steps

1. **Extract/normalize audio** with ffmpeg (installed system-wide): pull audio
   from video, convert to wav/mp3, mono-downmix if needed.
2. **Transcribe** with `faster-whisper`, not `openai-whisper`. faster-whisper is
   CTranslate2-based — same model family, far lighter install, no PyTorch
   dependency, faster on CPU. Install into a dedicated venv:
   ```
   <python-with-pip> -m venv /opt/data/venvs/whisper
   /opt/data/venvs/whisper/bin/pip install faster-whisper
   ```
   Python here has no pip module in the system interpreter or the active venv;
   the pip-carrying interpreter is under `/opt/hermes/tools/` (find it with
   `which -a pip`). First run downloads a model into `~/.cache/huggingface` —
   pick model size by speed/accuracy trade-off (`tiny`/`base` fast+rough,
   `small`/`medium`/`large` slower+more accurate, `large-v3` best). Russian and
   English are both well supported.
3. **Diarization (separate, optional)** — `pyannote.audio` is the standard:
   ```
   /opt/data/venvs/whisper/bin/pip install torch torchaudio \
       --index-url https://download.pytorch.org/whl/cpu
   /opt/data/venvs/whisper/bin/pip install pyannote.audio
   ```
   Then download the pipeline `pyannote/speaker-diarization-3.1`, which pulls
   segmentation + speaker-embedding models — these are **gated** on HuggingFace
   and refuse to download without a valid `HF_TOKEN` (free account + accepted
   license). You can run transcription and hold off on diarization until the
   user decides they want labels and provides the token.
4. **Deliver** the transcript as the user's required format (this user mostly
   wants Word `.docx` or Excel `.xlsx` — use the microsoft-office skill).

## Pitfalls

- Verify the audio actually carries speech BEFORE trusting or presenting any
  transcript: run `ffmpeg -i <file> -af volumedetect -f null -` and read
  `max_volume`. Whisper hallucinates fluent, plausible text on near-silent input
  (e.g. `max_volume` around -80 dB or below), so a transcript produced from an
  effectively silent file is fabrication, not speech — report the file as silent
  instead of quoting its "transcript". A 90 s file that yields only a few short
  segments over long gaps is a tell.
- faster-whisper's `decode_audio` calls `av.open(..., metadata_errors="ignore")`,
  an argument removed in PyAV >= 14. If transcription dies with `TypeError:
  open() got an unexpected keyword argument 'metadata_errors'`, patch
  `site-packages/faster_whisper/audio.py` to drop that one kwarg (leave the rest
  of the call intact) rather than downgrading PyAV.
- Install `torch` from `--index-url https://download.pytorch.org/whl/cpu` only —
  the default PyPI `torch` pulls multi-GB CUDA binaries you do not need on a
  CPU-only box.
- Do not claim speaker separation from a whisper-only result; whisper has no
  notion of speakers, so a "who said what" output silently collapses to a flat
  transcript.
- The venv pythons on this host lack pip; create the venv from the pip-carrying
  interpreter (find it via `which -a pip`), not from the venv's own python.
- Model files land in the HuggingFace cache and can be hundreds of MB (diarization
  + torch total several GB on disk) — check `df -h` before a large install; this
  host's disk runs near full.
