#!/usr/bin/env python3
"""
Rename movies and TV episodes based on parsed filename metadata.

Uses guessit to parse video filenames and renames them to a clean format.
Optionally fetches episode names from TMDb if API key is configured in secrets.txt.

Usage:
    python rename_media.py <directory>
    python rename_media.py <directory> --dry-run

Naming format:
    Movies: Title (Year).ext
    TV Episodes: Show Name SSxEE - Episode Name.ext
"""

import os
import sys
import re
from pathlib import Path

from guessit import guessit

# Optional TMDb support for episode names
try:
    import tmdbsimple as tmdb
    TMDB_AVAILABLE = True
except ImportError:
    TMDB_AVAILABLE = False

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".m4v"}


def load_secrets() -> dict[str, str]:
    """Load secrets from secrets.txt file."""
    secrets = {}
    secrets_file = Path(__file__).parent / "secrets.txt"
    if not secrets_file.exists():
        return secrets
    with open(secrets_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                secrets[key.strip()] = value.strip()
    return secrets


def get_tmdb_api_key() -> str | None:
    """Get TMDb API key from environment or secrets file."""
    key = os.environ.get("TMDB_API_KEY")
    if key:
        return key
    secrets = load_secrets()
    return secrets.get("TMDB_API_KEY")


TMDB_API_KEY = get_tmdb_api_key()

# Caches for TMDb lookups
_show_cache: dict[str, dict] = {}
_episode_cache: dict[str, str] = {}


def sanitize_filename(name: str) -> str:
    """Remove or replace characters that are invalid in filenames."""
    name = name.replace(":", " -").replace("/", "-").replace("\\", "-")
    name = re.sub(r'[<>"|?*]', "", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip()


def title_case(name: str) -> str:
    """Convert to title case, preserving acronyms."""
    words = name.split()
    result = []
    for word in words:
        if word.isupper() and len(word) <= 4:
            result.append(word)
        else:
            result.append(word.capitalize())
    return " ".join(result)


def lookup_show(title: str) -> dict | None:
    """Look up a TV show on TMDb."""
    if not TMDB_AVAILABLE or not TMDB_API_KEY:
        return None

    cache_key = title.lower()
    if cache_key in _show_cache:
        return _show_cache[cache_key]

    try:
        search = tmdb.Search()
        search.tv(query=title)
        if search.results:
            result = search.results[0]
            show_info = {"id": result["id"], "name": result["name"]}
            _show_cache[cache_key] = show_info
            return show_info
    except Exception:
        pass
    return None


def lookup_episode_name(show_id: int, season: int, episode: int) -> str | None:
    """Look up episode name from TMDb."""
    if not TMDB_AVAILABLE or not TMDB_API_KEY:
        return None

    cache_key = f"{show_id}_{season}_{episode}"
    if cache_key in _episode_cache:
        return _episode_cache[cache_key]

    try:
        ep = tmdb.TV_Episodes(show_id, season, episode)
        info = ep.info()
        name = info.get("name", "")
        if name:
            _episode_cache[cache_key] = name
            return name
    except Exception:
        pass
    return None


def get_new_movie_name(info: dict, extension: str) -> str | None:
    """Generate new filename for a movie."""
    title = info.get("title")
    if not title:
        return None

    title = title_case(title)
    year = info.get("year")

    if year:
        new_name = f"{title} ({year})"
    else:
        new_name = title

    return sanitize_filename(new_name) + extension


def get_new_episode_name(info: dict, extension: str) -> str | None:
    """Generate new filename for a TV episode."""
    title = info.get("title")
    season = info.get("season")
    episode = info.get("episode")

    if not title or season is None or episode is None:
        return None

    # Try to get proper show name and episode title from TMDb
    show = lookup_show(title)
    if show:
        show_name = show["name"]
        # Handle single episode
        if isinstance(episode, int):
            ep_title = lookup_episode_name(show["id"], season, episode)
        else:
            ep_title = None  # Multi-episode, skip title lookup
    else:
        show_name = title_case(title)
        ep_title = None

    # Fall back to episode title from filename if TMDb didn't provide one
    if not ep_title:
        ep_title = info.get("episode_title")
        if ep_title:
            ep_title = title_case(ep_title)

    # Build episode string: SSxEE or SSxEEEE for multi-episode
    if isinstance(episode, list):
        ep_str = "".join(f"E{e:02d}" for e in episode)
        season_ep = f"{season:02d}x{ep_str}"
    else:
        season_ep = f"{season:02d}x{episode:02d}"

    if ep_title:
        new_name = f"{show_name} {season_ep} - {ep_title}"
    else:
        new_name = f"{show_name} {season_ep}"

    return sanitize_filename(new_name) + extension


def process_file(file_path: Path, dry_run: bool = False) -> bool:
    """Process a single video file and rename it if needed."""
    info = guessit(file_path.name)
    media_type = info.get("type")
    extension = file_path.suffix.lower()

    if media_type == "movie":
        new_name = get_new_movie_name(info, extension)
    elif media_type == "episode":
        new_name = get_new_episode_name(info, extension)
    else:
        print(f"  ? {file_path.name} (unknown type)")
        return False

    if not new_name:
        print(f"  ? {file_path.name} (insufficient info)")
        return False

    if file_path.name == new_name:
        print(f"  = {file_path.name}")
        return False

    new_path = file_path.parent / new_name

    if new_path.exists() and new_path != file_path:
        print(f"  ! {file_path.name} (target exists)")
        return False

    if dry_run:
        print(f"  - {file_path.name}")
        print(f"  + {new_name}")
    else:
        try:
            file_path.rename(new_path)
            print(f"  - {file_path.name}")
            print(f"  + {new_name}")
        except OSError as e:
            print(f"  ! {file_path.name} ({e})")
            return False

    return True


def find_video_files(directory: Path) -> list[Path]:
    """Recursively find all video files in directory."""
    video_files = []
    for root, _, files in os.walk(directory):
        for filename in files:
            file_path = Path(root) / filename
            if file_path.suffix.lower() in VIDEO_EXTENSIONS:
                video_files.append(file_path)
    return sorted(video_files)


def main(target_directory: str, dry_run: bool = False) -> int:
    """Main function to process all video files."""
    target_path = Path(target_directory).resolve()

    if not target_path.exists():
        print(f"Error: Directory does not exist: {target_path}")
        return 1

    if not target_path.is_dir():
        print(f"Error: Not a directory: {target_path}")
        return 1

    # Setup TMDb if available
    if TMDB_AVAILABLE and TMDB_API_KEY:
        tmdb.API_KEY = TMDB_API_KEY
        print("TMDb: enabled (will fetch episode names)")
    else:
        if not TMDB_AVAILABLE:
            print("TMDb: not installed (pip install tmdbsimple)")
        else:
            print("TMDb: no API key (set TMDB_API_KEY for episode names)")

    video_files = find_video_files(target_path)

    if not video_files:
        print(f"No video files found in: {target_path}")
        return 0

    print(f"Found {len(video_files)} video file(s)")
    if dry_run:
        print("DRY RUN - no files will be renamed")
    print()

    current_dir = None
    renamed_count = 0

    for video_path in video_files:
        rel_dir = video_path.parent.relative_to(target_path)
        if rel_dir != current_dir:
            current_dir = rel_dir
            dir_label = str(rel_dir) if str(rel_dir) != "." else "(root)"
            print(f"[{dir_label}]")

        if process_file(video_path, dry_run):
            renamed_count += 1

    print(f"\n{'=' * 50}")
    action = "Would rename" if dry_run else "Renamed"
    print(f"{action} {renamed_count}/{len(video_files)} files")

    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <directory> [--dry-run]")
        print()
        print("Rename movies and TV episodes to a clean format.")
        print()
        print("Options:")
        print("  --dry-run    Preview changes without renaming")
        print()
        print("Output format:")
        print("  Movies:  Title (Year).ext")
        print("  TV:      Show Name SSxEE - Episode Name.ext")
        print()
        print("Episode names:")
        print("  Add TMDB_API_KEY to secrets.txt to fetch episode names.")
        print("  Get a free key at: https://www.themoviedb.org/settings/api")
        sys.exit(1)

    directory = sys.argv[1]
    dry_run = "--dry-run" in sys.argv

    sys.exit(main(directory, dry_run))
