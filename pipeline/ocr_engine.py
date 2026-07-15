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
    # On Linux, once installed, it registers globally to the system path
    pytesseract.pytesseract.tesseract_cmd = '/usr/bin/tesseract'

def get_exchange_rate(from_currency, to_currency="SGD"):
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
                    return data["conversion_rates"].get(to_currency, 1.0)
        except Exception:
            pass
    fallbacks = {"USD": 1.34, "EUR": 1.45, "MYR": 0.31, "JPY": 0.0088}
    return fallbacks.get(from_currency, 1.0)

def fallback_local_ocr(image_bytes: bytes) -> dict:
    """
    Secure Local Backup: Uses standalone Tesseract execution to read layouts
    line-by-line, bypassing Windows Application Control DLL blocks completely.
    """
    try:
        # Open the image directly out of the memory stream safely
        img = Image.open(io.BytesIO(image_bytes))
        
        # Extract plain layout text lines natively
        full_text = pytesseract.image_to_string(img)
        
        # 1. Parse Organization Name
        org = "Polyclinic / Medical Centre"
        if any(k in full_text.upper() for k in ["NATIONAL HEALTHCARE", "POLYCLINICS", "HOUGANG POLYCLINIC"]):
            org = "National Healthcare Group Polyclinics"
        elif "SINGHEALTH" in full_text.upper():
            org = "SingHealth Polyclinics"
        elif "FAIRPRICE" in full_text.upper():
            org = "FairPrice Group"
            
        # 2. Parse Date (DD/MM/YYYY or YYYY-MM-DD)
        dt = "2026-07-08" 
        date_match = re.search(r'(\d{2})/(\d{2})/(\d{4})', full_text)
        if date_match:
            dt = f"{date_match.group(3)}-{date_match.group(2)}-{date_match.group(1)}"
            
        # 3. Parse Final Net Total Values
        amount = 0.0
        amt_patterns = [
            r'(?:After Govt Subsidy|Nett Payable|Total Amt).*?\$?\s*(\d+\.\d{2})',
            r'(?:Total Due|Amount Due|Total).*?\$?\s*(\d+\.\d{2})'
        ]
        for pattern in amt_patterns:
            match = re.search(pattern, full_text, re.IGNORECASE)
            if match:
                amount = float(match.group(1))
                break
                
        # 4. Enforce structural classification rules
        cat = "Healthcare" if any(k in full_text.upper() for k in ["POLYCLINIC", "SUBSIDY", "CHARGES", "CLINIC", "INVESTIGATIONS"]) else "Miscellaneous"
        
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
        return {"organization": "Unknown Merchant", "original_amount": 0.0, "currency": "SGD", "total_amount": 0.0, "date": "2026-07-14", "category": "Miscellaneous"}

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
    - original_amount: Final net payable float value associated with 'Nett Payable' or 'Total Amt (After Govt Subsidy)'.
    - category: Choose strictly from [Dining, Grocery, Transport, Utilities, Entertainment, Healthcare, Miscellaneous]. Set to 'Healthcare' if terms like Polyclinic, Subsidy, patient or clinic exist.
    """
    
    try:
        completion = client.chat.completions.create(
            model="qwen/qwen3.6-27b", 
            messages=[{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": data_url}}]}],
            temperature=0.1
        )
        
        raw_content = completion.choices[0].message.content.strip()
        json_match = re.search(r'\{.*\}', raw_content, re.DOTALL)
        parsed_json = json.loads(json_match.group(0)) if json_match else json.loads(raw_content)
        
        org = parsed_json.get("organization") or "Unknown Merchant"
        orig_amount = float(parsed_json.get("original_amount") or 0.0)
        
        if org == "Unknown Merchant" or orig_amount == 0.0:
            return fallback_local_ocr(image_bytes)
            
        currency = str(parsed_json.get("currency") or "SGD").upper().strip()
        dt = parsed_json.get("date") or "2026-07-14"
        cat = parsed_json.get("category") or "Miscellaneous"
        
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