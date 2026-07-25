"""PDF export of a conversation or a case briefing.

Deterministic service (no LLM). Exports are watermarked with the requester's
identity and the export itself is audited, per architecture §12.3.

Kannada rendering: ReportLab's built-in fonts have no Kannada glyphs. If a
Kannada-capable TrueType font is present (``KSPCIP_PDF_FONT_PATH`` or a system
Noto Sans Kannada), it is registered and used; otherwise Kannada text is
exported alongside its English original and the PDF says so explicitly rather
than emitting tofu boxes silently.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from ...domain.models import Principal
from ...domain.ports import FileStore
from ...infrastructure.observability import get_logger

LOGGER = get_logger(__name__)

_FONT_CANDIDATES = [
    os.environ.get("KSPCIP_PDF_FONT_PATH", ""),
    "/usr/share/fonts/truetype/noto/NotoSansKannada-Regular.ttf",
    "/usr/share/fonts/truetype/lohit-kannada/Lohit-Kannada.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansKannada-Regular.ttf",
]

INK = colors.HexColor("#12212F")
RULE = colors.HexColor("#C8D3DC")
ACCENT = colors.HexColor("#1F5C7A")
MUTED = colors.HexColor("#5A6B78")


def _register_indic_font() -> str | None:
    for candidate in _FONT_CANDIDATES:
        if candidate and Path(candidate).exists():
            try:
                pdfmetrics.registerFont(TTFont("IndicSans", candidate))
                return "IndicSans"
            except Exception:  # noqa: BLE001 - font loading is best effort
                LOGGER.warning("indic_font_registration_failed", extra={"path": candidate})
    return None


class PDFExportService:
    def __init__(self, file_store: FileStore) -> None:
        self._file_store = file_store
        self._indic_font = _register_indic_font()

    @property
    def supports_kannada_glyphs(self) -> bool:
        return self._indic_font is not None

    # ---------------------------------------------------------------- public
    def export_conversation(
        self,
        *,
        principal: Principal,
        session_id: str,
        turns: Sequence[dict[str, Any]],
        scope_label: str,
    ) -> dict[str, Any]:
        title = "Conversation record"
        subtitle = f"Session {session_id}"
        blocks = self._conversation_blocks(turns)
        key = f"exports/{principal.user_id}/{session_id}.pdf"
        return self._render(key=key, title=title, subtitle=subtitle, principal=principal,
                            scope_label=scope_label, blocks=blocks)

    def export_case_briefing(
        self,
        *,
        principal: Principal,
        case_reference: str,
        sections: Sequence[tuple[str, Sequence[str]]],
        scope_label: str,
    ) -> dict[str, Any]:
        blocks: list[Any] = []
        styles = self._styles()
        for heading, lines in sections:
            blocks.append(Paragraph(heading, styles["h2"]))
            for line in lines:
                blocks.append(Paragraph(self._escape(line), styles["body"]))
            blocks.append(Spacer(1, 4 * mm))
        key = f"exports/{principal.user_id}/briefing-{case_reference}.pdf"
        return self._render(key=key, title="Case briefing", subtitle=f"FIR {case_reference}",
                            principal=principal, scope_label=scope_label, blocks=blocks)

    # -------------------------------------------------------------- internals
    def _styles(self) -> dict[str, ParagraphStyle]:
        base = getSampleStyleSheet()
        body_font = self._indic_font or "Helvetica"
        return {
            "h1": ParagraphStyle("h1", parent=base["Title"], fontName="Helvetica-Bold", fontSize=17,
                                 textColor=INK, spaceAfter=2 * mm, alignment=TA_LEFT),
            "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=11,
                                 textColor=ACCENT, spaceBefore=4 * mm, spaceAfter=2 * mm),
            "meta": ParagraphStyle("meta", parent=base["Normal"], fontName="Helvetica", fontSize=8.5,
                                   textColor=MUTED, leading=12),
            "body": ParagraphStyle("body", parent=base["Normal"], fontName=body_font, fontSize=9.5,
                                   textColor=INK, leading=14, spaceAfter=1.5 * mm),
            "quote": ParagraphStyle("quote", parent=base["Normal"], fontName=body_font, fontSize=9.5,
                                    textColor=INK, leading=14, leftIndent=6 * mm, spaceAfter=1.5 * mm),
            "cite": ParagraphStyle("cite", parent=base["Normal"], fontName="Courier", fontSize=7.6,
                                   textColor=MUTED, leading=10, leftIndent=6 * mm, spaceAfter=2.5 * mm),
        }

    def _conversation_blocks(self, turns: Sequence[dict[str, Any]]) -> list[Any]:
        styles = self._styles()
        blocks: list[Any] = []
        if not turns:
            blocks.append(Paragraph("This session has no recorded turns.", styles["body"]))
            return blocks
        for turn in turns:
            group: list[Any] = [
                Paragraph(f"Turn {turn['turn_seq']} · {turn['created_at'][:19].replace('T', ' ')} UTC",
                          styles["meta"]),
                Paragraph(f"<b>Question</b> ({turn['user_language']}): {self._escape(turn['user_text_original'])}",
                          styles["quote"]),
            ]
            if turn["user_language"] != "en" and turn["user_text_english"] != turn["user_text_original"]:
                group.append(Paragraph(f"<b>Working English</b>: {self._escape(turn['user_text_english'])}",
                                       styles["quote"]))
            group.append(Paragraph(f"<b>Answer</b>: {self._escape(turn['answer_text_english'])}", styles["quote"]))
            if turn.get("answer_text_display") and turn["answer_text_display"] != turn["answer_text_english"]:
                group.append(Paragraph(f"<b>Answer (as shown)</b>: {self._escape(turn['answer_text_display'])}",
                                       styles["quote"]))
            locators = turn.get("evidence_locators") or []
            if locators:
                group.append(Paragraph("Evidence: " + ", ".join(str(loc) for loc in locators[:12]), styles["cite"]))
            blocks.append(KeepTogether(group))
        return blocks

    def _render(
        self,
        *,
        key: str,
        title: str,
        subtitle: str,
        principal: Principal,
        scope_label: str,
        blocks: Sequence[Any],
    ) -> dict[str, Any]:
        import io

        styles = self._styles()
        buffer = io.BytesIO()
        generated = datetime.now(timezone.utc)
        watermark = f"Exported by {principal.display_name} ({principal.username} · {principal.role})"

        def decorate(canvas, doc) -> None:
            canvas.saveState()
            canvas.setStrokeColor(RULE)
            canvas.setLineWidth(0.5)
            canvas.line(18 * mm, A4[1] - 22 * mm, A4[0] - 18 * mm, A4[1] - 22 * mm)
            canvas.setFont("Helvetica", 7.5)
            canvas.setFillColor(MUTED)
            canvas.drawString(18 * mm, A4[1] - 18 * mm, "KARNATAKA STATE POLICE · CRIME INTELLIGENCE PLATFORM")
            canvas.drawRightString(A4[0] - 18 * mm, A4[1] - 18 * mm, "INTELLIGENCE PRODUCT — NOT EVIDENCE")
            canvas.line(18 * mm, 18 * mm, A4[0] - 18 * mm, 18 * mm)
            canvas.drawString(18 * mm, 13 * mm, watermark)
            canvas.drawRightString(A4[0] - 18 * mm, 13 * mm, f"Page {doc.page}")
            canvas.restoreState()

        doc = BaseDocTemplate(
            buffer, pagesize=A4,
            leftMargin=18 * mm, rightMargin=18 * mm, topMargin=28 * mm, bottomMargin=24 * mm,
            title=title, author="KSP-CIP",
        )
        frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body")
        doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=decorate)])

        story: list[Any] = [Paragraph(title, styles["h1"]), Paragraph(subtitle, styles["meta"])]
        meta_rows = [
            ["Generated", generated.strftime("%Y-%m-%d %H:%M UTC")],
            ["Requested by", f"{principal.display_name} ({principal.role})"],
            ["Authorized scope", scope_label],
        ]
        if not self.supports_kannada_glyphs:
            meta_rows.append(["Script note", "No Kannada-capable font installed; Kannada text may not render."])
        table = Table(meta_rows, colWidths=[32 * mm, doc.width - 32 * mm])
        table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("TEXTCOLOR", (0, 0), (0, -1), MUTED),
            ("TEXTCOLOR", (1, 0), (1, -1), INK),
            ("LINEBELOW", (0, 0), (-1, -2), 0.25, RULE),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.extend([Spacer(1, 3 * mm), table, Spacer(1, 6 * mm)])
        story.extend(blocks)
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph(
            "This document is an intelligence product generated from the Crime Intelligence Platform. "
            "Every claim carries a source reference. Verify against the authoritative FIR record in the "
            "source system before relying on it in any proceeding.",
            styles["meta"],
        ))
        doc.build(story)

        payload = buffer.getvalue()
        self._file_store.write_bytes(key, payload, "application/pdf")
        return {
            "key": key,
            "url": self._file_store.url_for(key),
            "size_bytes": len(payload),
            "generated_at": generated.isoformat(),
        }

    @staticmethod
    def _escape(text: str) -> str:
        return (
            str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
