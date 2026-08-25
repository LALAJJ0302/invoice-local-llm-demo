import os
import docx
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

def set_cell_background(cell, fill_hex):
    """Helper function to set background color for a table cell."""
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), fill_hex)
    tcPr.append(shd)

def create_briefing_docx(output_filename: str = "Enterprise_AI_Workflow_Briefing.docx"):
    doc = docx.Document()

    # Configure Margins (1 inch all around)
    sections = doc.sections
    for section in sections:
        section.top_margin = Inches(1.0)
        section.bottom_margin = Inches(1.0)
        section.left_margin = Inches(1.0)
        section.right_margin = Inches(1.0)

    # Base Font Settings
    style = doc.styles['Normal']
    font = style.font
    font.name = 'Calibri'
    font.size = Pt(11)
    font.color.rgb = RGBColor(0x33, 0x33, 0x33)

    # =========================================================================
    # Document Title & Subtitle
    # =========================================================================
    title_p = doc.add_paragraph()
    title_p.paragraph_format.space_after = Pt(4)
    title_run = title_p.add_run("Enterprise AI Workflow Automation Platform")
    title_run.font.size = Pt(24)
    title_run.font.bold = True
    title_run.font.color.rgb = RGBColor(0x0F, 0x17, 0x2A)

    subtitle_p = doc.add_paragraph()
    subtitle_p.paragraph_format.space_after = Pt(18)
    sub_run = subtitle_p.add_run("Technical Progress, Architectural Evolution & Team Onboarding Guide")
    sub_run.font.size = Pt(13)
    sub_run.font.italic = True
    sub_run.font.color.rgb = RGBColor(0x64, 0x74, 0x8B)

    # =========================================================================
    # Section 1: Executive Summary & Objective
    # =========================================================================
    h1 = doc.add_heading("1. Executive Summary & Objective", level=1)
    h1.paragraph_format.space_before = Pt(12)
    h1.paragraph_format.space_after = Pt(6)

    doc.add_paragraph(
        "The Enterprise AI Workflow Automation Platform is an intelligent, zero-cost, local-first document processing "
        "pipeline designed to automate manual data entry for incoming business documents (specifically invoices and receipts). "
        "Traditional document handling requires 3–5 minutes per document, carries high labor costs, and introduces data entry errors. "
        "Our platform completely automates the lifecycle from email arrival to database analytics."
    )

    # Key Objectives
    obj_bullets = [
        ("Automated Ingestion: ", "Monitor mailbox continuously and extract invoice attachments."),
        ("Local AI Extraction: ", "Utilize local LLMs (Ollama) to extract structured JSON data without token costs or privacy leaks."),
        ("Confidence Gating: ", "Validate extraction accuracy and route documents between direct approval (Validated) and human review (NeedsReview)."),
        ("Persistence & Analytics: ", "Store records in SQLite and visualize real-time KPIs through an interactive Streamlit web dashboard.")
    ]
    for bold_prefix, text in obj_bullets:
        bp = doc.add_paragraph(style='List Bullet')
        bp.paragraph_format.space_after = Pt(3)
        b_run = bp.add_run(bold_prefix)
        b_run.bold = True
        bp.add_run(text)

    # =========================================================================
    # Section 2: Architectural Evolution (Cloud vs. Local Open-Source)
    # =========================================================================
    h2 = doc.add_heading("2. Architectural Evolution: Why We Pivoted to Local Open-Source", level=1)
    h2.paragraph_format.space_before = Pt(14)
    h2.paragraph_format.space_after = Pt(6)

    doc.add_paragraph(
        "Our initial project proposal was designed around the proprietary Microsoft enterprise ecosystem "
        "(Power Automate, Azure AI Document Intelligence, Copilot Studio, SharePoint). During preliminary implementation, "
        "we encountered major access blockers: institutional (UTS) tenant security policies prevented registering Azure Entra applications "
        "for Outlook Graph API access, and Copilot Studio required organizational paid credits ($30/month) unavailable under student licenses."
    )
    doc.add_paragraph(
        "Following our supervisor's guidance, we de-scoped the proprietary cloud dependencies and transitioned to a 100% open-source, "
        "local-first architecture. This pivot eliminates external paywalls, guarantees offline execution, and provides deeper technical learning."
    )

    # Comparison Table
    table_data = [
        ["Architecture Layer", "Original Microsoft Cloud Design", "Implemented Local Open-Source Stack", "Why We Made the Change"],
        ["Document Ingestion", "Outlook Connector / Graph API", "Python IMAP (email_listener.py)", "Bypasses Azure AD restrictions; connects to any standard mailbox."],
        ["AI Extraction Engine", "Azure Document Intelligence / Copilot", "Ollama (llama3.2) + Pydantic", "Zero API cost, unlimited inference, strict JSON Schema validation."],
        ["Validation Gate", "AI Builder Confidence Check", "Custom Confidence Scoring Engine", "Prevents hallucinations by cross-checking tokens against source text."],
        ["Storage & Archiving", "SharePoint / Dataverse / OneDrive", "SQLite (workflow_platform.db) + Local Archive", "Lightweight, ACID-compliant, zero external cloud dependencies."],
        ["Monitoring Dashboard", "Power BI Dashboard", "Streamlit (app.py) + Plotly", "Real-time web UI, instant KPI metrics, and Human-in-the-Loop review."]
    ]

    table = doc.add_table(rows=len(table_data), cols=4)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False

    col_widths = [Inches(1.3), Inches(1.7), Inches(1.8), Inches(1.7)]

    for row_idx, row in enumerate(table.rows):
        for col_idx, cell in enumerate(row.cells):
            cell.width = col_widths[col_idx]
            cell.text = table_data[row_idx][col_idx]
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.space_before = Pt(2)

            if row_idx == 0:
                set_cell_background(cell, "0F172A")
                for run in p.runs:
                    run.font.bold = True
                    run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
                    run.font.size = Pt(10)
            else:
                if row_idx % 2 == 1:
                    set_cell_background(cell, "F8FAFC")
                for run in p.runs:
                    run.font.size = Pt(9.5)

    doc.add_paragraph().paragraph_format.space_after = Pt(8)

    # =========================================================================
    # Section 3: End-to-End System Workflow
    # =========================================================================
    h3 = doc.add_heading("3. End-to-End System Workflow", level=1)
    h3.paragraph_format.space_before = Pt(14)
    h3.paragraph_format.space_after = Pt(6)

    doc.add_paragraph(
        "The automated pipeline operates in six continuous stages:"
    )

    flow_steps = [
        "1. Email Monitoring (email_listener.py): Polls Gmail/Outlook via IMAP for messages matching SUBJECT 'invoice', downloads attachments to ./inbox.",
        "2. Text Ingestion (extractor.py): Reads raw document text from PDF files using pypdf.",
        "3. Local AI Extraction (main.py): Queries local Ollama model (llama3.2) with strict Pydantic schemas to output structured JSON.",
        "4. Fallback Heuristics: If the model misses the vendor name, the engine automatically extracts candidates from the document header or filename.",
        "5. Confidence Gate (validator.py): Computes an accuracy score (Threshold: 0.80) to classify records as Validated or NeedsReview.",
        "6. Storage & Archiving (storage.py): Moves files to ./archive, saves records to SQLite, and updates the real-time Streamlit dashboard."
    ]
    for step in flow_steps:
        p = doc.add_paragraph(style='List Bullet')
        p.paragraph_format.space_after = Pt(3)
        p.add_run(step)

    # =========================================================================
    # Section 4: File Structure & Component Roles
    # =========================================================================
    h4 = doc.add_heading("4. Project Structure & Codebase Overview", level=1)
    h4.paragraph_format.space_before = Pt(14)
    h4.paragraph_format.space_after = Pt(6)

    components = [
        ("generate_mock_invoices.py", "Generates realistic synthetic invoice PDFs with line items, tax details, and varied currencies using ReportLab."),
        ("email_listener.py", "Background daemon that polls Gmail via IMAP, filters invoice emails, saves attachments into ./inbox, and triggers main.py."),
        ("models.py", "Defines Pydantic data contracts (ExtractedInvoice, InvoiceItem) to enforce deterministic JSON output from Ollama."),
        ("main.py", "The core pipeline orchestrator executing ingestion, LLM inference, confidence scoring, file archiving, and SQLite persistence."),
        ("workflow_platform.db", "SQLite database storing extracted invoice metadata, confidence scores, raw JSON, and timestamps."),
        ("app.py", "Interactive Streamlit web dashboard providing KPI summary cards, Plotly charts, invoice search, and Human-in-the-Loop approval buttons.")
    ]
    for filename, desc in components:
        p = doc.add_paragraph(style='List Bullet')
        p.paragraph_format.space_after = Pt(3)
        f_run = p.add_run(filename + ": ")
        f_run.bold = True
        f_run.font.color.rgb = RGBColor(0x1E, 0x40, 0xAF)
        p.add_run(desc)

    # =========================================================================
    # Section 5: Teammate Testing Guide
    # =========================================================================
    h5 = doc.add_heading("5. Teammate Testing Guide: How to Run Locally", level=1)
    h5.paragraph_format.space_before = Pt(14)
    h5.paragraph_format.space_after = Pt(6)

    doc.add_paragraph("Follow these steps to set up and run the entire automation pipeline on your local machine:")

    guide_steps = [
        ("Step 1: Install Dependencies: ", "Ensure Ollama is running (`ollama pull llama3.2`) and run:\n`pip3 install ollama pypdf pydantic streamlit pandas plotly python-dotenv reportlab`"),
        ("Step 2: Configure Mailbox (.env): ", "Create a `.env` file with your credentials:\n`GMAIL_USER=your_email@gmail.com`\n`GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx` (Generate via Google Account -> Security -> App Passwords)."),
        ("Step 3: Run Email Listener: ", "Execute `python3 email_listener.py`. Send an email to yourself with 'invoice' in the subject and a PDF attachment."),
        ("Step 4: Launch Web Dashboard: ", "In a separate terminal tab, run `python3 -m streamlit run app.py` and navigate to `http://localhost:8501` to view live extraction results.")
    ]
    for title, desc in guide_steps:
        p = doc.add_paragraph(style='List Bullet')
        p.paragraph_format.space_after = Pt(4)
        t_run = p.add_run(title)
        t_run.bold = True
        p.add_run(desc)

    # =========================================================================
    # Section 6: Next Steps & Technical Roadmap
    # =========================================================================
    h6 = doc.add_heading("6. Next Steps & Technical Roadmap", level=1)
    h6.paragraph_format.space_before = Pt(14)
    h6.paragraph_format.space_after = Pt(6)

    next_items = [
        ("OCR Integration for Scanned PDFs: ", "Currently, pypdf handles native digital PDFs. We will integrate `pytesseract` and `pdf2image` to automatically parse image-based scanned receipts."),
        ("Database Deduplication: ", "Implement upsert logic to prevent duplicate processing of the same invoice number across multiple runs."),
        ("Custom Fine-Tuned Local Models: ", "Evaluate specialized smaller vision/extraction models (e.g., Qwen2.5-VL) for complex multi-page invoice layouts.")
    ]
    for bold_title, detail in next_items:
        p = doc.add_paragraph(style='List Bullet')
        p.paragraph_format.space_after = Pt(3)
        p.add_run(bold_title).bold = True
        p.add_run(detail)

    # Save Word Document
    doc.save(output_filename)
    print(f"[*] Successfully created Word Document: {os.path.abspath(output_filename)}")

if __name__ == "__main__":
    create_briefing_docx("Enterprise_AI_Workflow_Briefing.docx")