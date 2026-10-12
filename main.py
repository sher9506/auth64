import base64
import json
import os
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv

# .env fayldan o'qish (lokal testlar uchun)
load_dotenv()

app = FastAPI(title="NotebookLM Account Proxy Service", version="1.0.0")

# Render muhitidan maxfiy kalitni olish
RENDER_SECRET = os.getenv("RENDER_SECRET")
if not RENDER_SECRET:
    raise ValueError("DIQQAT: RENDER_SECRET muhit o'zgaruvchisi topilmadi!")

# --- PYDANTIC SCHEMALAR ---
class AuthPayload(BaseModel):
    auth_base64: str

class NotebookCreatePayload(BaseModel):
    auth_base64: str
    notebook_name: str

class AddSourcePayload(BaseModel):
    auth_base64: str
    notebook_id: str
    url: str

class AskPayload(BaseModel):
    auth_base64: str
    notebook_id: str
    query: str
    conversation_id: str = None


# --- YORDAMCHI FUNKSIYA ---
def get_playwright_context(p, auth_base64: str):
    """Base64 kodlangan auth ni ochib, talabaning brauzer sessiyasini tiklaydi"""
    try:
        json_str = base64.b64decode(auth_base64).decode('utf-8')
        storage_state = json.loads(json_str)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Yaroqsiz Base64 auth formati: {str(e)}")
    
    # Headless rejimda brauzer ochamiz
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(storage_state=storage_state)
    return browser, context


# --- ENDPOINTLAR ---

@app.get("/")
def health_check():
    return {"status": "ok", "service": "Render NLM Proxy is running smoothly"}

@app.post("/api/verify-auth")
def verify_auth(payload: AuthPayload, x_render_auth: str = Header(None)):
    """Talabaning Google auth sessiyasi yaroqli ekanligini tekshiradi"""
    if x_render_auth != RENDER_SECRET:
        raise HTTPException(status_code=403, detail="Ruxsat etilmadi: noto'g'ri RENDER_SECRET")
    
    with sync_playwright() as p:
        try:
            browser, context = get_playwright_context(p, payload.auth_base64)
            page = context.new_page()
            page.goto("https://notebooklm.google.com/", timeout=30000)
            
            # Agar Google login sahifasiga o'tib ketsa, demak sessiya eskirgan
            if "accounts.google.com" in page.url:
                browser.close()
                return {"valid": False, "message": "Google sessiyasi eskirgan yoki yaroqsiz."}
            
            browser.close()
            return {"valid": True, "message": "Google sessiyasi faol va to'g'ri ishlayapti!"}
        except Exception as e:
            return {"valid": False, "error": str(e)}

@app.post("/api/create-notebook")
def create_notebook(payload: NotebookCreatePayload, x_render_auth: str = Header(None)):
    """Talabaning shaxsiy akkaunti nomidan yangi daftar (notebook) ochadi"""
    if x_render_auth != RENDER_SECRET:
        raise HTTPException(status_code=403, detail="Ruxsat etilmadi")
    
    with sync_playwright() as p:
        browser, context = get_playwright_context(p, payload.auth_base64)
        page = context.new_page()
        try:
            page.goto("https://notebooklm.google.com/", timeout=30000)
            
            # Bu yerda NotebookLM da daftar yaratish avtomatizatsiya skripti bajariladi
            # Hozircha sinov uchun mock_id qaytariladi
            notebook_id = "mock_notebook_id_xyz789"
            
            browser.close()
            return {"success": True, "notebook_id": notebook_id, "name": payload.notebook_name}
        except Exception as e:
            browser.close()
            raise HTTPException(status_code=500, detail=f"Daftar ochishda xatolik: {str(e)}")

@app.post("/api/add-source")
def add_source(payload: AddSourcePayload, x_render_auth: str = Header(None)):
    """Daftarga yangi manba (masalan, lex.uz URL) qo'shadi"""
    if x_render_auth != RENDER_SECRET:
        raise HTTPException(status_code=403, detail="Ruxsat etilmadi")
    
    with sync_playwright() as p:
        browser, context = get_playwright_context(p, payload.auth_base64)
        page = context.new_page()
        try:
            # URL qo'shish logikasi
            browser.close()
            return {"success": True, "message": f"Manba muvaffaqiyatli qo'shildi: {payload.url}"}
        except Exception as e:
            browser.close()
            raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/ask")
def ask_question(payload: AskPayload, x_render_auth: str = Header(None)):
    """Daftarga savol yuboradi va follow-up (suhbatni davom ettirish) ni boshqaradi"""
    if x_render_auth != RENDER_SECRET:
        raise HTTPException(status_code=403, detail="Ruxsat etilmadi")
    
    with sync_playwright() as p:
        browser, context = get_playwright_context(p, payload.auth_base64)
        page = context.new_page()
        try:
            # Savol berish va javobni olish logikasi
            answer = "Bu talabaning shaxsiy NotebookLM limitidan kelgan javob matni."
            conversation_id = payload.conversation_id or "conv_id_abc123"
            
            browser.close()
            return {
                "success": True,
                "answer": answer,
                "conversation_id": conversation_id
            }
        except Exception as e:
            browser.close()
            raise HTTPException(status_code=500, detail=str(e))
