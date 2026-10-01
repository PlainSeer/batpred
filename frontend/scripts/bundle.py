"""Create a reproducible frontend.zip from the Vite production output."""

import zipfile
from pathlib import Path


def build_bundle(dist, output):
    """Archive sorted build files with fixed timestamps and permissions."""
    if not (dist / "index.html").is_file():
        raise FileNotFoundError("Build the frontend before creating its bundle")
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(dist.rglob("*")):
            if not path.is_file():
                continue
            info = zipfile.ZipInfo("dist/" + path.relative_to(dist).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes(), compresslevel=9)


if __name__ == "__main__":
    frontend = Path(__file__).resolve().parents[1]
    build_bundle(frontend / "dist", frontend.parent / "apps" / "predbat" / "frontend.zip")
