"""Profile photo: upload is re-encoded, private to its owner, shown in the nav."""
import io
import os
import tempfile

import pytest
from PIL import Image


@pytest.fixture()
def c(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    import database
    monkeypatch.setattr(database, "DB_PATH", path, raising=False)
    if hasattr(database, "_DB_PATH"):
        monkeypatch.setattr(database, "_DB_PATH", path)
    database.init_db()
    import web_app
    import app as legacy
    monkeypatch.setattr(legacy, "_auth_required", lambda: False)
    web_app.app.config["WTF_CSRF_ENABLED"] = False
    client = web_app.app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = 1
    yield client
    web_app.app.config["WTF_CSRF_ENABLED"] = True
    os.unlink(path)


def png(w=800, h=400, color=(200, 40, 40)):
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "PNG")
    return b.getvalue()


def test_clean_photo_crops_square_and_strips_metadata():
    import profile_photo
    b = io.BytesIO()
    im = Image.new("RGB", (900, 300), (10, 200, 10))
    exif = Image.Exif()
    exif[0x8825] = {2: (1, 2, 3)}   # GPS block
    im.save(b, "JPEG", exif=exif)
    out = Image.open(io.BytesIO(profile_photo.clean_photo(b.getvalue())))
    assert out.format == "JPEG" and out.size == (256, 256)
    assert not out.getexif().get(0x8825)
    with pytest.raises(profile_photo.PhotoError):
        profile_photo.clean_photo(b"<svg onload=alert(1)>")
    with pytest.raises(profile_photo.PhotoError):
        profile_photo.clean_photo(b"")


def test_upload_shows_in_nav_and_is_private(c):
    assert c.get("/account/photo").status_code == 404
    assert "nav-avatar__img" not in c.get("/intel").get_data(as_text=True)
    r = c.post("/api/account/photo", data={"photo": (io.BytesIO(png()), "me.png")}, content_type="multipart/form-data")
    d = r.get_json()
    assert d["ok"] and d["url"].startswith("/account/photo?v=")
    got = c.get(d["url"])
    assert got.status_code == 200 and got.mimetype == "image/jpeg" and "private" in got.headers["Cache-Control"]
    html = c.get("/intel").get_data(as_text=True)
    assert f'class="nav-avatar__img" src="{d["url"]}"' in html and "mobile-app-nav__photo" in html
    assert "Tap to change photo" in html and html.count("data-photo-pick") == 2
    # Another user never gets this photo.
    with c.session_transaction() as s:
        s["user_id"] = 2
    assert c.get("/account/photo").status_code == 404
    with c.session_transaction() as s:
        s["user_id"] = 1
    assert c.delete("/api/account/photo").get_json()["ok"]
    assert c.get("/account/photo").status_code == 404


def test_rejects_non_images_and_requires_sign_in(c):
    r = c.post("/api/account/photo", data={"photo": (io.BytesIO(b"GIF89a not really"), "x.gif")}, content_type="multipart/form-data")
    assert r.status_code == 400 and not r.get_json()["ok"]
    with c.session_transaction() as s:
        s.clear()
    import app as legacy
    assert c.post("/api/account/photo", json={"image": ""}).status_code in (401, 302)


def test_nav_offers_upload_from_the_photo_spot(c):
    html = c.get("/intel").get_data(as_text=True)
    assert html.count("data-photo-pick") == 2 and "Tap to add a photo" in html
    assert 'id="profile-photo-file"' in html and "js/profile_photo.js" in html
