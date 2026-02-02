# Subtitling Tools

Scripts for managing subtitles and renaming media files.

## Scripts

### download_sync_and_embed.py

Downloads, synchronizes, and embeds subtitles for video files.

```bash
python download_sync_and_embed.py <directory>
```

For each video file:
1. Downloads missing Spanish/English subtitles from OpenSubtitles and Addic7ed
2. Synchronizes subtitle timing using ffsubsync
3. Embeds subtitles into the video (Spanish track first)
4. Moves originals to `Originals/` subfolder

### rename_media.py

Renames video files based on parsed metadata.

```bash
python rename_media.py <directory>
python rename_media.py <directory> --dry-run
```

Naming format:
- Movies: `Title (Year).ext`
- TV Episodes: `Show Name SSxEE - Episode Name.ext`

## Setup

### Dependencies

```bash
pip install subliminal ffsubsync guessit tmdbsimple chardet babelfish
```

Also requires `ffmpeg` and `ffprobe` installed on the system.

### Configuration

Create a `secrets.txt` file in the project root:

```
OPENSUBTITLES_USERNAME=your_username
OPENSUBTITLES_PASSWORD=your_password
ADDIC7ED_USERNAME=your_username
ADDIC7ED_PASSWORD=your_password
TMDB_API_KEY=your_api_key
```

- OpenSubtitles: https://www.opensubtitles.org/
- Addic7ed: https://www.addic7ed.com/
- TMDb API key: https://www.themoviedb.org/settings/api
