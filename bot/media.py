"""Blur/pixelate flagged photos and videos before reposting them."""
import logging
import subprocess

from PIL import Image, ImageFilter

from bot.config import PHOTO_BLUR_RADIUS, VIDEO_BLUR_STRENGTH

log = logging.getLogger(__name__)


def blur_photo(src_path: str, dst_path: str) -> None:
    with Image.open(src_path) as img:
        img = img.convert("RGB")
        blurred = img.filter(ImageFilter.GaussianBlur(radius=PHOTO_BLUR_RADIUS))
        blurred.save(dst_path, format="JPEG", quality=70)


def blur_video(src_path: str, dst_path: str, timeout: int = 120) -> bool:
    """Re-encode the whole clip with a heavy box blur. Returns False on failure."""
    cmd = [
        "ffmpeg", "-y", "-i", src_path,
        "-vf", f"boxblur={VIDEO_BLUR_STRENGTH}:2",
        "-c:a", "copy",
        dst_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        log.warning("video blur failed: %s", exc)
        return False
    if result.returncode != 0:
        log.warning("ffmpeg exited %s: %s", result.returncode, result.stderr.decode(errors="ignore")[-500:])
        return False
    return True
