# Gavin Talking Head Workflow Design

Date: 2026-07-06
Project: ComfyUI
GavLife card: CODE-205

## Goal

Create a first realistic YouTube-style talking-head workflow using:

- A 24.195 second Vish/Fish S2 Pro audio clip:
  `/home/gavman/Downloads/fish_s2_pro_recorded_ref_00009.flac`
- A Gavin-like realistic male presenter image based on the provided portrait reference.
- A landscape 16:9 video output suitable for YouTube.

The first pass should validate likeness, lip-sync, facial stability, and basic presenter credibility before investing in higher resolution or more complex motion.

## Selected Approach

Use the existing LTX 2.3 talking portrait workflow as the primary path.

This is the preferred first pass because the repo already contains LTX 2.3 talking portrait workflow files under:

- `basedir/user/default/workflows/video/ltx2_3_talking_portrait_ia2v.json`
- `basedir/user/default/workflows/video/ltx2_3_talking_portrait_lipsync_retry.json`
- `basedir/user/default/workflows/video/dacrikka_ltx2_3_lipsync_code205.json`
- `basedir/user/default/workflows/video/dacrikka_ltx2_3_lipsync_exact.json`

If the LTX path gives poor mouth timing, the fallback is to keep the same source image and try a more dedicated lip-sync pass.

## Source Image Requirements

The presenter source image should be a clean Gavin-like waist-up or mid-torso image:

- Adult male presenter resembling Gavin from the provided portrait reference.
- Landscape 16:9 composition for YouTube.
- Professional but approachable clothing: light blazer and pale shirt.
- Camera-facing pose with clear eyes and relaxed mouth.
- Simple bright indoor background.
- No hands in frame for the first test.
- Stable, uncluttered background to reduce video wobble.

The first render should prioritize identity stability and lip-sync over expressive body movement.

## Audio Preparation

The input audio has been verified with `ffprobe`:

- Format: FLAC
- Duration: 24.195193 seconds
- Sample rate: 44.1 kHz
- Channels: mono
- Sample format: 16-bit

Before running the video workflow:

1. Copy the FLAC into `basedir/input/`.
2. Convert to WAV only if the selected workflow node requires WAV or behaves more reliably with WAV.
3. Preserve the original FLAC as the source of truth.

## Runtime Coordination

Take down the Fish S2 Pro container before running the talking-head workflow.

Reason: the Fish container is only needed to generate the audio, which is already complete. Stopping it should free GPU/VRAM and avoid resource contention while LTX/video generation runs.

The exact command should be confirmed against the active compose setup before execution, but likely options are:

- Stop only the Fish S2 Pro service if it is in the same compose project.
- Stop the Fish-specific compose stack if it was launched separately.

Do not delete generated audio or model files when stopping the container.

## Workflow Run

First run settings should stay conservative:

- Output aspect ratio: 16:9 landscape.
- Initial resolution: 1280x720 or closest stable workflow-native size.
- Duration: match the 24.195 second audio clip.
- Motion: subtle head, face, and shoulder motion.
- Export: video with the provided audio attached.

The workflow should produce a short validation render, not a final polished production export.

## Review Criteria

Review the first output against four criteria:

1. Likeness: presenter is recognizably Gavin-like without uncanny distortion.
2. Lip-sync: mouth timing follows the Vish/Fish S2 Pro audio closely.
3. Stability: face, teeth, eyes, blazer, and background do not drift or flicker badly.
4. YouTube fit: framing feels like a credible presenter shot in landscape format.

## Fallback Path

If the first output fails:

- If likeness is weak, regenerate or improve the still source image before changing the video engine.
- If lip-sync is weak but the source image is good, try a dedicated lip-sync workflow with the same source image and audio.
- If the whole frame wobbles, simplify the background and reduce motion before increasing resolution.

## Out Of Scope For First Pass

- Full 1080p or 4K final export.
- Hand gestures.
- Multiple camera angles.
- Captions, lower thirds, or branding.
- Automated batch generation.
- Direct upload or publication to YouTube.

## Next Implementation Step

Create an implementation plan that covers:

1. Identifying the exact Fish container/compose command to stop.
2. Copying and optionally converting the audio into `basedir/input/`.
3. Creating the Gavin-like 16:9 source image.
4. Updating or cloning the existing LTX talking portrait workflow.
5. Running the first 24 second render.
6. Capturing review notes and deciding whether to iterate the image or swap lip-sync workflow.
