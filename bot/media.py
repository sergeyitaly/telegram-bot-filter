"""Blur/pixelate flagged photos and videos before reposting them."""
import asyncio
import logging

from PIL import Image, ImageFilter

from bot.config import PHOTO_BLUR_RADIUS, VIDEO_BLUR_STRENGTH

log = logging.getLogger(__name__)


def blur_photo(src_path: str, dst_path: str) -> None:
    with Image.open(src_path) as img:
        img = img.convert("RGB")
        blurred = img.filter(ImageFilter.GaussianBlur(radius=PHOTO_BLUR_RADIUS))
        blurred.save(dst_path, format="JPEG", quality=70)


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
