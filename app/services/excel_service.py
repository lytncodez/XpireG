"""Excel (.xlsx) reader. Validation/persistence is shared with CSV in csv_service."""

from __future__ import annotations

import io
import zipfile

import pandas as pd

from app.services.csv_service import ImportFileError


def read_excel(content: bytes) -> pd.DataFrame:
    """Read the first worksheet. Dates stay as datetime objects; everything else as raw values."""
    if not zipfile.is_zipfile(io.BytesIO(content)):
        raise ImportFileError("The file is not a valid .xlsx workbook")
    try:
        df = pd.read_excel(io.BytesIO(content), engine="openpyxl", sheet_name=0, dtype=object)
    except (ValueError, KeyError, zipfile.BadZipFile, OSError) as exc:
        raise ImportFileError(f"Could not read the Excel file: {exc}") from exc
    if df.empty and not len(df.columns):
        raise ImportFileError("The worksheet is empty")
    return df
