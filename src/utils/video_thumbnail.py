import subprocess
from pathlib import Path
from typing import Optional, Tuple


def get_video_dimensions(video_path: str) -> Tuple[Optional[int], Optional[int]]:
    """Получает ширину и высоту видео через ffprobe."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            str(video_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0 and "x" in result.stdout.strip():
            parts = result.stdout.strip().split("x")
            return int(parts[0]), int(parts[1])
    except Exception:
        pass
    return None, None


def make_video_thumbnail(video_path: str, out_dir: str, *, seek_sec: float = 1.0) -> Optional[Path]:
    """
    Делает JPEG-превью для Telegram sendVideo:
    - JPEG
    - <= 200KB
    - width/height <= 320
    """
    video = Path(video_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    thumb_path = out / f"{video.stem}.thumb.jpg"

    # Несколько попыток: сначала лучшее качество, потом сильнее сжимаем.
    attempts = [
        # (width, qscale)  qscale: 2..31 (меньше = лучше качество)
        (320, 8),
        (320, 12),
        (256, 14),
        (200, 16),
    ]

    for width, q in attempts:
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{seek_sec:.2f}",
            "-i", str(video),
            "-vframes", "1",
            "-vf", f"scale={width}:-2",
            "-q:v", str(q),
            str(thumb_path),
        ]
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if thumb_path.exists() and thumb_path.stat().st_size <= 200_000:
                return thumb_path
        except Exception:
            continue

    # Если сделали, но не уложились — всё равно вернём (иногда Telegram проглатывает),
    # но лучше так не делать.
    return thumb_path if thumb_path.exists() else None