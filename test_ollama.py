import json
import os

import ollama
from pypdf import PdfReader


def test_single_pdf(pdf_path: str, model_name: str = "llama3.2"):
    """Read a single PDF, send it to Ollama, and print the raw extraction."""
    print(f"=== Reading PDF: {pdf_path} ===")

    reader = PdfReader(pdf_path)
    raw_text = ""
    for page in reader.pages:
        raw_text += page.extract_text() or ""

    print("\n--- [Raw Text extracted from PDF] ---")
    print(raw_text.strip())
    print("-------------------------------------\n")

    print(f"Sending text to Ollama ({model_name})... Please wait...")

    prompt = f"""
You are an expert Document Intelligence AI.
Extract the invoice details from the following text accurately.

Return ONLY a valid JSON object with these fields:
- invoice_number
- vendor_name
- date
- due_date
- currency
- total_amount
- items

For items, return a list of objects with:
- description
- quantity
- unit_price
- total

Text:
\"\"\"{raw_text}\"\"\"
"""

    response = ollama.chat(
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
        format="json",
        options={"temperature": 0.0},
    )

    raw_json_output = response["message"]["content"]

    print("\n--- [Ollama JSON Output] ---")
    print(raw_json_output)
    print("----------------------------\n")

    parsed_data = json.loads(raw_json_output)

    print("--- [Parsed Fields] ---")
    print(f"Parsed Vendor: {parsed_data.get('vendor_name')}")
    print(f"Parsed Invoice #: {parsed_data.get('invoice_number')}")
    print(f"Parsed Date: {parsed_data.get('date')}")
    print(f"Parsed Due Date: {parsed_data.get('due_date')}")
    print(
        f"Parsed Total Amount: {parsed_data.get('total_amount')} "
        f"{parsed_data.get('currency')}"
    )
    print(f"Parsed Line Items Count: {len(parsed_data.get('items', []))}")


if __name__ == "__main__":
    archive_dir = "./archive"

    if not os.path.exists(archive_dir):
        print("No archive folder found. Please run main.py first.")
    else:
        sample_files = [
            os.path.join(archive_dir, file_name)
            for file_name in os.listdir(archive_dir)
            if file_name.endswith(".pdf")
        ]

        if sample_files:
            test_single_pdf(sample_files[0])
        else:
            print("No archived PDF found. Please run main.py first.")