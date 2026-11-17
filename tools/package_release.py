"""Create a source-only ZIP from an allowlist, with an integrity manifest."""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOP_FILES = ("README.md", "requirements.txt", "requirements-dev.txt", "requirements.lock", "Dockerfile", ".dockerignore", ".gitignore", ".gitattributes", ".env.example")
TOP_DIRS = ("backend", "data", "frontend", "tests", "tools", "evals", "docs", "deploy", ".github")
SUFFIXES = {".py", ".md", ".json", ".js", ".mjs", ".css", ".html", ".svg", ".yaml", ".yml", ".txt"}
BLOCKED_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", "secrets", "var", "reports", "releases"}
REQUIRED = {"backend/main.py", "backend/server.py", "data/catalogue.json", "frontend/dist/index.html", "requirements.lock", "Dockerfile"}
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b(?:sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b"),
)
KEY_ASSIGNMENT = re.compile(r"^[ \t]*(?:export[ \t]+)?GUARDCART_AI_API_KEY[ \t]*=[ \t]*([^\r\n]*)", re.MULTILINE)


def populated_key_assignment(text):
    for match in KEY_ASSIGNMENT.finditer(text):
        value = match.group(1).strip()
        if not value or value.startswith("#"):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1].strip()
        if value:
            return True
    return False


def linked(path):
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def collect_sources(root):
    root = root.resolve(strict=True)
    paths = [root / name for name in TOP_FILES if (root / name).is_file()]
    for name in TOP_DIRS:
        directory = root / name
        if linked(directory):
            raise ValueError("Linked source directories are not allowed.")
        if not directory.is_dir():
            continue
        for parent, directories, files in os.walk(directory, followlinks=False):
            base = Path(parent)
            for child in directories:
                if linked(base / child):
                    raise ValueError("Linked source directories are not allowed.")
            directories[:] = [child for child in directories if child not in BLOCKED_DIRS]
            for filename in files:
                path = base / filename
                if filename.startswith(".env") or path.suffix not in SUFFIXES:
                    continue
                # Only a reviewed template is allowed from deploy/.
                if path.suffix == ".env":
                    continue
                paths.append(path)
    production_template = root / "deploy" / "production.env.example"
    if production_template.is_file():
        paths.append(production_template)
    contents = {}
    for path in sorted(set(paths)):
        relative = path.relative_to(root).as_posix()
        if linked(path) or not path.resolve(strict=True).is_relative_to(root):
            raise ValueError("Source links or paths outside the project are not allowed.")
        if path.stat().st_size > 2_000_000:
            raise ValueError("Source file exceeds the packaging size limit: " + relative)
        data = path.read_bytes()
        try:
            text = data.decode("utf-8-sig")
        except UnicodeError:
            raise ValueError("Only reviewed UTF-8 source files may be packaged: " + relative) from None
        if populated_key_assignment(text) or any(pattern.search(text) for pattern in SECRET_PATTERNS):
            raise ValueError("Potential credential detected; inspect this file locally: " + relative)
        contents[relative] = data
    if not REQUIRED <= contents.keys():
        raise ValueError("Required runtime files are missing from the source package.")
    return contents


def manifest_for(contents):
    files = [{"path": path, "bytes": len(data), "sha256": sha256(data).hexdigest()} for path, data in sorted(contents.items())]
    digest = sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"format_version": 1, "source_sha256": digest, "files": files,
            "scope": "Source only; no credentials, session/database data, caches or installed dependencies."}


def package(root=ROOT, *, check_only=False):
    root = root.resolve(strict=True)
    contents = collect_sources(root)
    manifest = manifest_for(contents)
    summary = {"source_files": len(contents), "source_sha256": manifest["source_sha256"], "provider_calls": 0}
    if check_only:
        return {**summary, "mode": "check_only"}
    directory = root / "releases"
    if linked(directory):
        raise ValueError("Release output must stay inside the project.")
    directory.mkdir(exist_ok=True)
    destination = directory / ("guardcart-v2-source-" + manifest["source_sha256"][:16] + ".zip")
    with tempfile.NamedTemporaryFile(dir=directory, suffix=".tmp", delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            entries = {**contents, "release-manifest.json": (json.dumps(manifest, indent=2) + "\n").encode()}
            for relative, data in sorted(entries.items()):
                info = zipfile.ZipInfo("guardcart-v2/" + relative, date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, data)
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)
    return {**summary, "mode": "packaged", "archive": str(destination), "archive_sha256": sha256(destination.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Inspect allowlisted sources without creating an archive.")
    args = parser.parse_args()
    try:
        print(json.dumps(package(check_only=args.check)))
    except ValueError as exc:
        # These are fixed messages and source paths, never matched credential values.
        print(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
