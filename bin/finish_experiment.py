#!/usr/bin/env python3
"""Explicit, preview-first cleanup of a finished project's generated output."""

import argparse
from contextlib import ExitStack
import fcntl
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys


def compiled_product(path):
    """Recognize native Cargo products by file type and header, not only a name."""
    if path.is_symlink() or not path.is_file():
        return False
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        header = stream.read(8)
    native = header[:4] in {
        b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce",
        b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf", b"\x7fELF",
    }
    if path.suffix in {".rlib", ".a"}:
        return header == b"!<arch>\n"
    if path.suffix in {".dylib", ".so"}:
        return native
    return not path.suffix and native and bool(path.stat().st_mode & 0o111)


def kache_shim_links(directory):
    """Validate kache 0.28's identity-addressed pinned-executable layout."""
    if directory.is_symlink():
        raise ValueError("symlinked shim directory")
    allowed_links = {}
    installed = shutil.which("kache")
    installed_stat = os.lstat(installed) if installed else None
    for identity in directory.iterdir():
        if not re.fullmatch("[0-9a-f]{32}", identity.name) or identity.is_symlink() or not identity.is_dir():
            raise ValueError("unexpected shim identity entry")
        if {entry.name for entry in identity.iterdir()} != {"kache"}:
            raise ValueError("unexpected shim contents")
        pinned = identity / "kache"
        info = pinned.lstat()
        if stat.S_ISLNK(info.st_mode):
            # macOS hard_link can preserve Homebrew's symlink inode. Only that
            # exact installed entry is recognized; never follow arbitrary links.
            if installed_stat is None or (info.st_dev, info.st_ino) != (installed_stat.st_dev, installed_stat.st_ino):
                raise ValueError("unrecognized pinned kache symlink")
            allowed_links[pinned] = (info.st_dev, info.st_ino)
        elif not compiled_product(pinned):
            raise ValueError("pinned kache is not a native executable")
    return allowed_links



def regular_tree(root, allowed_links=None):
    """Capture identity/content metadata and reject links, mounts and special files."""
    allowed_links = allowed_links or {}
    device = root.lstat().st_dev
    snapshot = {}
    def record(path):
        info = path.lstat()
        if info.st_dev != device or info.st_uid != os.getuid():
            raise ValueError(f"foreign filesystem or owner: {path}")
        if stat.S_ISLNK(info.st_mode):
            if allowed_links.get(path) != (info.st_dev, info.st_ino):
                raise ValueError(f"symlink: {path}")
        elif not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise ValueError(f"special file: {path}")
        snapshot[path] = (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    record(root)
    def failed(error):
        raise error
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=failed):
        for name in (*dirs, *files):
            record(Path(directory) / name)
    return snapshot


def direct_path(value):
    path = Path(os.path.abspath(value))
    for ancestor in (*reversed(path.parents), path):
        if ancestor.is_symlink():
            raise ValueError(f"symlink in declared path: {ancestor}")
    if not path.is_dir() or path in {Path('/'), Path.home(), Path('/tmp'), Path('/private/tmp'), Path('/Users')}:
        raise ValueError(f"declare a specific existing project/experiment directory: {path}")
    return path


def cargo_layout(target, stack):
    entries = {entry.name for entry in target.iterdir()}
    allowed = {"debug", "release", "doc", ".rustc_info.json", ".rustdoc_fingerprint.json", "CACHEDIR.TAG"}
    if entries - allowed or ".rustc_info.json" not in entries:
        raise ValueError("nonstandard target contents or missing Cargo compiler marker")
    allowed_links = {}
    for profile in ("debug", "release"):
        directory = target / profile
        if not directory.exists():
            continue
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError(f"invalid Cargo profile directory: {directory}")
        expected = {".fingerprint", "build", "deps", "examples", "incremental"}
        for child in directory.iterdir():
            if child.name == ".kache-build-script-shims" and child.is_dir():
                allowed_links.update(kache_shim_links(child))
            elif child.is_dir():
                if child.name not in expected and not child.name.endswith(".dSYM"):
                    raise ValueError(f"nonstandard profile directory: {child}")
            elif child.name in {".cargo-lock", ".cargo-build-lock"} and child.is_file() and child.stat().st_size == 0:
                handle = stack.enter_context(child.open("rb"))
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            elif compiled_product(child):
                continue
            elif child.suffix == ".d" and any(compiled_product(directory / (child.stem + suffix)) for suffix in ("", ".rlib", ".a", ".dylib", ".so")):
                continue
            else:
                raise ValueError(f"nonstandard profile file: {child}")
    return allowed_links


def mypy_layout(cache):
    marker = cache / "CACHEDIR.TAG"
    if marker.is_symlink() or not marker.is_file():
        raise ValueError("missing regular mypy cache marker")
    with marker.open("rb") as stream:
        if not stream.read(80).startswith(b"Signature: 8a477f597d28d172789f06886806bc55"):
            raise ValueError("invalid mypy cache marker")
    for entry in cache.iterdir():
        if entry.name in {"CACHEDIR.TAG", ".gitignore"} and entry.is_file():
            continue
        if (entry.name == "missing_stubs" or re.fullmatch(r"[0-9]+\.[0-9]+", entry.name)) and entry.is_dir():
            continue
        raise ValueError(f"nonstandard mypy cache entry: {entry}")

    for directory, dirs, files in os.walk(cache, followlinks=False):
        if Path(directory) == cache:
            continue
        for name in files:
            entry = Path(directory) / name
            if entry.is_symlink() or not entry.is_file():
                raise ValueError(f"non-regular mypy cache data: {entry}")
            if name.endswith((".data.json", ".meta.json")) or name == "@plugins_snapshot.json":
                continue
            if re.fullmatch(r"cache(?:\.[0-9]+)?\.db", name):
                with entry.open("rb") as stream:
                    if stream.read(16) == b"SQLite format 3\x00":
                        continue
            raise ValueError(f"unrecognized mypy cache data: {entry}")


def inspect_activity(root, output):
    """Check open output and build/test processes scoped to the declared root."""
    opened = subprocess.run(["/usr/sbin/lsof", "-nP", "+D", str(output), "-F", "pftDi"], capture_output=True, text=True, timeout=20)
    if opened.returncode not in (0, 1) or opened.stderr.strip():
        raise OSError("cannot verify open output files")
    installed = shutil.which("kache")
    installed_info = os.stat(installed) if installed else None
    records = []
    pid = None
    entry = None
    for line in opened.stdout.splitlines():
        key, value = line[:1], line[1:]
        if key in {"p", "f"}:
            if entry is not None:
                records.append((pid, entry))
            entry = None
        if key == "p":
            pid = int(value)
            if pid <= 0:
                raise ValueError("invalid lsof PID output")
        elif key == "f":
            if pid is None:
                raise ValueError("missing lsof process record")
            entry = {"f": value}
        elif key in {"t", "D", "i"} and entry is not None:
            entry[key] = value
        else:
            raise ValueError("malformed lsof output")
    if entry is not None:
        records.append((pid, entry))
    if opened.stdout and not records:
        raise ValueError("incomplete lsof output")
    for pid, entry in records:
        if pid == os.getpid():
            continue  # Only our read-only Cargo lock handles are open here.
        # Kache pins its installed executable via hardlinks. A mapped alias is
        # not an active build; all other output handles still prevent cleanup.
        if installed_info and entry.get("f") == "txt" and entry.get("t") == "REG" and (
            int(entry.get("D", "-1"), 16), int(entry.get("i", "-1"))
        ) == (installed_info.st_dev, installed_info.st_ino):
            continue
        raise ValueError("output is open or mapped by another process")
    processes = subprocess.run(["/bin/ps", "-axo", "pid=,uid=,comm="], capture_output=True, text=True, check=True, timeout=5)
    for line in processes.stdout.splitlines():
        fields = line.split(None, 2)
        if len(fields) != 3 or fields[1] != str(os.getuid()) or fields[0] == str(os.getpid()):
            continue
        name = Path(fields[2]).name.lower()
        if name not in {"cargo", "rustc", "rustdoc", "clippy-driver", "kache", "mypy", "dmypy", "pytest"} and not name.startswith("python"):
            continue
        pid = fields[0]
        cwd = subprocess.run(["/usr/sbin/lsof", "-nP", "-a", "-p", pid, "-d", "cwd", "-Fn"], capture_output=True, text=True, timeout=5)
        if cwd.returncode not in (0, 1) or cwd.stderr.strip():
            raise OSError("cannot inspect build/test process working directory")
        for entry in cwd.stdout.splitlines():
            if entry.startswith("n") and Path(entry[1:]).is_relative_to(root):
                raise ValueError(f"build/test process {pid} uses declared project/experiment")
        command = subprocess.run(["/bin/ps", "-p", pid, "-o", "command="], capture_output=True, text=True, timeout=5)
        if command.returncode not in (0, 1) or command.stderr.strip():
            raise OSError("cannot inspect build/test process command")
        if str(root) in command.stdout:
            raise ValueError(f"build/test process {pid} references declared project/experiment")


def finish_one(root, output, kind, clean):
    # Keep Cargo locks held until deletion completes; no Cargo process/toolchain is launched.
    with ExitStack() as stack:
        direct_path(output)
        links = cargo_layout(output, stack) if kind == "cargo" else None
        if kind == "mypy":
            mypy_layout(output)
        before = regular_tree(output, links)
        inspect_activity(root, output)
        parent_fd = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, parent_fd)
        parent_info = os.fstat(parent_fd)
        if regular_tree(output, links) != before or (output.parent.stat().st_dev, output.parent.stat().st_ino) != (parent_info.st_dev, parent_info.st_ino):
            raise ValueError("output changed during inspection")
        if clean:
            if not shutil.rmtree.avoids_symlink_attacks:
                raise OSError("this Python lacks symlink-safe directory removal")
            shutil.rmtree(output.name, dir_fd=parent_fd)
        print(f"{'CLEANED' if clean else 'WOULD CLEAN'} {output}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, epilog="Completion is an explicit opt-in, not an age policy. Keep the declared project/experiment stopped for the entire command.")
    parser.add_argument("--project", required=True, help="exact project; only its direct target directory is eligible")
    parser.add_argument("--finished", action="store_true", required=True, help="confirm the declared work is finished and stopped")
    parser.add_argument("--test-cache-root", help="optional specific finished experiment tree; only validated .mypy_cache directories")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--clean", action="store_true", help="delete eligible generated output; otherwise preview")
    mode.add_argument("--dry-run", action="store_true", help="preview (default)")
    parser.add_argument("--no-popup", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        project = direct_path(args.project)
        manifest = project / "Cargo.toml"
        if not manifest.is_file() or manifest.is_symlink():
            raise ValueError("project must have a regular Cargo.toml")
        candidates = []
        target = project / "target"
        if target.exists() or target.is_symlink():
            candidates.append((project, target, "cargo"))
        if args.test_cache_root:
            cache_root = direct_path(args.test_cache_root)
            def failed(error):
                raise error
            for directory, dirs, files in os.walk(cache_root, followlinks=False, onerror=failed):
                dirs[:] = [name for name in dirs if name not in {".git", "target", "node_modules", ".venv", "venv"}]
                if ".mypy_cache" in dirs:
                    candidates.append((cache_root, Path(directory) / ".mypy_cache", "mypy"))
                    dirs.remove(".mypy_cache")
        failures = 0
        for root, output, kind in candidates:
            try:
                finish_one(root, output, kind, args.clean)
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                print(f"SKIPPED {output}: {error}", file=sys.stderr)
                failures += 1
        if not candidates:
            print("No generated target or mypy cache found in the declared paths.")
        return int(bool(failures))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"Cleanup refused: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
