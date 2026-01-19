"""
Tabular data extractor for ReadDocument tool.

Handles CSV, TSV, Excel, and Parquet files using Pandas.
"""
import logging
from pathlib import Path
from typing import Any

from ..config import get_config
from ..exceptions import SheetNotFoundError
from ..security import sanitize_cell_content
from ..utils import parse_column_selection, parse_row_range
from .base import BaseExtractor, ExtractedContent

logger = logging.getLogger(__name__)

# Required dependencies
import pandas as pd  # Required: pandas
import pyarrow  # Required: pyarrow (for parquet support)


class TabularExtractor(BaseExtractor):
    """Extractor for tabular data files (CSV, TSV, Excel, Parquet)."""

    SUPPORTED_EXTENSIONS = {
        ".csv",
        ".tsv",
        ".xlsx",
        ".xls",
        ".ods",
        ".parquet",
    }

    def supports_format(self, extension: str) -> bool:
        """Check if extension is supported."""
        return extension.lower() in self.SUPPORTED_EXTENSIONS

    async def extract(self, path: Path, args: dict[str, Any]) -> ExtractedContent:
        """
        Extract tabular data as formatted table.

        Args:
            path: Path to the tabular file
            args:
                - sheet: Sheet name or index for Excel files
                - rows: Row range (e.g., "1-100", "head:50", "tail:20")
                - columns: Column selection (e.g., "A,B,C" or "name,age")
                - include_metadata: Include file metadata (default: True)

        Returns:
            ExtractedContent with markdown table
        """
        config = get_config()
        sheet = args.get("sheet")
        rows_spec = args.get("rows")
        columns_spec = args.get("columns")
        include_metadata = args.get("include_metadata", True)

        ext = path.suffix.lower()

        # Load data based on format
        df, sheet_names, selected_sheet = await self._load_data(path, ext, sheet)

        total_rows = len(df)
        total_cols = len(df.columns)

        # Apply column selection
        available_columns = list(df.columns.astype(str))
        selected_columns = parse_column_selection(columns_spec, available_columns)
        df = df[selected_columns]

        # Apply row selection
        start_idx, end_idx = parse_row_range(rows_spec, total_rows)

        # Apply memory limits
        memory_config = config.memory
        if end_idx - start_idx > memory_config.max_dataframe_rows:
            end_idx = start_idx + memory_config.max_dataframe_rows
            logger.warning(f"Row limit applied: {memory_config.max_dataframe_rows}")

        df = df.iloc[start_idx:end_idx]

        # Format as markdown table
        content = self._format_as_markdown(df, start_idx, config)

        # Build metadata
        metadata = {}
        if include_metadata:
            stat = path.stat()
            metadata = {
                "filename": path.name,
                "size_bytes": stat.st_size,
            }
            if sheet_names:
                metadata["sheets"] = sheet_names
                metadata["selected_sheet"] = selected_sheet

        result = ExtractedContent(
            content=content,
            format_type=self._get_format_name(ext),
            metadata=metadata,
            total_rows=total_rows,
            total_columns=total_cols,
            column_names=selected_columns,
            was_truncated=(end_idx < total_rows),
        )

        if end_idx < total_rows:
            result.add_note(f"{total_rows - end_idx} more rows not shown")

        logger.info(f"Extracted {len(df)} rows x {len(df.columns)} cols from {path.name}")
        return result

    async def _load_data(
        self, path: Path, ext: str, sheet: str | int | None
    ) -> tuple[Any, list[str] | None, str | None]:
        """
        Load data from file based on extension.

        Returns:
            Tuple of (DataFrame, sheet_names, selected_sheet_name)
        """
        config = get_config()
        memory_config = config.memory

        sheet_names = None
        selected_sheet = None

        if ext == ".csv":
            df = pd.read_csv(
                path,
                nrows=memory_config.max_dataframe_rows,
                on_bad_lines="skip",
            )

        elif ext == ".tsv":
            df = pd.read_csv(
                path,
                sep="\t",
                nrows=memory_config.max_dataframe_rows,
                on_bad_lines="skip",
            )

        elif ext in (".xlsx", ".xls", ".ods"):
            # Get sheet names first
            xl = pd.ExcelFile(path)
            sheet_names = xl.sheet_names

            # Determine which sheet to read
            if sheet is None:
                selected_sheet = sheet_names[0] if sheet_names else None
            elif isinstance(sheet, int):
                if 0 <= sheet < len(sheet_names):
                    selected_sheet = sheet_names[sheet]
                else:
                    raise SheetNotFoundError(sheet, sheet_names)
            else:
                if sheet in sheet_names:
                    selected_sheet = sheet
                else:
                    raise SheetNotFoundError(sheet, sheet_names)

            df = pd.read_excel(
                path,
                sheet_name=selected_sheet,
                nrows=memory_config.max_dataframe_rows,
            )

        elif ext == ".parquet":
            df = pd.read_parquet(path)
            # Apply row limit after loading
            if len(df) > memory_config.max_dataframe_rows:
                df = df.head(memory_config.max_dataframe_rows)

        else:
            # Try generic CSV as fallback
            df = pd.read_csv(path, nrows=memory_config.max_dataframe_rows)

        # Limit columns if needed
        if len(df.columns) > memory_config.max_dataframe_cols:
            df = df.iloc[:, : memory_config.max_dataframe_cols]
            logger.warning(f"Column limit applied: {memory_config.max_dataframe_cols}")

        return df, sheet_names, selected_sheet

    def _format_as_markdown(self, df: Any, start_idx: int, config: Any) -> str:
        """Format DataFrame as markdown table with sanitized content."""
        output_config = config.output

        # Sanitize all cell content
        def sanitize_cell(val):
            if pd.isna(val):
                return ""
            return sanitize_cell_content(str(val), output_config)

        df_sanitized = df.map(sanitize_cell)

        # Add row numbers column
        df_sanitized.insert(0, "Row", range(start_idx + 1, start_idx + len(df_sanitized) + 1))

        # Format as markdown
        lines = []

        # Header
        headers = [str(col) for col in df_sanitized.columns]
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("|" + "|".join(["---"] * len(headers)) + "|")

        # Rows
        for _, row in df_sanitized.iterrows():
            cells = [str(val) for val in row]
            lines.append("| " + " | ".join(cells) + " |")

        return "\n".join(lines)

    def _get_format_name(self, ext: str) -> str:
        """Get human-readable format name."""
        names = {
            ".csv": "CSV",
            ".tsv": "TSV",
            ".xlsx": "Excel Spreadsheet",
            ".xls": "Excel Spreadsheet (Legacy)",
            ".ods": "OpenDocument Spreadsheet",
            ".parquet": "Apache Parquet",
        }
        return names.get(ext, "Tabular Data")
