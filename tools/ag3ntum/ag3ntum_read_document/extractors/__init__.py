"""
Extractor registry for ReadDocument tool.

Maps format categories to their appropriate extractors.
Heavy dependencies (PyMuPDF, Pillow, mutagen, openpyxl) are loaded lazily
to avoid ~200MB of import-time memory when extractors are not used.
"""
from ..format_detector import FormatCategory, FormatInfo
from .base import BaseExtractor, ExtractedContent

__all__ = [
    "BaseExtractor",
    "ExtractedContent",
    "get_extractor",
]


def _get_extractor_class(category: FormatCategory) -> type[BaseExtractor] | None:
    """Lazy-load and return the extractor class for a category."""
    if category == FormatCategory.TEXT or category == FormatCategory.STRUCTURED:
        from .text import TextExtractor
        return TextExtractor
    elif category == FormatCategory.TABULAR:
        from .tabular import TabularExtractor
        return TabularExtractor
    elif category == FormatCategory.PDF:
        from .pdf import PDFExtractor
        return PDFExtractor
    elif category == FormatCategory.OFFICE:
        from .office import OfficeExtractor
        return OfficeExtractor
    elif category == FormatCategory.ARCHIVE:
        from .archive import ArchiveExtractor
        return ArchiveExtractor
    elif category == FormatCategory.IMAGE:
        from .image import ImageExtractor
        return ImageExtractor
    elif category == FormatCategory.AUDIO:
        from .audio import AudioExtractor
        return AudioExtractor
    else:
        return None


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

    extractor_class = _get_extractor_class(format_info.category)

    if extractor_class is None:
        raise FormatNotSupportedError(format_info.extension, format_info.mime_type)

    return extractor_class()
