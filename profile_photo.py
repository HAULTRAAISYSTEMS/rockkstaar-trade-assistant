"""Profile photo: one small square picture per user, shown in the nav avatar.

The browser crops and shrinks the photo before upload; the server still
re-encodes every upload with Pillow, so whatever was sent (EXIF location,
odd formats, embedded payloads) never comes back out. Only the clean
256×256 JPEG is stored, in user_settings, so it survives Render redeploys
without a disk. Each user can only read and change their own photo.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io

from flask import Blueprint, Response, jsonify, request

import database

SIZE = 256
MAX_UPLOAD = 4 * 1024 * 1024          # raw bytes accepted before re-encoding
_KEY = "profile_photo_jpeg_b64"
_VER = "profile_photo_version"


class PhotoError(ValueError):
    pass


def clean_photo(raw: bytes) -> bytes:
    """Decode any JPEG/PNG/WebP/GIF, centre-crop to a square, return a 256px JPEG."""
    if not raw:
        raise PhotoError("Choose a photo first.")
    if len(raw) > MAX_UPLOAD:
        raise PhotoError("That photo is too large. Use one under 4 MB.")
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:  # pragma: no cover - Pillow is in requirements.txt
        raise PhotoError("Photo uploads are not available right now.") from exc
    Image.MAX_IMAGE_PIXELS = 40_000_000
    try:
        img = Image.open(io.BytesIO(raw))
        if img.format not in {"JPEG", "PNG", "WEBP", "GIF", "MPO"}:
            raise PhotoError("Use a JPEG, PNG or WebP photo.")
        img.load()
    except PhotoError:
        raise
    except Exception as exc:
        raise PhotoError("That file isn't a photo we can read.") from exc
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (12, 16, 26))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    else:
        img = img.convert("RGB")
    img = ImageOps.fit(img, (SIZE, SIZE), method=Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=86, optimize=True, progressive=True)
    return out.getvalue()


def get_photo(user_id: int) -> tuple[bytes, str] | None:
    if not user_id:
        return None
    b64 = database.get_user_setting(user_id, _KEY)
    if not b64:
        return None
    try:
        return base64.b64decode(b64), database.get_user_setting(user_id, _VER) or "1"
    except (binascii.Error, ValueError):
        return None


def photo_version(user_id: int) -> str | None:
    """Cache-busting token for the nav, or None when the user has no photo."""
    if not user_id:
        return None
    return database.get_user_setting(user_id, _VER)


def save_photo(user_id: int, jpeg: bytes) -> str:
    version = hashlib.sha256(jpeg).hexdigest()[:12]
    database.set_user_setting(user_id, _KEY, base64.b64encode(jpeg).decode("ascii"))
    database.set_user_setting(user_id, _VER, version)
    return version


def delete_photo(user_id: int) -> None:
    conn = database.get_db()
    try:
        conn.execute("DELETE FROM user_settings WHERE user_id = ? AND key IN (?, ?)", (user_id, _KEY, _VER))
        conn.commit()
    finally:
        conn.close()


def create_profile_photo_blueprint(*, current_user):
    bp = Blueprint("profile_photo", __name__)

    def uid() -> int:
        return int((current_user() or {}).get("id") or 0)

    @bp.app_context_processor
    def _inject_photo():
        try:
            v = photo_version(uid())
        except Exception:
            v = None
        return {"profile_photo_url": f"/account/photo?v={v}" if v else None}

    @bp.get("/account/photo")
    def photo():
        found = get_photo(uid())
        if not found:
            return Response(status=404)
        data, version = found
        resp = Response(data, mimetype="image/jpeg")
        resp.headers["Cache-Control"] = "private, max-age=31536000, immutable"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["ETag"] = version
        return resp

    @bp.post("/api/account/photo")
    def upload():
        user_id = uid()
        if not user_id:
            return jsonify({"ok": False, "error": "Sign in first."}), 401
        raw = b""
        f = request.files.get("photo")
        if f:
            raw = f.read(MAX_UPLOAD + 1)
        else:
            d = request.get_json(silent=True) or {}
            s = str(d.get("image") or "")
            if s.startswith("data:"):
                s = s.split(",", 1)[-1]
            try:
                raw = base64.b64decode(s, validate=True) if s else b""
            except (binascii.Error, ValueError):
                return jsonify({"ok": False, "error": "That file isn't a photo we can read."}), 400
        try:
            jpeg = clean_photo(raw)
        except PhotoError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        v = save_photo(user_id, jpeg)
        return jsonify({"ok": True, "url": f"/account/photo?v={v}"})

    @bp.delete("/api/account/photo")
    def remove():
        user_id = uid()
        if not user_id:
            return jsonify({"ok": False, "error": "Sign in first."}), 401
        delete_photo(user_id)
        return jsonify({"ok": True})

    return bp
