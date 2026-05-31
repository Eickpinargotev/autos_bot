import os
import re
import tempfile
from dataclasses import dataclass, field

import httpx

from src.core.config import settings


IMAGE_MARKER_RE = re.compile(r"(?im)(?:^|\s)imagen\s*=\s*([A-Za-z0-9_-]+)")


@dataclass
class OutboundAttachment:
    image_id: str
    path: str
    content_type: str = "image/jpeg"


@dataclass
class ParsedOutboundMessage:
    clean_text: str
    image_ids: list[str] = field(default_factory=list)


def parse_outbound_message(text: str) -> ParsedOutboundMessage:
    image_ids = IMAGE_MARKER_RE.findall(text or "")
    clean_text = IMAGE_MARKER_RE.sub("", text or "")
    clean_text = re.sub(r"[ \t]+\n", "\n", clean_text)
    clean_text = re.sub(r"\n{3,}", "\n\n", clean_text).strip()
    return ParsedOutboundMessage(clean_text=clean_text, image_ids=image_ids)


def download_drive_image(image_id: str) -> OutboundAttachment | None:
    url = settings.GOOGLE_DRIVE_IMAGE_DOWNLOAD_URL_TEMPLATE.format(image_id=image_id)
    downloaded = 0
    suffix = ".jpg"
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    temp_path = temp_file.name
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=settings.OUTBOUND_IMAGE_TIMEOUT_SECONDS) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "image/jpeg").split(";", 1)[0]
            if content_type == "image/png":
                suffix = ".png"
            elif content_type == "image/webp":
                suffix = ".webp"

            for chunk in response.iter_bytes():
                downloaded += len(chunk)
                if downloaded > settings.OUTBOUND_IMAGE_MAX_BYTES:
                    raise ValueError(f"Imagen {image_id} excede el límite de bytes")
                temp_file.write(chunk)
        temp_file.close()

        if suffix != ".jpg":
            new_path = temp_path.rsplit(".", 1)[0] + suffix
            os.replace(temp_path, new_path)
            temp_path = new_path

        return OutboundAttachment(image_id=image_id, path=temp_path, content_type=content_type)
    except Exception as e:
        print(f"Error descargando imagen {image_id}: {e}")
        try:
            temp_file.close()
        except Exception:
            pass
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        return None


def cleanup_attachment(attachment: OutboundAttachment):
    try:
        os.unlink(attachment.path)
    except FileNotFoundError:
        pass
