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

# Adaptive OS detection strategy
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
        except Exception:
            pass

    fallbacks = {"USD": 1.34, "EUR": 1.45, "MYR": 0.31, "JPY": 0.0088}
    return fallbacks.get(from_currency, 1.0)


def infer_category(text: str) -> str:
    clean_text = text.upper()
    keywords = {
        "Healthcare": ["POLYCLINIC", "CLINIC", "HEALTHCARE", "DOCTOR", "HOSPITAL", "PHARMACY", "SINGHEALTH", "NATIONAL HEALTHCARE", "PATIENT", "MEDICINE", "CONSULTATION", "NHGP"],
        "Dining": ["RESTAURANT", "CAFÉ", "CAFE", "STARBUCKS", "MCDONALD", "KFC", "FOOD", "DINING", "BISTRO", "BAR", "COFFEE", "BAKERY", "EATERY", "SUBWAY", "MEAL", "KOI", "LIHO"],
        "Transport": ["GRAB", "TAXI", "UBER", "COMFORT", "SMRT", "TRANSIT", "AIRPORT", "SHELL", "PETROL", "PARKING", "BUS", "LTA", "RIDE", "EZ-LINK"],
        "Grocery": ["FAIRPRICE", "COLD STORAGE", "SHENG SIONG", "GIANT", "SUPERMARKET", "GROCERY", "DON DON DONKI", "NTUC", "MART", "WATSONS", "GUARDIAN"],
        "Lodging": ["HOTEL", "HOSTEL", "INN", "SUITES", "AIRBNB", "RESORT", "LODGING", "STAY", "MOTEL"],
        "Utilities": ["TELECOM", "SINGTEL", "STARHUB", "M1", "ELECTRIC", "WATER", "POWER", "UTILITIES", "SP GROUP"]
    }
    for category, term_list in keywords.items():
        if any(term in clean_text for term in term_list):
            return category
    return "Miscellaneous"


def parse_total_amount(text: str) -> float:
    clean_text = text.upper()
    
    # 1. Direct Regex for explicitly labeled totals
    patterns = [
        r'(?:NETT\s*PAYABLE|TOTAL\s*AMT|GRAND\s*TOTAL|TOTAL\s*DUE|AMOUNT\s*DUE|TOTAL|NET)\s*[:\$]?\s*([0-9]+\.[0-9]{2})',
        r'(?:SGD|USD|EUR|MYR|JPY)\s*[\$]?\s*([0-9]+\.[0-9]{2})'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, clean_text)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                continue

    # 2. Extract all decimal numbers ($XX.XX) and choose max (receipt total is almost always the max value)
    amounts = re.findall(r'\b[0-9]+\.[0-9]{2}\b', text)
    if amounts:
        try:
            valid_floats = [float(a) for a in amounts if float(a) < 10000.0]
            if valid_floats:
                return max(valid_floats)
        except ValueError:
            pass
            
    return 0.0


def fallback_local_ocr(image_bytes: bytes) -> dict:
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = ImageOps.exif_transpose(img).convert('L')
        img = ImageEnhance.Contrast(img).enhance(1.8)
        
        full_text = pytesseract.image_to_string(img)
        lines = [line.strip() for line in full_text.split('\n') if line.strip()]

        org = lines[0] if lines else "Unknown Merchant"
        amount = parse_total_amount(full_text)
        cat = infer_category(full_text)

        dt = "2026-07-20"
        date_match = re.search(r'(\d{2})[/.-](\d{2})[/.-](\d{4})', full_text)
        if date_match:
            dt = f"{date_match.group(3)}-{date_match.group(2)}-{date_match.group(1)}"

        return {
            "organization": org,
            "original_amount": amount,
            "currency": "SGD",
            "total_amount": amount,
            "date": dt,
            "category": cat
        }
    except Exception as e:
        print(f"Fallback Error: {e}")
        return {
            "organization": "Unknown Merchant",
            "original_amount": 0.0,
            "currency": "SGD",
            "total_amount": 0.0,
            "date": "2026-07-20",
            "category": "Miscellaneous"
        }


def extract_receipt_data(image_input, mime_type: str = "image/jpeg") -> dict:
    """
    Safely converts image_input (UploadedFile, BytesIO, or bytes) into raw bytes
    and executes extraction.
    """
    # 1. Safely extract raw bytes from Streamlit UploadedFile or BytesIO
    if hasattr(image_input, "getvalue"):
        image_bytes = image_input.getvalue()
    elif hasattr(image_input, "read"):
        image_input.seek(0)
        image_bytes = image_input.read()
    elif isinstance(image_input, bytes):
        image_bytes = image_input
    else:
        image_bytes = b""

    if not image_bytes:
        return {
            "organization": "Unknown Merchant",
            "original_amount": 0.0,
            "currency": "SGD",
            "total_amount": 0.0,
            "date": "2026-07-20",
            "category": "Miscellaneous"
        }

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return fallback_local_ocr(image_bytes)

    client = Groq(api_key=api_key)
    base64_image = base64.b64encode(image_bytes).decode('utf-8')
    data_url = f"data:{mime_type};base64,{base64_image}"

    prompt = """
    Extract information from this receipt/invoice and return ONLY a single JSON object.
    Keys required:
    - organization: Merchant/Clinic/Entity name string.
    - original_amount: Final total amount paid as float.
    - currency: 3-letter currency code (e.g. SGD, USD).
    - date: Date string as YYYY-MM-DD.
    - category: Exactly one of [Dining, Grocery, Transport, Utilities, Lodging, Healthcare, Miscellaneous].
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
            response_format={"type": "json_object"},
            reasoning_format="hidden",
            temperature=0.1
        )

        raw_response = completion.choices[0].message.content.strip()
        parsed_json = json.loads(raw_response)

        org = parsed_json.get("organization") or "Unknown Merchant"
        orig_amount = float(parsed_json.get("original_amount") or 0.0)
        currency = str(parsed_json.get("currency") or "SGD").upper().strip()
        dt = parsed_json.get("date") or "2026-07-20"
        cat = parsed_json.get("category") or "Miscellaneous"

        if orig_amount == 0.0 or cat == "Miscellaneous":
            local_res = fallback_local_ocr(image_bytes)
            if orig_amount == 0.0:
                orig_amount = local_res["original_amount"]
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
        print(f"Groq API Exception: {e}")
        return fallback_local_ocr(image_bytes)
