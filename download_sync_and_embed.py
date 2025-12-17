#!/usr/bin/env python3
"""
Download, synchronize, and embed subtitles for video files.

For each video file in a directory:
1. Check for existing embedded subtitles
2. Download missing Spanish/English subtitles using subliminal (library API)
3. Synchronize downloaded subtitles using ffsubsync
4. Create backup copies of subtitle files
5. Move original video to Originals/ subfolder
6. Embed subtitles into video file in the original location

Configure your provider credentials in secrets.txt.
"""

import os
import subprocess
import sys
import json
import shutil
from pathlib import Path

import chardet
from babelfish import Language
from subliminal import download_best_subtitles, save_subtitles, scan_video, region

# Target languages: ISO 639-3 codes for internal use, mapped to babelfish Language objects
TARGET_LANGUAGES = {
    "spa": Language("spa"),  # Spanish
    "eng": Language("eng"),  # English
}

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm"}


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


def build_provider_configs() -> dict[str, dict]:
    """Build provider configs from secrets file."""
    secrets = load_secrets()
    configs = {}

    # OpenSubtitles
    if secrets.get("OPENSUBTITLES_USERNAME") and secrets.get("OPENSUBTITLES_PASSWORD"):
        configs["opensubtitles"] = {
            "username": secrets["OPENSUBTITLES_USERNAME"],
            "password": secrets["OPENSUBTITLES_PASSWORD"],
        }

    # Addic7ed
    if secrets.get("ADDIC7ED_USERNAME") and secrets.get("ADDIC7ED_PASSWORD"):
        configs["addic7ed"] = {
            "username": secrets["ADDIC7ED_USERNAME"],
            "password": secrets["ADDIC7ED_PASSWORD"],
        }

    return configs


PROVIDER_CONFIGS = build_provider_configs()


def setup_cache() -> None:
    """Configure subliminal cache for faster repeated runs."""
    cache_file = Path.home() / ".cache" / "subliminal" / "cache.dbm"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    region.configure(
        "dogpile.cache.dbm",
        arguments={"filename": str(cache_file)},
        replace_existing_backend=True,
    )


def detect_encoding(file_path: str) -> str | None:
    """Detect the encoding of a file."""
    with open(file_path, "rb") as f:
        raw_data = f.read()
        result = chardet.detect(raw_data)
        return result.get("encoding")


def convert_to_utf8(subtitle_path: str, fallback_encoding: str = "ISO-8859-1") -> bool:
    """Convert subtitle file to UTF-8 encoding."""
    try:
        detected_encoding = detect_encoding(subtitle_path)
        print(f"  Detected encoding: {detected_encoding or 'unknown'}")

        # Handle BOM suffixes
        if detected_encoding and detected_encoding.lower().endswith("-sig"):
            detected_encoding = detected_encoding[:-4]

        encoding_to_use = detected_encoding or fallback_encoding
        if not detected_encoding:
            print(f"  Using fallback encoding: {fallback_encoding}")

        temp_file = subtitle_path + ".utf8"
        subprocess.run(
            ["iconv", "-f", encoding_to_use, "-t", "UTF-8", subtitle_path, "-o", temp_file],
            check=True,
            capture_output=True,
        )
        os.replace(temp_file, subtitle_path)
        print(f"  Converted to UTF-8")
        return True
    except subprocess.CalledProcessError as e:
        print(f"  Error converting to UTF-8: {e}")
        return False
    except Exception as e:
        print(f"  Failed to detect or convert encoding: {e}")
        return False


def get_embedded_subtitles(video_path: str) -> list[dict]:
    """Return list of embedded subtitle info in the video."""
    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-print_format", "json",
            "-show_streams",
            "-select_streams", "s",
            video_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        return [
            {
                "index": stream.get("index"),
                "stream_index": i,
                "language": stream.get("tags", {}).get("language", "und"),
            }
            for i, stream in enumerate(data.get("streams", []))
        ]
    except subprocess.CalledProcessError:
        return []


def get_embedded_languages(video_path: str) -> set[str]:
    """Return set of embedded subtitle language codes (ISO 639-3)."""
    embedded = get_embedded_subtitles(video_path)
    return {info["language"].lower() for info in embedded}


def is_video_file(path: Path) -> bool:
    """Check if a path is a video file."""
    return path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS


def get_subtitle_path(video_path: Path, lang_code: str) -> Path:
    """Get the expected subtitle path for a video and language."""
    return video_path.with_suffix(f".{lang_code}.srt")


def download_subtitles_for_video(video_path: Path, languages: set[str]) -> dict[str, Path]:
    """
    Download subtitles for a single video file using subliminal library.

    Args:
        video_path: Path to the video file
        languages: Set of ISO 639-3 language codes to download

    Returns:
        Dictionary mapping language codes to downloaded subtitle paths
    """
    if not languages:
        return {}

    print(f"  Downloading subtitles for: {', '.join(sorted(languages))}")

    try:
        # Scan the video to get metadata for better matching
        video = scan_video(str(video_path))
    except Exception as e:
        print(f"  Error scanning video: {e}")
        return {}

    # Use configured providers if any, otherwise all available
    providers = list(PROVIDER_CONFIGS.keys()) if PROVIDER_CONFIGS else None

    downloaded = {}

    # Download each language separately to avoid rate limiting issues
    for lang_code in sorted(languages):
        lang_obj = {Language(lang_code)}

        try:
            subtitles = download_best_subtitles(
                [video],
                lang_obj,
                providers=providers,
                provider_configs=PROVIDER_CONFIGS if PROVIDER_CONFIGS else None,
            )
        except Exception as e:
            print(f"  Error downloading {lang_code}: {e}")
            continue

        video_subs = subtitles.get(video, [])
        if not video_subs:
            print(f"  Not found: {lang_code}")
            continue

        subtitle = video_subs[0]
        save_subtitles(video, [subtitle], directory=str(video_path.parent))

        # subliminal saves as video.lang.srt where lang is alpha2 or alpha3 depending on config
        # We need to find the actual saved file and rename if needed
        possible_paths = [
            video_path.with_suffix(f".{subtitle.language.alpha2}.srt"),
            video_path.with_suffix(f".{subtitle.language.alpha3}.srt"),
        ]

        for possible_path in possible_paths:
            if possible_path.exists():
                target_path = get_subtitle_path(video_path, lang_code)
                if possible_path != target_path:
                    shutil.move(str(possible_path), str(target_path))
                downloaded[lang_code] = target_path
                print(f"  Downloaded: {lang_code}")
                break
        else:
            print(f"  Warning: Could not locate saved subtitle file for {lang_code}")

    return downloaded


def synchronize_subtitle(video_path: Path, subtitle_path: Path) -> bool:
    """
    Synchronize a subtitle file to match the video using ffsubsync.

    Returns True if synchronization was successful.
    """
    if not subtitle_path.exists():
        return False

    print(f"  Synchronizing: {subtitle_path.name}")

    # Convert to UTF-8 first (ffsubsync works better with UTF-8)
    convert_to_utf8(str(subtitle_path))

    synced_path = subtitle_path.with_suffix(".synced.srt")

    try:
        result = subprocess.run(
            ["ffsubsync", str(video_path), "-i", str(subtitle_path), "-o", str(synced_path)],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0 and synced_path.exists():
            # Replace original with synced version
            shutil.move(str(synced_path), str(subtitle_path))
            print(f"  Synchronized successfully")
            return True
        else:
            print(f"  Synchronization failed: {result.stderr[:200] if result.stderr else 'unknown error'}")
            # Clean up failed sync file
            if synced_path.exists():
                synced_path.unlink()
            return False
    except FileNotFoundError:
        print(f"  Error: ffsubsync not found. Please install it: pip install ffsubsync")
        return False
    except Exception as e:
        print(f"  Synchronization error: {e}")
        if synced_path.exists():
            synced_path.unlink()
        return False


def create_subtitle_backup(subtitle_path: Path, backup_dir: Path) -> Path | None:
    """Create a backup copy of a subtitle file."""
    if not subtitle_path.exists():
        return None

    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / subtitle_path.name

    shutil.copy2(str(subtitle_path), str(backup_path))
    return backup_path


def embed_subtitles_into_video(
    video_path: Path,
    output_path: Path,
    subtitle_files: dict[str, Path],
) -> bool:
    """
    Embed subtitle files into a video, ensuring Spanish subtitles are first.

    Args:
        video_path: Source video file
        output_path: Output video file path
        subtitle_files: Dictionary mapping language codes to subtitle file paths

    Returns:
        True if embedding was successful
    """
    # Get existing subtitle streams
    existing_subs = get_embedded_subtitles(str(video_path))

    # Check if Spanish is already first among existing subs
    spanish_is_first = (
        existing_subs
        and existing_subs[0]["language"].lower() == "spa"
    )

    if not subtitle_files and not existing_subs:
        # No subtitles at all, just copy
        shutil.copy2(str(video_path), str(output_path))
        return True

    if not subtitle_files and spanish_is_first:
        # No new subs and Spanish is already first, just copy
        shutil.copy2(str(video_path), str(output_path))
        return True

    # Determine subtitle codec for new subs based on container
    suffix = video_path.suffix.lower()
    if suffix == ".mp4":
        new_sub_codec = "mov_text"
    elif suffix == ".mkv":
        new_sub_codec = "srt"
    else:
        new_sub_codec = "srt"  # Default to srt for other containers

    # Build ffmpeg command
    cmd = ["ffmpeg", "-y", "-i", str(video_path)]

    # Add subtitle file inputs
    sub_inputs = []
    for lang_code, sub_path in subtitle_files.items():
        if sub_path.exists():
            cmd.extend(["-i", str(sub_path)])
            sub_inputs.append((lang_code, sub_path))

    # Map video and audio streams from original
    cmd.extend(["-map", "0:v", "-map", "0:a?"])

    # Build ordered list of subtitle sources: (input_index, stream_index, lang_code, is_new)
    # We'll order them: Spanish first, then others
    all_subs = []

    # Add existing embedded subs (input 0)
    for sub_info in existing_subs:
        lang = sub_info["language"].lower()
        # Skip if we're adding a new version of this language
        if lang not in subtitle_files:
            all_subs.append((0, sub_info["stream_index"], lang, False))

    # Add new subtitle inputs
    for i, (lang_code, _) in enumerate(sub_inputs):
        all_subs.append((i + 1, 0, lang_code, True))

    # Sort: Spanish first, then by original order
    def sort_key(item):
        input_idx, stream_idx, lang, is_new = item
        if lang == "spa":
            return (0, input_idx, stream_idx)  # Spanish first
        else:
            return (1, input_idx, stream_idx)  # Others maintain relative order

    all_subs.sort(key=sort_key)

    if not all_subs:
        shutil.copy2(str(video_path), str(output_path))
        return True

    # Map subtitle streams in the desired order
    for input_idx, stream_idx, lang, is_new in all_subs:
        if input_idx == 0:
            # Existing embedded subtitle
            cmd.extend(["-map", f"0:s:{stream_idx}"])
        else:
            # New external subtitle file
            cmd.extend(["-map", f"{input_idx}:0"])

    # Copy video and audio streams (no re-encoding)
    cmd.extend(["-c:v", "copy", "-c:a", "copy"])

    # Set codec and metadata for each subtitle stream in output order
    for out_idx, (input_idx, stream_idx, lang, is_new) in enumerate(all_subs):
        if is_new:
            # New subtitle: encode with appropriate codec
            cmd.extend([f"-c:s:{out_idx}", new_sub_codec])
        else:
            # Existing subtitle: copy as-is (might be bitmap format like PGS)
            cmd.extend([f"-c:s:{out_idx}", "copy"])
        # Set language metadata for all streams
        cmd.extend([f"-metadata:s:s:{out_idx}", f"language={lang}"])

    cmd.append(str(output_path))

    if sub_inputs:
        print(f"  Embedding subtitles: {', '.join(lang for lang, _ in sub_inputs)}")
    else:
        print(f"  Reordering subtitle tracks (Spanish first)")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"  Completed successfully")
            return True
        else:
            print(f"  Failed: {result.stderr[:300] if result.stderr else 'unknown error'}")
            return False
    except Exception as e:
        print(f"  Error: {e}")
        return False


def process_video(video_path: Path, output_dir: Path, backup_dir: Path) -> tuple[bool, bool]:
    """
    Process a single video file: download, sync, and embed missing subtitles.

    Returns:
        Tuple of (success, has_spanish) where:
        - success: True if processing was successful
        - has_spanish: True if the output file has Spanish subtitles
    """
    print(f"\nProcessing: {video_path.name}")

    # Get embedded subtitle languages
    embedded_langs = get_embedded_languages(str(video_path))
    print(f"  Embedded subtitles: {embedded_langs if embedded_langs else 'none'}")

    # Determine which languages are missing
    target_lang_codes = set(TARGET_LANGUAGES.keys())
    missing_langs = target_lang_codes - embedded_langs

    # Check for existing external subtitle files
    existing_external = {}
    for lang_code in missing_langs.copy():
        sub_path = get_subtitle_path(video_path, lang_code)
        if sub_path.exists():
            existing_external[lang_code] = sub_path
            missing_langs.discard(lang_code)
            print(f"  Found existing external subtitle: {sub_path.name}")

    # Download missing subtitles
    downloaded = {}
    if missing_langs:
        downloaded = download_subtitles_for_video(video_path, missing_langs)

    # Combine all external subtitles (existing + downloaded)
    all_external_subs = {**existing_external, **downloaded}

    if not all_external_subs:
        if not missing_langs:
            print(f"  All target languages already embedded")
        else:
            print(f"  Could not find subtitles for: {', '.join(missing_langs)}")

        # Check if we have Spanish (embedded or external)
        has_spanish = "spa" in embedded_langs
        if not has_spanish:
            print(f"  *** WARNING: NO SPANISH SUBTITLES AVAILABLE ***")

        # Still process video to ensure Spanish is first subtitle track
        output_path = output_dir / video_path.name
        if not output_path.exists():
            success = embed_subtitles_into_video(video_path, output_path, {})
            return success, has_spanish
        return True, has_spanish

    # Synchronize downloaded subtitles (not existing external ones, as they might already be synced)
    for lang_code, sub_path in downloaded.items():
        synchronize_subtitle(video_path, sub_path)

    # Create backups of subtitle files
    for lang_code, sub_path in all_external_subs.items():
        backup_path = create_subtitle_backup(sub_path, backup_dir)
        if backup_path:
            print(f"  Backed up: {sub_path.name} -> {backup_path}")

    # Embed subtitles into video
    output_path = output_dir / video_path.name
    success = embed_subtitles_into_video(video_path, output_path, all_external_subs)

    # Check if we have Spanish (embedded or in external subs being added)
    has_spanish = "spa" in embedded_langs or "spa" in all_external_subs
    if not has_spanish:
        print(f"  *** WARNING: NO SPANISH SUBTITLES AVAILABLE ***")

    return success, has_spanish


def main(target_directory: str) -> int:
    """
    Main function to process all video files in the target directory.

    Returns exit code (0 for success, 1 for errors).
    """
    target_path = Path(target_directory).resolve()

    if not target_path.exists():
        print(f"Error: Directory does not exist: {target_path}")
        return 1

    if not target_path.is_dir():
        print(f"Error: Not a directory: {target_path}")
        return 1

    # Setup cache for faster subtitle searches
    setup_cache()

    # Show configured providers
    if PROVIDER_CONFIGS:
        print(f"Configured providers: {', '.join(PROVIDER_CONFIGS.keys())}")
    else:
        print("No provider credentials configured (using anonymous access)")

    # Setup directories
    originals_dir = target_path / "Originals"
    backup_dir = target_path / "Subtitle_Backups"

    originals_dir.mkdir(exist_ok=True)

    # Find all video files
    video_files = sorted([f for f in target_path.iterdir() if is_video_file(f)])

    if not video_files:
        print(f"No video files found in: {target_path}")
        return 0

    print(f"Found {len(video_files)} video file(s)")
    print(f"Target languages: {', '.join(TARGET_LANGUAGES.keys())}")
    print(f"Originals will be moved to: {originals_dir}")

    # Process each video file
    success_count = 0
    missing_spanish: list[str] = []

    for video_path in video_files:
        try:
            # Move original to Originals/ subfolder
            original_in_originals = originals_dir / video_path.name
            shutil.move(str(video_path), str(original_in_originals))

            # Also move any existing external subtitle files
            for lang_code in TARGET_LANGUAGES.keys():
                sub_path = get_subtitle_path(video_path, lang_code)
                if sub_path.exists():
                    shutil.move(str(sub_path), str(originals_dir / sub_path.name))

            # Process from Originals/, output to original location
            success, has_spanish = process_video(original_in_originals, target_path, backup_dir)
            if success:
                success_count += 1
            if not has_spanish:
                missing_spanish.append(video_path.name)
        except Exception as e:
            print(f"  Error processing {video_path.name}: {e}")
            missing_spanish.append(video_path.name)  # Assume missing if error

    print(f"\n{'=' * 50}")
    print(f"Processed {success_count}/{len(video_files)} videos successfully")
    print(f"Originals moved to: {originals_dir}")

    # Print prominent warning for missing Spanish subtitles
    if missing_spanish:
        print()
        print("!" * 60)
        print("!!! FILES MISSING SPANISH SUBTITLES !!!")
        print("!" * 60)
        for filename in missing_spanish:
            print(f"  - {filename}")
        print("!" * 60)
        print(f"Total: {len(missing_spanish)} file(s) without Spanish subtitles")

    return 0 if success_count == len(video_files) else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: python3 {sys.argv[0]} <target_directory>")
        print()
        print("Process video files in a directory:")
        print("  - Download missing Spanish/English subtitles")
        print("  - Synchronize subtitles to video timing")
        print("  - Embed subtitles into video files")
        print()
        print("Originals are moved to <target_directory>/Originals/")
        print("Processed videos remain in <target_directory>/")
        sys.exit(1)

    sys.exit(main(sys.argv[1]))
