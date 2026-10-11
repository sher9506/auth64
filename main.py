"""
NLM user-auth servisi (test). Auth har so'rovda keladi (X-NLM-Auth sarlavhasi yoki tanada `auth`),
vaqtinchalik papkada ochiladi, ish tugagach papka o'chadi, yangilangan auth javob bilan qaytadi.
Render'da auth saqlanmaydi va logga yozilmaydi.

Ishga tushirish:
    RENDER_SECRET=test123 uvicorn main:app --port 8000
"""
import asyncio
import base64
import json
import os
import re
import secrets
import shutil
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from notebooklm import NotebookLMClient, NotebookNotFoundError

API_KEY = os.environ.get("RENDER_SECRET", "")
GLOBAL = asyncio.Semaphore(int(os.environ.get("GLOBAL_LIMIT", "4")))   # serverda bir vaqtda nechta ish
MIN_GAP = float(os.environ.get("MIN_GAP_SEC", "3"))                    # bir akkauntning ketma-ket ishlari orasidagi pauza
ASK_TIMEOUT = int(os.environ.get("ASK_TIMEOUT", "420"))
JOB_TTL = 3600
PREFIX = "FF-"                                                          # faqat shu nomli daftarlar o'chiriladi
FILES = ("storage_state.json", "master_token.json")
JOBS, TASKS, LOCKS, LAST = {}, set(), {}, {}

RULES = (
    "\n\nMUHIM QOIDA: Javobni xulosa bilan tugating. Oxirida savol bermang, "
    "internetdan qidirishni yoki qo'shimcha yordam taklif qilmang. "
    "Javobni to'liq o'zbek LOTIN yozuvida yozing, kirill harflari bo'lmasin."
)
META_RE = re.compile(
    r"notebook\s?lm|system\s+prompt|ignore\s+(all\s+)?(previous|prior)|"
    r"tizim\s+(prompt|ko'rsatma)|nechta\s+(ta\s+)?(manba|hujjat)|manbalar\s+soni|"
    r"qaysi\s+(ai|model|tizim)|sen\s+kimsan|siz\s+kimsiz", re.I)
META_MSG = "Iltimos, faqat kazus yoki huquqiy savol kiriting."
AUTH_WORDS = ("auth", "login", "expired", "401", "403", "unauthorized", "sign in", "cookie")
QUOTA_WORDS = ("429", "quota", "limit exceeded", "rate limit", "resource exhausted")


# ───────────── auth paketi ─────────────
def unpack(auth_b64):
    """Ikki format: oddiy storage_state.json (base64) yoki {"files": {nom: base64}} paketi (master_token bilan)."""
    try:
        raw = base64.b64decode(auth_b64)
        obj = json.loads(raw)
    except Exception:
        raise HTTPException(400, "auth formati noto'g'ri")
    if isinstance(obj, dict) and "files" in obj:
        files = {k: base64.b64decode(v) for k, v in obj["files"].items() if k in FILES}
    else:
        files = {"storage_state.json": raw}
    if "storage_state.json" not in files:
        raise HTTPException(400, "auth paketida storage_state.json yo'q")
    return files


def pack(d, bundle):
    if bundle:
        files = {n: base64.b64encode((d / n).read_bytes()).decode()
                 for n in FILES if (d / n).exists()}
        return base64.b64encode(json.dumps({"files": files}).encode()).decode()
    return base64.b64encode((d / "storage_state.json").read_bytes()).decode()


@asynccontextmanager
async def client_for(auth_b64):
    files = unpack(auth_b64)
    bundle = len(files) > 1
    d = Path(tempfile.mkdtemp(prefix="nlm_"))
    box = {}
    try:
        for n, b in files.items():
            p = d / n
            p.write_bytes(b)
            os.chmod(p, 0o600)
        async with NotebookLMClient.from_storage(str(d / "storage_state.json"), timeout=float(os.environ.get("NLM_RPC_TIMEOUT", "180")), backend=os.environ.get("NLM_BACKEND") or None) as c:
            yield c, box
        box["auth"] = pack(d, bundle)      # kutubxona yangilagan bo'lsa, yangisi qaytadi
    finally:
        shutil.rmtree(d, ignore_errors=True)   # diskda qoldirilmaydi


# ───────────── yordamchilar ─────────────
def require_key(x_api_key: str = Header("")):
    if not API_KEY:
        raise HTTPException(503, "RENDER_SECRET sozlanmagan")
    if not secrets.compare_digest(x_api_key.encode(), API_KEY.encode()):
        raise HTTPException(401, "kalit noto'g'ri")


def clean(t):
    t = re.sub(r"^Answer:\s*", "", (t or "").strip())
    t = re.sub(r"[*#`]+", "", t)
    t = re.sub(r"(?m)^\s*-{3,}\s*$", "", t)
    t = re.sub(r"(?<=\w)[\u02bb\u02bc\u2018\u2019`\u00b4](?=\w)", "'", t)
    t = re.sub(r"(?i)notebook\s?lm|gemini\s+notebook", "", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def classify(e):
    low = str(e).lower().replace("_", " ")
    if any(w in low for w in QUOTA_WORDS):
        return "quota"
    if any(w in low for w in AUTH_WORDS):
        return "auth_expired"
    return "error"


async def gap(acc):
    w = MIN_GAP - (time.time() - LAST.get(acc, 0))
    if w > 0:
        await asyncio.sleep(w)
    LAST[acc] = time.time()


class JobReq(BaseModel):
    question: str = Field(min_length=3, max_length=12000)
    urls: List[str] = []
    title: str = "Suhbat"
    notebook_id: Optional[str] = None        # bor bo'lsa, shu daftarda davom etadi
    source_ids: Optional[List[str]] = None
    conversation_id: Optional[str] = None
    keep: bool = False                       # true bo'lsa, daftar o'chirilmaydi (suhbat davom etadi)
    auth: Optional[str] = None               # sarlavha katta bo'lsa, shu yerda
    account_id: Optional[str] = None


class DelReq(BaseModel):
    notebook_id: str
    auth: Optional[str] = None
    account_id: Optional[str] = None


# ───────────── ish ─────────────
async def run_job(jid, auth, acc, r):
    job = JOBS[jid]
    box = {}
    try:
        lock = LOCKS.setdefault(acc, asyncio.Lock())   # bir akkauntda bir vaqtda bitta ish
        async with GLOBAL:
            async with lock:
                job["status"] = "running"
                async with client_for(auth) as (c, box):
                    await gap(acc)
                    nb_id, created = r.notebook_id, False
                    if not nb_id:
                        nb = await c.notebooks.create(PREFIX + r.title[:80])
                        nb_id, created = nb.id, True
                    job["notebook_id"] = nb_id
                    try:
                        ids = list(r.source_ids or [])
                        for u in r.urls:
                            s = await c.sources.add_url(nb_id, u, wait=True)
                            ids.append(s.id)
                        job["source_ids"] = ids
                        res = await asyncio.wait_for(
                            c.chat.ask(nb_id, r.question + RULES,
                                       source_ids=ids or None,
                                       conversation_id=r.conversation_id),
                            ASK_TIMEOUT)
                    except BaseException:
                        if created and not r.keep:     # xato bo'lsa, yetim daftar qolmasin
                            try:
                                await c.notebooks.delete(nb_id)
                            except Exception:
                                pass
                        raise
                    if created and not r.keep:
                        await c.notebooks.delete(nb_id)
                        job["notebook_deleted"] = True
        job.update(status="done", answer=clean(res.answer),
                   conversation_id=res.conversation_id,
                   turn=getattr(res, "turn_number", None), auth=box.get("auth"))
    except Exception as e:
        print("[job %s] %s: %s" % (jid, type(e).__name__, str(e)[:200]), flush=True)   # auth hech qachon chop etilmaydi
        job.update(status="error", error_code=classify(e),
                   error="Javob tayyorlanmadi. Qayta urinib ko'ring.")
    job["finished"] = time.time()


app = FastAPI(title="NLM user-auth service")


@app.post("/api/jobs", dependencies=[Depends(require_key)])
async def create_job(r: JobReq, x_nlm_auth: str = Header(""), x_account_id: str = Header("")):
    auth = x_nlm_auth or r.auth
    acc = x_account_id or r.account_id
    if not auth or not acc:
        raise HTTPException(400, "auth va account_id kerak")
    if META_RE.search(r.question):
        raise HTTPException(422, META_MSG)
    now = time.time()
    for k in [k for k, v in JOBS.items() if v.get("finished") and now - v["finished"] > JOB_TTL]:
        JOBS.pop(k, None)
    jid = uuid.uuid4().hex
    JOBS[jid] = {"status": "queued", "created": now}
    t = asyncio.create_task(run_job(jid, auth, acc, r))
    TASKS.add(t)
    t.add_done_callback(TASKS.discard)
    return {"job_id": jid, "status": "queued"}


@app.get("/api/jobs/{jid}", dependencies=[Depends(require_key)])
async def get_job(jid: str):
    job = JOBS.get(jid)
    if not job:
        raise HTTPException(404, "ish topilmadi")
    out = dict(job)
    if job["status"] in ("done", "error"):
        job["auth"] = None      # yangilangan auth bir marta beriladi, xotirada qolmaydi
    return out


@app.post("/api/delete", dependencies=[Depends(require_key)])
async def delete(r: DelReq, x_nlm_auth: str = Header(""), x_account_id: str = Header("")):
    auth = x_nlm_auth or r.auth
    acc = x_account_id or r.account_id
    if not auth or not acc:
        raise HTTPException(400, "auth va account_id kerak")
    lock = LOCKS.setdefault(acc, asyncio.Lock())
    async with GLOBAL:
        async with lock:
            async with client_for(auth) as (c, box):
                try:
                    nb = await c.notebooks.get(r.notebook_id)
                except NotebookNotFoundError:
                    nb = None
                if nb is not None:
                    if not (nb.title or "").startswith(PREFIX):
                        raise HTTPException(403, "bu daftar FF- bilan boshlanmaydi, tegilmaydi")
                    await c.notebooks.delete(r.notebook_id)
    return {"deleted": r.notebook_id, "auth": box.get("auth")}


@app.get("/ping")
async def ping():
    return {"ok": True}
