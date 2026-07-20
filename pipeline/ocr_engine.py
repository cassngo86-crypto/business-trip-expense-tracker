import os
import sys
import base64
import json
import re
import urllib.request
from dotenv import load_dotenv
from groq import Groq
import pytesseract
from PIL import Image, ImageEnhance, ImageOps
import io

load_dotenv()

# Adaptive OS detection strategy for cloud deployment
if sys.platform.startswith('win'):
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
else:
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
        except Exception as e:
            print(f"Exchange Rate API error: {e}")

    fallbacks = {"USD": 1.34, "EUR": 1.45, "MYR": 0.31, "JPY": 0.0088}
    return fallbacks.get(from_currency, 1.0)


def preprocess_mobile_image(image_bytes: bytes) -> Image.Image:
    """Fixes camera orientation (EXIF) and enhances image contrast for OCR."""
    img = Image.open(io.BytesIO(image_bytes))
    
    # Auto-rotate based on EXIF tag (critical for photos taken on mobile phones)
    img = ImageOps.exif_transpose(img)
    
    # Convert to Grayscale
    img = img.convert('L')
    
    # Enhance contrast to clean up mobile shadows
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.0)
    
    return img


def infer_category(full_text: str, merchant: str = "") -> str:
    """Flexible keyword engine for robust category assignment."""
    combined = f"{merchant} {full_text}".upper()

    category_map = {
        "Healthcare": ["POLYCLINIC", "SUBSIDY", "CHARGES", "CLINIC", "INVESTIGATIONS", "HEALTHCARE", "DOCTOR", "HOSPITAL", "PHARMACY", "SINGHEALTH", "NATIONAL HEALTHCARE", "MEDICAL"],
        "Dining": ["RESTAURANT", "CAFÉ", "CAFE", "STARBUCKS", "MCDONALD", "KFC", "FOOD", "DINING", "BISTRO", "BAR", "COFFEE", "BAKERY", "EATERY", "MCD", "SUBWAY"],
        "Transport": ["GRAB", "TAXI", "UBER", "COMFORT", "SMRT", "TRANSIT", "AIRPORT", "SINGAPORE AIRLINES", "SHELL", "PETROL", "PARKING", "TBT", "BUS", "LTA"],
        "Grocery": ["FAIRPRICE", "COLD STORAGE", "SHENG SIONG", "GIANT", "SUPERMARKET", "GROCERY", "DON DON DONKI", "NTUC"],
        "Lodging": ["HOTEL", "HOSTEL", "INN", "SUITES", "AIRBNB", "RESORT", "LODGING", "STAY"],
        "Utilities": ["TELECOM", "SINGTEL", "STARHUB", "M1", "ELECTRIC", "WATER", "POWER", "UTILITIES"]
    }

    for cat, keywords in category_map.items():
        if any(kw in combined for kw in keywords):
            return cat

    return "Miscellaneous"


def parse_total_amount(text: str) -> float:
    """Robust pattern search for receipt total amount."""
    clean_text = text.upper()

    # Search pattern for standard receipt total keywords
    amt_patterns = [
        r'(?:AFTER\s*GOVT\s*SUBSIDY|NETT\s*PAYABLE|TOTAL\s*AMT|GRAND\s*TOTAL|TOTAL\s*DUE|AMOUNT\s*DUE|TOTAL|NET)\b[:\s]*[\$\¥\€\£]?\s*([0-9]+[\.\,][0-9]{2})',
        r'(?:SGD|USD|EUR|MYR|JPY)\s*[\$]?\s*([0-9]+[\.\,][0-9]{2})',
        r'TOTAL[\s\S]*?([0-9]+\.[0-9]{2})'
    ]

    for pattern in amt_patterns:
        match = re.search(pattern, clean_text)
        if match:
            try:
                val_str = match.group(1).replace(',', '.')
                return float(val_str)
            except ValueError:
                continue

    # Fallback: Extract all floating-point values ($xx.xx format) and select the maximum
    all_amounts = re.findall(r'([0-9]+\.[0-9]{2})', text)
    if all_amounts:
        try:
            return max([float(a) for a in all_amounts])
        except ValueError:
            pass

    return 0.0


def fallback_local_ocr(image_bytes: bytes) -> dict:
    """Local Tesseract parsing fallback."""
    try:
        img = preprocess_mobile_image(image_bytes)
        full_text = pytesseract.image_to_string(img)
        
        print(f"--- RAW TESSERACT OCR OUTPUT ---\n{full_text}\n------------------------------")

        lines = [line.strip() for line in full_text.split('\n') if line.strip()]

        # Merchant detection
        org = lines[0] if lines else "Unknown Merchant"
        if any(k in full_text.upper() for k in ["NATIONAL HEALTHCARE", "POLYCLINICS", "HOUGANG POLYCLINIC"]):
            org = "National Healthcare Group Polyclinics"
        elif "SINGHEALTH" in full_text.upper():
            org = "SingHealth Polyclinics"
        elif "FAIRPRICE" in full_text.upper():
            org = "FairPrice Group"

        # Date detection
        dt = "2026-07-20"
        date_match = re.search(r'(\d{2})[/.-](\d{2})[/.-](\d{4})', full_text)
        if date_match:
            dt = f"{date_match.group(3)}-{date_match.group(2)}-{date_match.group(1)}"
        else:
            iso_match = re.search(r'(\d{4})[/.-](\d{2})[/.-](\d{2})', full_text)
            if iso_match:
                dt = f"{iso_match.group(1)}-{iso_match.group(2)}-{iso_match.group(3)}"

        amount = parse_total_amount(full_text)
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
            "date": "2026-07-20",
            "category": "Miscellaneous"
        }


def extract_receipt_data(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
    """Primary extraction using Groq vision API with fallbacks."""
    if not image_bytes:
        return fallback_local_ocr(image_bytes)

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print("GROQ_API_KEY missing, using local OCR fallback.")
        return fallback_local_ocr(image_bytes)

    client = Groq(api_key=api_key)
    base64_image = base64.b64encode(image_bytes).decode('utf-8')
    data_url = f"data:{mime_type};base64,{base64_image}"

    prompt = """
    Scan this receipt/invoice image carefully to extract 5 structured values:
    Return ONLY a single valid raw JSON object. Do NOT wrap in ```json ``` blocks.

    JSON Format:
    {
      "organization": "Merchant or Clinic Name",
      "original_amount": 0.00,
      "currency": "SGD",
      "date": "YYYY-MM-DD",
      "category": "Dining"
    }

    Rules:
    - original_amount: Final total amount paid (e.g. Total, Grand Total, Nett Payable). Must be float.
    - category: Must be strictly one of: [Dining, Grocery, Transport, Utilities, Lodging, Healthcare, Miscellaneous].
    - date: Format YYYY-MM-DD.
    """

    try:
        completion = client.chat.completions.create(
            model="qwen/qwen3.6-27b",
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
        print(f"--- GROQ RAW RESPONSE ---\n{raw_content}\n-------------------------")

        json_match = re.search(r'\{.*\}', raw_content, re.DOTALL)
        parsed_json = json.loads(json_match.group(0)) if json_match else json.loads(raw_content)

        org = parsed_json.get("organization") or "Unknown Merchant"
        orig_amount = float(parsed_json.get("original_amount") or 0.0)
        currency = str(parsed_json.get("currency") or "SGD").upper().strip()
        dt = parsed_json.get("date") or "2026-07-20"
        cat = parsed_json.get("category") or "Miscellaneous"

        # If Groq returns empty/partial values, patch with local OCR fallback
        if org == "Unknown Merchant" or orig_amount == 0.0 or cat == "Miscellaneous":
            local_res = fallback_local_ocr(image_bytes)
            if orig_amount == 0.0:
                orig_amount = local_res["original_amount"]
            if org == "Unknown Merchant":
                org = local_res["organization"]
            if cat == "Miscellaneous":
                cat = local_res["category"]

        rate = get_exchange_rate(currency, "SGD")
        return {
            "organization": org,
            "original_amount": orig_amount,
            "currency": currency,
            "total_amount": round(orig_amount * rate, 2),
            "date": dt,
            "category": cat
        }
    except Exception as e:
        print(f"Groq API Error: {e}. Falling back to Tesseract OCR...")
        return fallback_local_ocr(image_bytes)
