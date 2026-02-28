"""
backend/app/services/file_parser.py

Converts uploaded file bytes into plain text strings.
Supports .txt (UTF-8) and .docx (via mammoth raw text extraction).
Raises ValueError for any unsupported file extension.
"""

import mammoth


def parse_file(filename: str, content: bytes) -> str:
    """
    Parse uploaded file bytes into plain text.

    Parameters
    ----------
    filename : str
        Original filename — used to determine file type by extension.
    content : bytes
        Raw file bytes as received from the upload endpoint.

    Returns
    -------
    str
        Extracted plain text content.

    Raises
    ------
    ValueError
        If the file extension is not .txt or .docx.
    """
    lower = filename.lower()

    if lower.endswith(".txt"):
        return content.decode("utf-8")

    if lower.endswith(".docx"):
        import io

        # mammoth.extract_raw_text returns a Result with .value (str) and .messages (list)
        result = mammoth.extract_raw_text(io.BytesIO(content))
        return result.value

    raise ValueError(
        f"Unsupported file type: '{filename}'. Only .txt and .docx files are accepted."
    )
