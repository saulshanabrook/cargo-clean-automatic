# cargo-clean-automatic

Weekly macOS LaunchAgent for cleaning Cargo build artifacts from every discovered top-level Cargo repository.

This is intentionally about project `target/` directories, not Cargo's global cache. Cargo's built-in `cache.auto-clean-frequency` only applies to the global Cargo home cache today; it does not garbage-collect workspace build artifacts.

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
