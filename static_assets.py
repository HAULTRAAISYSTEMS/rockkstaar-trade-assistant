"""Content versions for shared CSS/JS, safe to reuse across page navigation."""
from functools import lru_cache
from hashlib import sha256
from pathlib import Path


@lru_cache(maxsize=256)
def _digest(path: str, mtime: int, size: int) -> str:
    return sha256(Path(path).read_bytes()).hexdigest()[:20]


def install_asset_versions(app):
    @app.url_defaults
    def version_static_assets(endpoint, values):
        filename = values.get("filename", "")
        if endpoint != "static" or not filename.endswith((".css", ".js")):
            return
        root = Path(app.static_folder).resolve()
        path = (root / filename).resolve()
        if not path.is_relative_to(root):
            return
        try:
            stat = path.stat()
            values["asset"] = _digest(str(path), stat.st_mtime_ns, stat.st_size)
        except OSError:
            pass  # Missing assets retain Flask's normal 404 behavior.
