"""
Image metadata extractor for ReadDocument tool.

Uses Pillow for image properties and exifread for EXIF metadata.
"""
import logging
from pathlib import Path
from typing import Any

from ..config import get_config
from ..security import sanitize_metadata
from ..utils import format_bytes
from .base import BaseExtractor, ExtractedContent

logger = logging.getLogger(__name__)

# Required dependencies
from PIL import Image  # Required: Pillow
from PIL.ExifTags import TAGS, GPSTAGS
import exifread  # Required: exifread


class ImageExtractor(BaseExtractor):
    """Extractor for image files."""

    SUPPORTED_EXTENSIONS = {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".tiff",
        ".tif",
        ".webp",
        ".ico",
        ".psd",
        ".heic",
        ".heif",
    }

    def supports_format(self, extension: str) -> bool:
        """Check if extension is supported."""
        return extension.lower() in self.SUPPORTED_EXTENSIONS

    async def extract(self, path: Path, args: dict[str, Any]) -> ExtractedContent:
        """
        Extract image properties and metadata.

        Args:
            path: Path to the image file
            args:
                - include_metadata: Include EXIF metadata (default: True)

        Returns:
            ExtractedContent with image information
        """
        config = get_config()
        include_metadata = args.get("include_metadata", True)

        # Open image
        try:
            img = Image.open(path)
        except Exception as e:
            logger.error(f"Failed to open image {path}: {e}")
            raise

        # Basic properties
        width, height = img.size
        mode = img.mode
        format_name = img.format or path.suffix.upper().lstrip(".")
        file_size = path.stat().st_size

        # Color depth
        mode_to_depth = {
            "1": "1-bit (B&W)",
            "L": "8-bit (Grayscale)",
            "P": "8-bit (Palette)",
            "RGB": "24-bit (RGB)",
            "RGBA": "32-bit (RGBA)",
            "CMYK": "32-bit (CMYK)",
            "YCbCr": "24-bit (YCbCr)",
            "LAB": "24-bit (LAB)",
            "HSV": "24-bit (HSV)",
            "I": "32-bit (Integer)",
            "F": "32-bit (Float)",
            "LA": "16-bit (Grayscale + Alpha)",
            "PA": "16-bit (Palette + Alpha)",
            "RGBa": "32-bit (RGB premultiplied alpha)",
            "La": "16-bit (Grayscale premultiplied alpha)",
            "I;16": "16-bit (Integer)",
            "I;16L": "16-bit (Integer, Little Endian)",
            "I;16B": "16-bit (Integer, Big Endian)",
        }
        color_depth = mode_to_depth.get(mode, f"{mode}")

        # Format content
        lines = []
        lines.append(f"**Image:** {path.name}")
        lines.append(f"**Dimensions:** {width} x {height} pixels")
        lines.append(f"**Format:** {format_name}")
        lines.append(f"**Color:** {color_depth}")
        lines.append(f"**Size:** {format_bytes(file_size)}")

        # Animation info (for GIF/WebP)
        if hasattr(img, "n_frames") and img.n_frames > 1:
            lines.append(f"**Frames:** {img.n_frames}")

        # ICC profile
        if img.info.get("icc_profile"):
            lines.append("**ICC Profile:** Present")

        # Extract EXIF metadata
        metadata = {}
        if include_metadata:
            exif_data = self._extract_exif(path, img)
            if exif_data:
                lines.append("")
                lines.append("**EXIF Metadata:**")
                for key, value in list(exif_data.items())[:20]:  # Limit displayed fields
                    lines.append(f"  - {key}: {value}")
                    metadata[key] = value

        img.close()

        content = "\n".join(lines)

        return ExtractedContent(
            content=content,
            format_type=f"{format_name} Image",
            metadata={
                "width": width,
                "height": height,
                "format": format_name,
                "mode": mode,
                "size_bytes": file_size,
                **metadata,
            },
        )

    def _extract_exif(self, path: Path, img: Any) -> dict[str, Any]:
        """
        Extract EXIF metadata from image.

        Tries exifread first (more detailed), falls back to PIL.
        """
        config = get_config()
        exif_data = {}

        # Try exifread first (provides more detailed EXIF)
        try:
            with open(path, "rb") as f:
                tags = exifread.process_file(f, details=False)

            for tag, value in tags.items():
                # Skip thumbnail data and internal tags
                if tag.startswith("Thumbnail") or tag.startswith("EXIF MakerNote"):
                    continue
                if "thumbnail" in tag.lower():
                    continue

                # Clean up tag name
                clean_tag = tag.replace("EXIF ", "").replace("Image ", "")
                exif_data[clean_tag] = str(value)

        except Exception as e:
            logger.debug(f"exifread failed: {e}")

        # Fall back to PIL EXIF
        if not exif_data:
            try:
                pil_exif = img.getexif()
                if pil_exif:
                    for tag_id, value in pil_exif.items():
                        tag_name = TAGS.get(tag_id, str(tag_id))

                        # Handle GPS data specially
                        if tag_name == "GPSInfo":
                            gps_data = {}
                            for gps_tag_id, gps_value in value.items():
                                gps_tag_name = GPSTAGS.get(gps_tag_id, str(gps_tag_id))
                                gps_data[gps_tag_name] = str(gps_value)
                            # Format GPS coordinates if available
                            if "GPSLatitude" in gps_data and "GPSLongitude" in gps_data:
                                lat = self._parse_gps_coord(
                                    gps_data.get("GPSLatitude"),
                                    gps_data.get("GPSLatitudeRef", "N"),
                                )
                                lon = self._parse_gps_coord(
                                    gps_data.get("GPSLongitude"),
                                    gps_data.get("GPSLongitudeRef", "E"),
                                )
                                if lat and lon:
                                    exif_data["GPS"] = f"{lat}, {lon}"
                        else:
                            exif_data[tag_name] = str(value)[:200]  # Truncate long values

            except Exception as e:
                logger.debug(f"PIL EXIF failed: {e}")

        # Sanitize metadata
        return sanitize_metadata(exif_data, config.output)

    def _parse_gps_coord(self, coord_str: str, ref: str) -> str | None:
        """Parse GPS coordinate string to decimal degrees."""
        try:
            # Handle various formats
            if not coord_str:
                return None

            # Simple parsing - coordinate strings vary widely
            # Return as-is with reference
            return f"{coord_str} {ref}"
        except Exception:
            return None
