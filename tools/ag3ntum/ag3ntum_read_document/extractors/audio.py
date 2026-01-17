"""
Audio metadata extractor for ReadDocument tool.

Uses mutagen for audio file metadata extraction.
"""
import logging
from pathlib import Path
from typing import Any

from ..config import get_config
from ..security import sanitize_metadata
from ..utils import format_bytes, format_duration
from .base import BaseExtractor, ExtractedContent

logger = logging.getLogger(__name__)

# Required dependencies
import mutagen  # Required: mutagen
from mutagen.easyid3 import EasyID3
from mutagen.mp3 import MP3
from mutagen.flac import FLAC
from mutagen.oggvorbis import OggVorbis
from mutagen.mp4 import MP4
from mutagen.wave import WAVE
from mutagen.aiff import AIFF


class AudioExtractor(BaseExtractor):
    """Extractor for audio file metadata."""

    SUPPORTED_EXTENSIONS = {
        ".mp3",
        ".wav",
        ".flac",
        ".ogg",
        ".m4a",
        ".aac",
        ".wma",
        ".aiff",
        ".aif",
    }

    def supports_format(self, extension: str) -> bool:
        """Check if extension is supported."""
        return extension.lower() in self.SUPPORTED_EXTENSIONS

    async def extract(self, path: Path, args: dict[str, Any]) -> ExtractedContent:
        """
        Extract audio file metadata.

        Args:
            path: Path to the audio file
            args:
                - include_metadata: Include detailed tags (default: True)

        Returns:
            ExtractedContent with audio information
        """
        config = get_config()
        include_metadata = args.get("include_metadata", True)

        # Open audio file
        try:
            audio = mutagen.File(path)
        except Exception as e:
            logger.error(f"Failed to open audio file {path}: {e}")
            raise

        if audio is None:
            logger.error(f"Could not identify audio format: {path}")
            raise RuntimeError(f"Could not identify audio format: {path}")

        file_size = path.stat().st_size
        ext = path.suffix.lower()

        # Extract basic info
        duration = getattr(audio.info, "length", None)
        bitrate = getattr(audio.info, "bitrate", None)
        sample_rate = getattr(audio.info, "sample_rate", None)
        channels = getattr(audio.info, "channels", None)

        # Format info
        format_info = self._get_format_info(audio, ext)

        # Build content
        lines = []
        lines.append(f"**Audio:** {path.name}")
        lines.append(f"**Format:** {format_info}")
        lines.append(f"**Size:** {format_bytes(file_size)}")

        if duration:
            lines.append(f"**Duration:** {format_duration(duration)}")

        if bitrate:
            # Bitrate is in bits/sec, convert to kbps
            kbps = bitrate // 1000 if bitrate > 1000 else bitrate
            lines.append(f"**Bitrate:** {kbps} kbps")

        if sample_rate:
            lines.append(f"**Sample Rate:** {sample_rate} Hz")

        if channels:
            channel_str = "Mono" if channels == 1 else "Stereo" if channels == 2 else f"{channels} channels"
            lines.append(f"**Channels:** {channel_str}")

        # Extract tags/metadata
        metadata = {
            "format": format_info,
            "size_bytes": file_size,
        }
        if duration:
            metadata["duration_seconds"] = round(duration, 2)
        if bitrate:
            metadata["bitrate_kbps"] = bitrate // 1000 if bitrate > 1000 else bitrate
        if sample_rate:
            metadata["sample_rate_hz"] = sample_rate
        if channels:
            metadata["channels"] = channels

        if include_metadata:
            tags = self._extract_tags(audio, ext)
            if tags:
                lines.append("")
                lines.append("**Tags:**")
                for key, value in list(tags.items())[:15]:  # Limit displayed tags
                    lines.append(f"  - {key}: {value}")
                    metadata[key.lower().replace(" ", "_")] = value

        content = "\n".join(lines)

        # Sanitize metadata
        metadata = sanitize_metadata(metadata, config.output)

        return ExtractedContent(
            content=content,
            format_type=f"Audio ({format_info})",
            metadata=metadata,
        )

    def _get_format_info(self, audio: Any, ext: str) -> str:
        """Get human-readable format info."""
        format_names = {
            ".mp3": "MP3",
            ".wav": "WAV",
            ".flac": "FLAC",
            ".ogg": "Ogg Vorbis",
            ".m4a": "AAC/M4A",
            ".aac": "AAC",
            ".wma": "WMA",
            ".aiff": "AIFF",
            ".aif": "AIFF",
        }

        base_name = format_names.get(ext, ext.upper().lstrip("."))

        # Add codec info if available
        if hasattr(audio.info, "codec"):
            return f"{base_name} ({audio.info.codec})"

        return base_name

    def _extract_tags(self, audio: Any, ext: str) -> dict[str, str]:
        """Extract common audio tags."""
        tags = {}

        # Common tag mappings
        tag_mappings = {
            "title": ["title", "TIT2", "\xa9nam", "TITLE"],
            "artist": ["artist", "TPE1", "\xa9ART", "ARTIST"],
            "album": ["album", "TALB", "\xa9alb", "ALBUM"],
            "album_artist": ["albumartist", "TPE2", "aART", "ALBUMARTIST"],
            "track": ["tracknumber", "TRCK", "trkn", "TRACKNUMBER"],
            "year": ["date", "TDRC", "\xa9day", "DATE", "year"],
            "genre": ["genre", "TCON", "\xa9gen", "GENRE"],
            "composer": ["composer", "TCOM", "\xa9wrt", "COMPOSER"],
            "comment": ["comment", "COMM", "\xa9cmt", "DESCRIPTION"],
        }

        # Try to get tags based on file type
        try:
            if hasattr(audio, "tags") and audio.tags:
                for display_name, possible_keys in tag_mappings.items():
                    for key in possible_keys:
                        if key in audio.tags:
                            value = audio.tags[key]
                            # Handle list values
                            if isinstance(value, list):
                                value = value[0] if value else ""
                            tags[display_name.replace("_", " ").title()] = str(value)[:200]
                            break

            # For MP3 with ID3 tags
            if ext == ".mp3":
                try:
                    id3 = EasyID3(audio.filename)
                    for key, value in id3.items():
                        display_key = key.replace("_", " ").title()
                        if display_key not in tags:
                            tags[display_key] = str(value[0] if value else "")[:200]
                except Exception:
                    pass

        except Exception as e:
            logger.debug(f"Tag extraction error: {e}")

        return tags
