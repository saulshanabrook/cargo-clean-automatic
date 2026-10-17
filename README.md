# cargo-clean-automatic

Weekly macOS LaunchAgent for cleaning Cargo build artifacts from every discovered top-level Cargo repository.

This is intentionally about project `target/` directories, not Cargo's global cache. Cargo's built-in `cache.auto-clean-frequency` only applies to the global Cargo home cache today; it does not garbage-collect workspace build artifacts.

## Finish one experiment

For a project that opts into cleanup when its work is finished, run:

```sh
cargo-clean-automatic --project . --finished --clean
```

Use the executable from this checkout (`/path/to/cargo-clean-automatic/bin/cargo-clean-automatic`), or put its `bin` directory on `PATH`. Python 3.11 or newer and macOS `lsof` are required for this mode. Omit `--clean` to preview. This command does not install or change the weekly LaunchAgent.

`--finished` means you own the declared work and will keep its builds, tests and output programs stopped until cleanup returns. It immediately removes only a validated direct `target/`, regardless of its age. It preserves the project, dirty source, nested projects and other output paths. Shared or custom Cargo target/build directories are outside this mode; leave those to their owners. Save any selected evidence elsewhere before finishing; nonstandard target layouts are refused rather than guessed at.

A project can expose the same command as a small Make recipe, with the cleaner available on `PATH`:

```make
.PHONY: clean-experiment
clean-experiment:
	cargo-clean-automatic --project . --finished --clean
```

If a finished experiment also produced mypy caches in a separate scratch tree, use the same command with that **specific** tree:

```sh
cargo-clean-automatic --project . --finished --clean --test-cache-root "$experiment_dir"
```

Only `.mypy_cache` directories with a valid cache marker and recognized JSON/SQLite cache contents are eligible. Source, stubs, logs, selected evidence, wheels and prepared environments remain. Discovery does not enter `.git`, `target`, `node_modules`, `.venv`, `venv` or symlinked directories. Never pass a shared scratch collection; the supplied experiment must belong to the finished work. No registry, manifest, routine preflight or schedule is required.

The command checks open/mapped output, relevant build/test processes, unchanged metadata and nonblocking Cargo locks. A build in an unrelated project does not prevent cleanup. A mapped Kache executable alias is allowed only when it has the exact device/inode of the installed executable; other open output still prevents cleanup. Links and uncertain contents are refused; every refusal or inspection failure returns a nonzero status, including partial success. Cargo locks stay held during deletion, but their files are removed with the target: **these checks cannot prevent a new build from starting during cleanup**. The owner-stopped precondition is necessary. No Cargo invocation or toolchain download occurs.

Validate this mode without real builds or real project cleanup:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

The sections below describe the separate legacy scheduled runner; its behavior is unchanged by finished mode.

## What It Cleans

The runner scans one or more roots and finds directories shaped like this:

```text
some-repo/
  Cargo.toml
  target/
```

For each match, it runs:

```sh
cargo clean --manifest-path some-repo/Cargo.toml
```

There is no recency filter. If a top-level Cargo repo has a direct `target/` directory, it is eligible for cleanup.

To avoid wasting time and permissions prompts in unrelated places, discovery skips common non-project trees such as `~/Library`, `~/.cargo/registry`, `~/.rustup`, `~/.cache`, `~/go/pkg/mod`, iCloud/CloudStorage, and Trash.

## Install

From this repo:

```sh
./install.sh
```

By default this installs:

```text
~/Library/LaunchAgents/com.saulshanabrook.cargo-clean-automatic.plist
```

The default schedule is weekly on Sunday at 10:00 local time, scanning `$HOME`.

You can customize the install:

```sh
CARGO_CLEAN_ROOT="$HOME/p" CARGO_CLEAN_WEEKDAY=1 CARGO_CLEAN_HOUR=10 ./install.sh
```

`Weekday` follows launchd's convention: Sunday is `1`, Monday is `2`, and so on.

## Test

Preview what would be cleaned:

```sh
bin/cargo-clean-automatic --dry-run --no-popup
```

Preview only a smaller root:

```sh
bin/cargo-clean-automatic --root "$HOME/p" --dry-run --no-popup
```

Run once now through launchd:

```sh
launchctl kickstart -k "gui/$(id -u)/com.saulshanabrook.cargo-clean-automatic"
```

## Popup And Logs

Each run writes a timestamped log to:

```text
~/Library/Logs/cargo-clean-automatic/
```

At the end of a run, the script shows a macOS notification and a short popup dialog with:

- number of target directories cleaned
- estimated space freed
- failure count
- log path

Disable UI for a manual run:

```sh
bin/cargo-clean-automatic --no-popup
```

## Uninstall

```sh
./uninstall.sh
```

This unloads and removes the LaunchAgent plist. It does not delete logs.
