#!/usr/bin/env python3
"""
Create a minimal branded template.docx for DOCX export.

Run once before first use:
    python scripts/create_template.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from docx import Document
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH


def create_template():
    doc = Document()

    # Title style customization
    title_style = doc.styles["Title"]
    title_style.font.name = "Calibri"
    title_style.font.size = Pt(28)
    title_style.font.color.rgb = RGBColor(0x1F, 0x49, 0x8C)  # dark blue

    # Heading 1 style
    h1_style = doc.styles["Heading 1"]
    h1_style.font.name = "Calibri"
    h1_style.font.size = Pt(16)
    h1_style.font.color.rgb = RGBColor(0x1F, 0x49, 0x8C)

    # Normal style
    normal_style = doc.styles["Normal"]
    normal_style.font.name = "Calibri"
    normal_style.font.size = Pt(11)

    # Add a header paragraph as branding placeholder
    header = doc.add_paragraph("DocHub — Product Requirements Document")
    header.alignment = WD_ALIGN_PARAGRAPH.CENTER
    header.runs[0].font.size = Pt(10)
    header.runs[0].font.color.rgb = RGBColor(0x88, 0x88, 0x88)

    # Save
    assets_dir = os.path.join(os.path.dirname(__file__), "..", "assets")
    os.makedirs(assets_dir, exist_ok=True)
    out_path = os.path.join(assets_dir, "template.docx")
    doc.save(out_path)
    print(f"Template saved to {out_path}")


if __name__ == "__main__":
    create_template()
