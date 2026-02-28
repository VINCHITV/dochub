"""
backend/app/services/docx_builder.py

Exports a generated PRD as a branded .docx file.

Design decisions:
- Open from `assets/template.docx` when it exists so that corporate branding
  (fonts, logo, footer) is inherited automatically. Fall back to Document() for
  local dev where the template may not be present.
- All 7 section titles use `Heading 1` style — this makes the document
  machine-parseable on re-upload via docx_parser.parse_docx_by_headings().
- Hidden metadata paragraph uses the w:vanish XML element to survive a DOCX
  round-trip through Word, LibreOffice, and Google Docs without appearing in
  the rendered document. The [DOCHUB_METADATA] prefix makes it grep-able.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from docx import Document
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

# Path to the branded template, relative to the backend/ working directory.
_TEMPLATE_PATH = Path("assets/template.docx")

# Section display names in render order
_SECTION_DISPLAY_NAMES: dict[str, str] = {
    "title": "Project Title",
    "description": "Description",
    "problem": "Problem Statement",
    "why": "Why Build This",
    "success": "Success Metrics",
    "audience": "Target Audience",
    "open_questions": "Open Questions & Risks",
}


def build_prd_docx(project: Any, prd_sections: dict[str, Any]) -> bytes:
    """
    Generate a .docx document from PRD section data.

    Parameters
    ----------
    project : Project (SQLModel row)
        The Project ORM object — provides project_id and other metadata.
        Typed as Any to avoid a circular import with models.py.
    prd_sections : dict[str, Any]
        Mapping of section_key -> section content. Values may be strings,
        Pydantic model instances, or dicts depending on the caller.

    Returns
    -------
    bytes
        Raw .docx bytes suitable for streaming as a file download.
    """
    if _TEMPLATE_PATH.exists():
        doc = Document(str(_TEMPLATE_PATH))
    else:
        doc = Document()

    # Write each section in canonical PRD order
    for section_key, display_name in _SECTION_DISPLAY_NAMES.items():
        content = prd_sections.get(section_key)
        if content is None:
            continue

        # Section heading — Heading 1 style for machine-parseable round-trips
        doc.add_heading(display_name, level=1)

        # Render content: handle str, dict, or Pydantic model
        text = _render_section_content(section_key, content)
        if text:
            doc.add_paragraph(text)

    # Hidden metadata paragraph at document end
    _append_hidden_metadata(doc, project)

    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def _render_section_content(section_key: str, content: Any) -> str:
    """
    Convert a section value to a plain text string for DOCX output.

    Handles three content shapes:
    - str: used directly
    - Pydantic model / dataclass with model_dump() / __dict__: serialised to
      a readable key=value block (sufficient for hackathon; a future version
      should use a proper template renderer)
    - dict: same as above
    """
    if isinstance(content, str):
        return content

    # Pydantic v2 model
    if hasattr(content, "model_dump"):
        data = content.model_dump()
        return _dict_to_text(data)

    # Plain dict
    if isinstance(content, dict):
        return _dict_to_text(content)

    # Fallback
    return str(content)


def _dict_to_text(data: dict) -> str:
    """
    Flatten a dict into a human-readable multi-line string.

    Lists are rendered as bullet points (- item).
    Nested dicts are JSON-serialised on one line to avoid deep nesting.
    """
    lines: list[str] = []
    for key, value in data.items():
        if key == "source_doc_ids":
            continue  # internal RAG metadata — not shown in the doc
        if isinstance(value, list):
            if value:
                lines.append(f"{key.replace('_', ' ').title()}:")
                for item in value:
                    if isinstance(item, dict):
                        lines.append(f"  - {json.dumps(item)}")
                    else:
                        lines.append(f"  - {item}")
        elif isinstance(value, dict):
            lines.append(f"{key.replace('_', ' ').title()}: {json.dumps(value)}")
        elif value is not None:
            lines.append(f"{key.replace('_', ' ').title()}: {value}")
    return "\n".join(lines)


def _append_hidden_metadata(doc: Document, project: Any) -> None:
    """
    Append a hidden paragraph containing [DOCHUB_METADATA]{...} JSON.

    The w:vanish run property makes the paragraph invisible in rendered output
    while preserving it in the underlying XML (survives Word/LibreOffice/GDocs
    round-trips on DOCX format).

    The extract_embedded_metadata() function in docx_parser.py reads this
    paragraph to auto-link re-uploaded documents to their originating project.
    """
    project_id = getattr(project, "id", None) or getattr(project, "project_id", "unknown")

    metadata_payload = {
        "project_id": str(project_id),
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    metadata_text = f"[DOCHUB_METADATA]{json.dumps(metadata_payload)}"

    # Add paragraph and apply w:vanish to the run
    paragraph = doc.add_paragraph()
    run = paragraph.add_run(metadata_text)

    # Build <w:rPr><w:vanish/></w:rPr> and attach to the run's XML element
    rpr = run._r.get_or_add_rPr()
    vanish = OxmlElement("w:vanish")
    rpr.append(vanish)
