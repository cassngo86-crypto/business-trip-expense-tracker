import os
import sys
import base64
import json
import re
import urllib.request
from dotenv import load_dotenv
from groq import Groq
import pytesseract
from PIL import Image
import io

load_dotenv()

# Adaptive OS detection strategy for cloud deployment
if sys.platform.startswith('win'):
    # Local Windows development environment path
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
else:
    # Linux cloud environment path (Streamlit Cloud / AWS / Heroku)
    pytesseract.pytesseract.tesseract_cmd = '/usr/bin/tesseract'


def get_exchange_rate(from_currency: str, to_currency: str = "SGD") -> float:
    from_currency = from_currency.upper().strip()
    to_currency = to_currency.upper().strip()
    if from_currency == to_currency:
        return 1.0

    api_key = os.environ.get("EXCHANGERATE_API_KEY")
    if api_key:
        try:
            url = f"https://v6.exchangerate-api.com/v6/{api_key}/latest/{from_currency}"
            with urllib.request.urlopen(url, timeout=5) as response:
                data = json.loads(response.read().decode())
                if data.get("result") == "success":
                    return float(data["conversion_rates"].get(to_currency, 1.0))
        except Exception:
            pass

    fallbacks = {"USD": 1.34, "EUR": 1.45, "MYR": 0.31, "JPY": 0.0088}
    return fallbacks.get(from_currency, 1.0)


def infer_category(full_text: str, merchant: str = "") -> str:
    """Classifies category based on keyword matching across merchant & body text."""
    combined = f"{merchant} {full_text}".upper()

    category_map = {
        "Healthcare": ["POLYCLINIC", "SUBSIDY", "CHARGES", "CLINIC", "INVESTIGATIONS", "HEALTHCARE", "DOCTOR", "HOSPITAL", "PHARMACY", "SINGHEALTH", "NATIONAL HEALTHCARE"],
        "Dining": ["RESTAURANT", "CAFÉ", "CAFE", "STARBUCKS", "MCDONALD", "KFC", "FOOD", "DINING", "BISTRO", "BAR", "COFFEE", "BAKERY", "EATERY"],
        "Transport": ["GRAB", "TAXI", "UBER", "COMFORT", "SMRT", "TRANSIT", "AIRPORT", "SINGAPORE AIRLINES", "SHELL", "PETROL", "PARKING", "SMRT", "TBT"],
        "Grocery": ["FAIRPRICE", "COLD STORAGE", "SHENG SIONG", "GIANT", "SUPERMARKET", "GROCERY", "DON DON DONKI"],
        "Lodging": ["HOTEL", "HOSTEL", "INN", "SUITES", "AIRBNB", "RESORT", "LODGING", "STAY"],
        "Utilities": ["TELECOM", "SINGTEL", "STARHUB", "M1", "ELECTRIC", "WATER", "POWER", "UTILITIES"]
    }

    for cat, keywords in category_map.items():
        if any(kw in combined for kw in keywords):
            return cat

    return "Miscellaneous"


def parse_total_amount(text: str) -> float:
    """Regex pipeline to accurately extract total amount from receipt text."""
    # Specific patterns target common receipt keywords
    amt_patterns = [
        r'(?:AFTER\s*GOVT\s*SUBSIDY|NETT\s*PAYABLE|TOTAL\s*AMT|GRAND\s*TOTAL|TOTAL\s*DUE|AMOUNT\s*DUE|TOTAL|NET)[:\s]*[\$\¥\€\£]?\s*([0-9]+[\.\,][0-9]{2})',
        r'(?:SGD|USD|EUR|MYR|JPY)\s*[\$]?\s*([0-9]+[\.\,][0-9]{2})'
    ]

    clean_text = text.upper()
    for pattern in amt_patterns:
        match = re.search(pattern, clean_text)
        if match:
            try:
                val_str = match.group(1).replace(',', '.')
                return float(val_str)
            except ValueError:
                continue

    # Fallback: find all standard price patterns ($xx.xx) and pick the maximum value
    all_amounts = re.findall(r'[\$\s]([0-9]+\.[0-9]{2})\b', text)
    if all_amounts:
        try:
            return max([float(a) for a in all_amounts])
        except ValueError:
            pass

    return 0.0


def fallback_local_ocr(image_bytes: bytes) -> dict:
    """
    Secure Local Backup: Uses Tesseract OCR with enhanced regex parsing logic
    for date, category, and total amount extraction.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        full_text = pytesseract.image_to_string(img)
        lines = [line.strip() for line in full_text.split('\n') if line.strip()]

        # 1. Merchant Extraction (Default to first non-empty line if not matched)
        org = lines[0] if lines else "Unknown Merchant"
        if any(k in full_text.upper() for k in ["NATIONAL HEALTHCARE", "POLYCLINICS", "HOUGANG POLYCLINIC"]):
            org = "National Healthcare Group Polyclinics"
        elif "SINGHEALTH" in full_text.upper():
            org = "SingHealth Polyclinics"
        elif "FAIRPRICE" in full_text.upper():
            org = "FairPrice Group"

        # 2. Date Extraction
        dt = "2026-07-14"
        date_match = re.search(r'(\d{2})[/.-](\d{2})[/.-](\d{4})', full_text)
        if date_match:
            dt = f"{date_match.group(3)}-{date_match.group(2)}-{date_match.group(1)}"
        else:
            iso_match = re.search(r'(\d{4})[/.-](\d{2})[/.-](\d{2})', full_text)
            if iso_match:
                dt = f"{iso_match.group(1)}-{iso_match.group(2)}-{iso_match.group(3)}"

        # 3. Total Amount Extraction
        amount = parse_total_amount(full_text)

        # 4. Category Classification
        cat = infer_category(full_text, org)

        return {
            "organization": org,
            "original_amount": amount,
            "currency": "SGD",
            "total_amount": amount,
            "date": dt,
            "category": cat
        }
    except Exception as e:
        print(f"Fallback extraction processing error: {e}")
        return {
            "organization": "Unknown Merchant",
            "original_amount": 0.0,
            "currency": "SGD",
            "total_amount": 0.0,
            "date": "2026-07-14",
            "category": "Miscellaneous"
        }


def extract_receipt_data(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
    """
    Multi-modal parser with cloud vision processing and an isolated local backup block.
    """
    if not image_bytes:
        return fallback_local_ocr(image_bytes)

    client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
    base64_image = base64.b64encode(image_bytes).decode('utf-8')
    data_url = f"data:{mime_type};base64,{base64_image}"

    prompt = """
    Scan this invoice/receipt image to extract exactly 5 specific data fields. 
    Output ONLY a raw, unformatted JSON object. Do not wrap in markdown blocks like ```json.
    
    Target Schema:
    {"organization": "Merchant Name", "original_amount": 0.00, "currency": "SGD", "date": "YYYY-MM-DD", "category": "Healthcare"}
    
    Processing Rules:
    - organization: Top prominent corporate entity or clinic identity name.
    - original_amount: Final total payable float value (e.g. Grand Total, Nett Payable, Total Due, or Total Amt After Subsidy).
    - category: Choose strictly from [Dining, Grocery, Transport, Utilities, Lodging, Healthcare, Miscellaneous].
    - date: Transaction date formatted as YYYY-MM-DD.
    """

    try:
        completion = client.chat.completions.create(
            model="llama-3.2-11b-vision-preview",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}}
                ]
            }],
            temperature=0.1
        )

        raw_content = completion.choices[0].message.content.strip()
        json_match = re.search(r'\{.*\}', raw_content, re.DOTALL)
        parsed_json = json.loads(json_match.group(0)) if json_match else json.loads(raw_content)

        org = parsed_json.get("organization") or "Unknown Merchant"
        orig_amount = float(parsed_json.get("original_amount") or 0.0)
        currency = str(parsed_json.get("currency") or "SGD").upper().strip()
        dt = parsed_json.get("date") or "2026-07-14"
        cat = parsed_json.get("category") or "Miscellaneous"

        # If Groq missed crucial fields, fallback to local OCR
        if org == "Unknown Merchant" or orig_amount == 0.0 or cat == "Miscellaneous":
            fallback_res = fallback_local_ocr(image_bytes)
            if orig_amount == 0.0:
                orig_amount = fallback_res["original_amount"]
            if org == "Unknown Merchant":
                org = fallback_res["organization"]
            if cat == "Miscellaneous":
                cat = fallback_res["category"]

        rate = get_exchange_rate(currency, "SGD")
        return {
            "organization": org,
            "original_amount": orig_amount,
            "currency": currency,
            "total_amount": round(orig_amount * rate, 2),
            "date": dt,
            "category": cat
        }
    except Exception:
        return fallback_local_ocr(image_bytes)
