import base64
import json
import os
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from playwright.sync_api import sync_playwright

app = FastAPI(title="NotebookLM Account Proxy Service")

# Render uchun maxfiy kalit (Sayt va Render bir-birini tanishi uchun)
RENDER_SECRET = os.getenv("RENDER_SECRET", "super-secret-render-token")

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

def get_playwright_context(p, auth_base64: str):
    try:
        json_str = base64.b64decode(auth_base64).decode('utf-8')
        storage_state = json.loads(json_str)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid Base64 auth data: {str(e)}")
    
    # Headless brauzer ochamiz va talabaning sessiyasini yuklaymiz
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(storage_state=storage_state)
    return browser, context

@app.get("/")
def health_check():
    return {"status": "ok", "service": "Render NLM Proxy is running"}

@app.post("/api/verify-auth")
def verify_auth(payload: AuthPayload, x_render_auth: str = Header(None)):
    if x_render_auth != RENDER_SECRET:
        raise HTTPException(status_code=403, detail="Unauthorized Render access")
    
    with sync_playwright() as p:
        try:
            browser, context = get_playwright_context(p, payload.auth_base64)
            page = context.new_page()
            page.goto("https://notebooklm.google.com/", timeout=30000)
            
            # Agar login sahifasiga yo'naltirib yuborsa, demak auth yaroqsiz
            if "accounts.google.com" in page.url:
                browser.close()
                return {"valid": False, "message": "Google session expired or invalid."}
            
            browser.close()
            return {"valid": True, "message": "Auth is valid and active!"}
        except Exception as e:
            return {"valid": False, "error": str(e)}

@app.post("/api/create-notebook")
def create_notebook(payload: NotebookCreatePayload, x_render_auth: str = Header(None)):
    if x_render_auth != RENDER_SECRET:
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    with sync_playwright() as p:
        browser, context = get_playwright_context(p, payload.auth_base64)
        page = context.new_page()
        try:
            page.goto("https://notebooklm.google.com/", timeout=30000)
            # Bu yerda NotebookLM interfeysi orqali daftar yaratish avtomatizatsiyasi yoziladi
            # (Elementlarga bosish, nom berish va notebook_id ni olish)
            # Hozircha namunaviy javob:
            notebook_id = "mock_notebook_id_123" 
            browser.close()
            return {"success": True, "notebook_id": notebook_id}
        except Exception as e:
            browser.close()
            raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/ask")
def ask_question(payload: AskPayload, x_render_auth: str = Header(None)):
    if x_render_auth != RENDER_SECRET:
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    with sync_playwright() as p:
        browser, context = get_playwright_context(p, payload.auth_base64)
        page = context.new_page()
        try:
            # Berilgan notebook_id ga kirib savol berish va follow-up qilish mantiqi
            answer = "Bu NotebookLM dan kelgan test javob (talabaning shaxsiy limiti orqali)."
            conversation_id = payload.conversation_id or "mock_conv_id_456"
            
            browser.close()
            return {
                "success": True,
                "answer": answer,
                "conversation_id": conversation_id
            }
        except Exception as e:
            browser.close()
            raise HTTPException(status_code=500, detail=str(e))
