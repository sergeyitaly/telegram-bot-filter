"""Blur/pixelate flagged photos and videos before reposting them."""
import asyncio
import logging
import threading

from PIL import Image, ImageFilter

from bot.config import PHOTO_BLUR_RADIUS, VIDEO_BLUR_STRENGTH

log = logging.getLogger(__name__)


def blur_photo(src_path: str, dst_path: str) -> None:
    with Image.open(src_path) as img:
        img = img.convert("RGB")
        blurred = img.filter(ImageFilter.GaussianBlur(radius=PHOTO_BLUR_RADIUS))
        # exif=b"" strips ALL metadata including GPS coordinates.
        # Even after blurring the visual content, EXIF can still carry the
        # exact location where the photo was taken.
        blurred.save(dst_path, format="JPEG", quality=70, exif=b"")


async def blur_video(src_path: str, dst_path: str, timeout: int = 120) -> bool:
    """Re-encode the whole clip with a heavy box blur. Returns False on failure.

    Runs as an async subprocess (not subprocess.run) so a slow encode on
    Render's throttled free-tier CPU doesn't block the event loop — without
    this, the whole bot stops responding to every other chat for the entire
    encode duration. Downscaling to 640px wide and using -preset ultrafast
    cut that duration further; content is being blurred past recognition
    anyway, so the resolution loss costs nothing.
    """
    cmd = [
        "ffmpeg", "-y", "-i", src_path,
        "-vf", f"scale='min(640,iw)':-2,boxblur={VIDEO_BLUR_STRENGTH}:2",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
        "-c:a", "copy",
        # Strip ALL metadata including GPS track data embedded by phones.
        "-map_metadata", "-1",
        dst_path,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except FileNotFoundError as exc:
        log.warning("video blur failed: %s", exc)
        return False

    try:
        async with asyncio.timeout(timeout):
            _, stderr = await proc.communicate()
    except TimeoutError:
        proc.kill()
        await proc.wait()
        log.warning("video blur timed out after %ss", timeout)
        return False

    if proc.returncode != 0:
        log.warning("ffmpeg exited %s: %s", proc.returncode, stderr.decode(errors="ignore")[-500:])
        return False
    return True


# ── Optional voice transcription via faster-whisper ──────────────────────────
# Install: pip install faster-whisper
# Whisper "base" model is ~142 MB, downloaded on first use to ~/.cache/huggingface.
# If faster-whisper is not installed this whole section is a graceful no-op.

_whisper_model = None
_whisper_load_lock = threading.Lock()


def transcribe_voice(path: str) -> str:
    """Transcribe a voice/audio file with faster-whisper.

    Returns the transcript string, or an empty string if faster-whisper is
    not installed or transcription fails. Lazy-loads the model on first call
    (thread-safe). Optimised for CPU inference (int8 quantisation)."""
    global _whisper_model
    try:
        from faster_whisper import WhisperModel  # noqa: PLC0415
    except ImportError:
        return ""

    with _whisper_load_lock:
        if _whisper_model is None:
            try:
                _whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
                log.info("Whisper 'base' model loaded for voice transcription")
            except Exception as exc:
                log.warning("failed to load Whisper model: %s", exc)
                return ""

    try:
        segments, _ = _whisper_model.transcribe(
            path,
            language="uk",
            beam_size=1,
            vad_filter=True,   # skip silence, faster on real voice messages
        )
        return " ".join(seg.text.strip() for seg in segments).strip()
    except Exception as exc:
        log.debug("Whisper transcription failed for %s: %s", path, exc)
        return ""
