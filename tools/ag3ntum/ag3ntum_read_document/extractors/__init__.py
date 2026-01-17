"""
Extractor registry for ReadDocument tool.

Maps format categories to their appropriate extractors.
"""
from ..format_detector import FormatCategory, FormatInfo
from .base import BaseExtractor, ExtractedContent
from .text import TextExtractor
from .tabular import TabularExtractor
from .pdf import PDFExtractor
from .office import OfficeExtractor
from .archive import ArchiveExtractor
from .image import ImageExtractor
from .audio import AudioExtractor

__all__ = [
    "BaseExtractor",
    "ExtractedContent",
    "get_extractor",
    "TextExtractor",
    "TabularExtractor",
    "PDFExtractor",
    "OfficeExtractor",
    "ArchiveExtractor",
    "ImageExtractor",
    "AudioExtractor",
]

# Extractor registry mapping categories to extractor classes
EXTRACTOR_REGISTRY: dict[FormatCategory, type[BaseExtractor]] = {
    FormatCategory.TEXT: TextExtractor,
    FormatCategory.TABULAR: TabularExtractor,
    FormatCategory.PDF: PDFExtractor,
    FormatCategory.OFFICE: OfficeExtractor,
    FormatCategory.ARCHIVE: ArchiveExtractor,
    FormatCategory.IMAGE: ImageExtractor,
    FormatCategory.AUDIO: AudioExtractor,
    FormatCategory.STRUCTURED: TextExtractor,  # Fallback to text for now
}


def get_extractor(format_info: FormatInfo) -> BaseExtractor:
    """
    Get the appropriate extractor for a format.

    Args:
        format_info: Detected format information

    Returns:
        Instantiated extractor for the format

    Raises:
        FormatNotSupportedError: If no extractor is available
    """
    from ..exceptions import FormatNotSupportedError

    extractor_class = EXTRACTOR_REGISTRY.get(format_info.category)

    if extractor_class is None:
        raise FormatNotSupportedError(format_info.extension, format_info.mime_type)

    return extractor_class()
