import asyncio, json, secrets, time, re, unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import httpx
from fastapi import (
    FastAPI, HTTPException, Query, Request, UploadFile, File, Form,
    WebSocket, WebSocketDisconnect, Depends, Body, Response, JSONResponse,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from pydantic import BaseModel

from config import *
from parser import _clean_name, _extract_year, _clean_category
from info import fetch_movie_info, fetch_series_info
from home import build_home_async, _slugify
from proxy import build_slug_map, get_by_slug, get_by_id, stream_proxy, stats as proxy_stats, total_slugs, limpar_tudo as limpar_proxy
import tmdb
from db import criar_tabelas, sincronizar, SessionLocal, get_db, engine, Base, Filme as FilmeDB, Serie as SerieDB, Canal as CanalDB, Slide as SlideDB
from websocket_manager import manager
from notificacoes import router as notificacoes_router
from firebase_client import get_firestore

import billing
from billing import Cliente, Plano, ApiKey, UsoMensal, Fatura, seed_planos
from auth import (
    verificar_api_key, get_cliente_sessao,
    gerar_chave, hash_chave, hash_senha, verificar_senha,
    criar_token_sessao, assinar_stream, validar_stream, ano_mes_atual,
)
import pagamentos

app = FastAPI(title="IPTV Organizer Pro · Multi-Provedor", version="14.0.0")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"], expose_headers=["*"])
app.include_router(notificacoes_router)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

_FILMES, _SERIES, _CANAIS = [], [], []
_HOME_CACHE, _HOME_CACHE_TS = {}, 0.0
_SYNC_ATIVO, _SYNC_LOGS = False, []
_IMPORT_ATIVO, _IMPORT_LOGS = False, []

def _stats() -> dict: return {"total_filmes": len(_FILMES), "total_series": len(_SERIES), "total_canais": len(_CANAIS)}

def _load_json(path, default=None):
    if not path.exists(): return default if default is not None else []
    try: return json.loads(path.read_text(encoding="utf-8"))
    except: return default if default is not None else []

def load_index_from_disk() -> bool:
    global _FILMES, _SERIES, _CANAIS
    if not (FILMES_FILE.exists() and SERIES_FILE.exists() and CANAIS_FILE.exists()): return False
    try:
        _FILMES = _load_json(FILMES_FILE); _SERIES = _load_json(SERIES_FILE); _CANAIS = _load_json(CANAIS_FILE)
        return True
    except: return False

def _save_all():
    FILMES_FILE.write_text(json.dumps(_FILMES, ensure_ascii=False), encoding="utf-8")
    SERIES_FILE.write_text(json.dumps(_SERIES, ensure_ascii=False), encoding="utf-8")
    CANAIS_FILE.write_text(json.dumps(_CANAIS, ensure_ascii=False), encoding="utf-8")

def _rebuild_slugs(): build_slug_map({"filmes": _FILMES, "series": _SERIES, "canais": _CANAIS})

def paginate(items, page, size):
    total = len(items); tp = (total + size - 1) // size if size else 0; st = (page - 1) * size
    return {"page": page, "page_size": size, "total_items": total, "total_pages": tp, "has_next": page < tp, "has_prev": page > 1, "items": items[st:st + size]}

def _normalizar_busca(texto: str) -> str:
    if not texto: return ""
    t = unicodedata.normalize('NFKD', texto).encode('ascii', 'ignore').decode('ascii').lower()
    t = re.sub(r'[^a-z0-9\s]', ' ', t)
    t = re.sub(r'\b(the|os|as|o|a|um|uma|de|do|da)\b', ' ', t)
    return " ".join(t.split())

def _card_app(item, tipo=None):
    t = tipo or item.get("tipo") or "filme"
    tipo_app = "movie" if t == "filme" else ("series" if t == "serie" else "channel")
    return {"id": str(item.get("id")), "title": item.get("titulo") or "", "poster": item.get("capa"), "type": tipo_app, "year": item.get("ano"), "slug": _slugify(item.get("titulo") or "", item.get("ano")), "titulo": item.get("titulo"), "capa": item.get("capa"), "tipo": t, "ano": item.get("ano"), "added_by_name": item.get("added_by_name"), "added_by_uid": item.get("added_by_uid")}

@app.on_event("startup")
async def startup():
    criar_tabelas()
    seed_planos()
    if load_index_from_disk():
        _rebuild_slugs(); sincronizar(_FILMES, _SERIES, _CANAIS); print(f"[✓] Carregado: {_stats()}")

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        await ws.send_text(json.dumps({"evento": "conectado", "dados": {"stats": _stats()}}))
        while True:
            data = await ws.receive_text()
            if data == "ping": await ws.send_text("pong")
    except: await manager.disconnect(ws)

@app.get("/")
def root():
    p = BASE_DIR / "painel.html"
    return FileResponse(p) if p.exists() else {"service": "Vênus API"}

@app.get("/admin")
def admin_panel(request: Request):
    p = BASE_DIR / "index.html"
    return FileResponse(p) if p.exists() else {"erro": "index.html não encontrado"}

# ==========================================================
# PROVEDORES IPTV (CRUD VIA FIRESTORE)
# ==========================================================
@app.get("/admin/provedores")
def listar_provedores():
    db = get_firestore()
    if not db: raise HTTPException(500, "Firebase indisponível")
    docs = db.collection("provedores").stream()
    items = []
    for doc in docs:
        d = doc.to_dict()
        d["id"] = doc.id
        items.append(d)
    return {"items": items}

@app.post("/admin/provedores")
def criar_provedor(nome: str = Body(...), host: str = Body(...), username: str = Body(...), password: str = Body(...)):
    db = get_firestore()
    if not db: raise HTTPException(500, "Firebase indisponível")
    doc_ref = db.collection("provedores").document()
    doc_ref.set({
        "nome": nome, "host": host.rstrip("/"), "username": username, "password": password,
        "criado_em": datetime.utcnow().isoformat()
    })
    return {"ok": True, "id": doc_ref.id}

@app.delete("/admin/provedores/{pid}")
def deletar_provedor(pid: str):
    db = get_firestore()
    if not db: raise HTTPException(500, "Firebase indisponível")
    db.collection("provedores").document(pid).delete()
    return {"ok": True}

# ==========================================================
# IPTV BUSCA E IMPORTAÇÃO (MULTI-PROVEDOR)
# ==========================================================
class IPTVAddPayload(BaseModel):
    id: str; tipo: str; titulo: str; capa: Optional[str] = None; ano: Optional[int] = None; url: Optional[str] = None; added_by_name: Optional[str] = None; added_by_uid: Optional[str] = None
class IPTVLotePayload(BaseModel):
    items: list[IPTVAddPayload]

@app.get("/admin/iptv/buscar")
async def admin_iptv_buscar(q: str, tipo: str = "filme", provedor_id: str = Query(...), db: Session = Depends(get_db)):
    fdb = get_firestore()
    if not fdb: raise HTTPException(500, "Firebase indisponível")
    
    doc = fdb.collection("provedores").document(provedor_id).get()
    if not doc.exists: raise HTTPException(404, "Provedor não encontrado")
    prov = doc.to_dict()
    
    q_norm = _normalizar_busca(q); action = "get_vod_streams" if tipo == "filme" else "get_series"
    
    async with httpx.AsyncClient(timeout=60) as client:
        try: data = (await client.get(f"{prov['host']}/player_api.php?username={prov['username']}&password={prov['password']}&action={action}")).json()
        except: raise HTTPException(500, "Erro no provedor.")
    
    if not isinstance(data, list): raise HTTPException(500, "Formato inválido.")
    existentes = {f.iptv_id for f in db.query(FilmeDB).all()} if tipo == "filme" else {s.iptv_id for s in db.query(SerieDB).all()}
    resultados = []
    
    for item in data:
        if not isinstance(item, dict): continue
        sid = str(item.get("stream_id") or item.get("series_id") or ""); nome = item.get("name") or ""
        if not sid or q_norm not in _normalizar_busca(nome): continue
        url_stream = f"{prov['host']}/movie/{prov['username']}/{prov['password']}/{sid}.mp4" if tipo == "filme" else ""
        resultados.append({"id": sid, "titulo": _clean_name(nome), "capa": item.get("stream_icon") or item.get("cover"), "ano": _extract_year(nome), "tipo": tipo, "ja_existe": sid in existentes, "url": url_stream})
    return {"items": resultados[:200]}

@app.post("/admin/iptv/adicionar")
async def admin_iptv_adicionar(payload: IPTVAddPayload, db: Session = Depends(get_db)):
    global _FILMES, _SERIES
    if payload.tipo == "filme":
        if not db.query(FilmeDB).filter(FilmeDB.iptv_id == payload.id).first():
            db.add(FilmeDB(iptv_id=payload.id, titulo=payload.titulo, capa=payload.capa, ano=payload.ano, categoria="Manual", url=payload.url or "", added_by_name=payload.added_by_name, added_by_uid=payload.added_by_uid))
            db.commit(); _FILMES.append({"id": payload.id, "tipo": "filme", "titulo": payload.titulo, "capa": payload.capa, "ano": payload.ano, "categoria": "Manual", "url": payload.url, "added_by_name": payload.added_by_name, "added_by_uid": payload.added_by_uid})
    elif payload.tipo == "serie":
        if not db.query(SerieDB).filter(SerieDB.iptv_id == payload.id).first():
            db.add(SerieDB(iptv_id=payload.id, titulo=payload.titulo, capa=payload.capa, ano=payload.ano, categoria="Manual", added_by_name=payload.added_by_name, added_by_uid=payload.added_by_uid))
            db.commit(); _SERIES.append({"id": payload.id, "tipo": "serie", "titulo": payload.titulo, "capa": payload.capa, "ano": payload.ano, "categoria": "Manual", "added_by_name": payload.added_by_name, "added_by_uid": payload.added_by_uid})
    _rebuild_slugs(); _save_all(); await manager.broadcast("catalogo_sincronizado", _stats()); return {"ok": True}

@app.post("/admin/iptv/adicionar-lote")
async def admin_iptv_adicionar_lote(payload: IPTVLotePayload, db: Session = Depends(get_db)):
    global _FILMES, _SERIES
    adicionados = 0
    for item in payload.items:
        if item.tipo == "filme" and not db.query(FilmeDB).filter(FilmeDB.iptv_id == item.id).first():
            db.add(FilmeDB(iptv_id=item.id, titulo=item.titulo, capa=item.capa, ano=item.ano, categoria="Manual", url=item.url or "", added_by_name=item.added_by_name, added_by_uid=item.added_by_uid))
            _FILMES.append({"id": item.id, "tipo": "filme", "titulo": item.titulo, "capa": item.capa, "ano": item.ano, "categoria": "Manual", "url": item.url, "added_by_name": item.added_by_name, "added_by_uid": item.added_by_uid}); adicionados += 1
        elif item.tipo == "serie" and not db.query(SerieDB).filter(SerieDB.iptv_id == item.id).first():
            db.add(SerieDB(iptv_id=item.id, titulo=item.titulo, capa=item.capa, ano=item.ano, categoria="Manual", added_by_name=item.added_by_name, added_by_uid=item.added_by_uid))
            _SERIES.append({"id": item.id, "tipo": "serie", "titulo": item.titulo, "capa": item.capa, "ano": item.ano, "categoria": "Manual", "added_by_name": item.added_by_name, "added_by_uid": item.added_by_uid}); adicionados += 1
    db.commit(); _rebuild_slugs(); _save_all(); await manager.broadcast("catalogo_sincronizado", _stats()); return {"ok": True, "adicionados": adicionados}

async def _importar_lote_bg(provedor_id: str, tipo: str, quantidade: int):
    global _IMPORT_ATIVO, _IMPORT_LOGS, _FILMES, _SERIES, _CANAIS
    _IMPORT_ATIVO = True; _IMPORT_LOGS = [f"[•] Iniciando importação de até {quantidade if quantidade > 0 else 'TODOS os'} {tipo}(s)..."]
    
    fdb = get_firestore()
    doc = fdb.collection("provedores").document(provedor_id).get()
    if not doc.exists:
        _IMPORT_LOGS.append("[!] Provedor não encontrado no banco.")
        _IMPORT_ATIVO = False
        return
    prov = doc.to_dict()
    
    db = SessionLocal()
    try:
        action = "get_vod_streams" if tipo == "filme" else ("get_series" if tipo == "serie" else "get_live_streams")
        cat_action = "get_vod_categories" if tipo == "filme" else ("get_series_categories" if tipo == "serie" else "get_live_categories")
        async with httpx.AsyncClient(timeout=120) as client:
            r_data, r_cat = await asyncio.gather(client.get(f"{prov['host']}/player_api.php?username={prov['username']}&password={prov['password']}&action={action}"), client.get(f"{prov['host']}/player_api.php?username={prov['username']}&password={prov['password']}&action={cat_action}"))
            data = r_data.json(); cats = r_cat.json()
        
        if not isinstance(data, list): _IMPORT_LOGS.append("[!] Erro: Provedor inválido."); return
        cat_map = {str(c.get("category_id")): _clean_category(c.get("category_name") or "Geral") for c in cats if isinstance(c, dict)} if isinstance(cats, list) else {}
        _IMPORT_LOGS.append(f"[✓] Lista baixada. Total: {len(data)}")
        
        existentes = {f.iptv_id for f in db.query(FilmeDB).all()} if tipo == "filme" else ({s.iptv_id for s in db.query(SerieDB).all()} if tipo == "serie" else {c.iptv_id for c in db.query(CanalDB).all()})
        adicionados = 0
        
        for item in data:
            if not isinstance(item, dict): continue
            if quantidade > 0 and adicionados >= quantidade: break
            sid = str(item.get("stream_id") or item.get("series_id") or "")
            if not sid or sid in existentes: continue
            nome = _clean_name(item.get("name") or "")
            if tipo == "filme":
                url_stream = f"{prov['host']}/movie/{prov['username']}/{prov['password']}/{sid}.{item.get('container_extension', 'mp4')}"
                db.add(FilmeDB(iptv_id=sid, titulo=nome, capa=item.get("stream_icon"), ano=_extract_year(item.get("name")), categoria=cat_map.get(str(item.get("category_id")), "Geral"), url=url_stream))
                _FILMES.append({"id": sid, "tipo": "filme", "titulo": nome, "capa": item.get("stream_icon"), "ano": _extract_year(item.get("name")), "categoria": cat_map.get(str(item.get("category_id")), "Geral"), "url": url_stream})
            elif tipo == "serie":
                db.add(SerieDB(iptv_id=sid, titulo=nome, capa=item.get("cover"), ano=_extract_year(item.get("releaseDate")), categoria=cat_map.get(str(item.get("category_id")), "Geral")))
                _SERIES.append({"id": sid, "tipo": "serie", "titulo": nome, "capa": item.get("cover"), "ano": _extract_year(item.get("releaseDate")), "categoria": cat_map.get(str(item.get("category_id")), "Geral")})
            else:
                url_stream = f"{prov['host']}/live/{prov['username']}/{prov['password']}/{sid}.ts"
                db.add(CanalDB(iptv_id=sid, titulo=nome, capa=item.get("stream_icon"), categoria=cat_map.get(str(item.get("category_id")), "Geral"), url=url_stream))
                _CANAIS.append({"id": sid, "tipo": "canal", "titulo": nome, "capa": item.get("stream_icon"), "categoria": cat_map.get(str(item.get("category_id")), "Geral"), "url": url_stream})
            adicionados += 1; existentes.add(sid)
            if adicionados % 100 == 0: db.commit(); _IMPORT_LOGS.append(f"[•] {adicionados} adicionados...")
        db.commit(); _rebuild_slugs(); _save_all(); _IMPORT_LOGS.append(f"[✓] Importação concluída! {adicionados} novos.")
        await manager.broadcast("catalogo_sincronizado", _stats())
    except Exception as e: _IMPORT_LOGS.append(f"[!] Erro: {e}")
    finally: db.close(); _IMPORT_ATIVO = False

@app.post("/admin/iptv/importar-lote-bg")
async def importar_lote_bg(provedor_id: str = Query(...), tipo: str = Query(...), quantidade: int = Query(...)):
    global _IMPORT_ATIVO
    if _IMPORT_ATIVO: return {"ok": False, "msg": "Já existe uma importação."}
    asyncio.create_task(_importar_lote_bg(provedor_id, tipo, quantidade)); return {"ok": True, "msg": "Iniciada."}

@app.get("/admin/iptv/importar-lote-bg/status")
async def importar_lote_bg_status(): return {"ativo": _IMPORT_ATIVO, "logs": _IMPORT_LOGS, "stats": _stats()}

# ==========================================================
# ROTAS DE CATÁLOGO E SISTEMA (MANTIDAS DO ORIGINAL)
# ==========================================================
@app.get("/status")
async def status(): return {"stats": _stats(), "sync_ativo": _SYNC_ATIVO, "proxy": {"slugs_carregados": total_slugs(), **proxy_stats()}}

@app.get("/home")
async def home(force: bool = False):
    global _HOME_CACHE, _HOME_CACHE_TS
    if not _FILMES and not _SERIES: return {"hero": [], "colecoes": [], "trailers": [], "stats": _stats()}
    if not force and _HOME_CACHE and (time.time() - _HOME_CACHE_TS) < 300: return _HOME_CACHE
    _HOME_CACHE = await build_home_async({"filmes": _FILMES, "series": _SERIES, "stats": _stats()}, force_hero=force)
    _HOME_CACHE_TS = time.time(); return _HOME_CACHE

@app.get("/filmes")
def listar_filmes(page: int = Query(1), size: int = Query(30), categoria: str | None = None):
    filmes = _FILMES
    if categoria: filmes = [f for f in filmes if f["categoria"].lower() == categoria.lower()]
    resp = paginate(filmes, page, size); resp["items"] = [_card_app(it, "filme") for it in resp["items"]]; return resp

@app.get("/series")
def listar_series(page: int = Query(1), size: int = Query(30), categoria: str | None = None):
    series = _SERIES
    if categoria: series = [s for s in series if s["categoria"].lower() == categoria.lower()]
    resp = paginate(series, page, size); resp["items"] = [_card_app(it, "serie") for it in resp["items"]]; return resp

@app.get("/canais")
def listar_canais(page: int = Query(1), size: int = Query(50), categoria: str | None = None):
    canais = _CANAIS
    if categoria: canais = [c for c in canais if c["categoria"].lower() == categoria.lower()]
    resp = paginate(canais, page, size); resp["items"] = [_card_app(it, "canal") for it in resp["items"]]; return resp

@app.get("/buscar")
def buscar(q: str, tipo: str | None = None, page: int = Query(1), size: int = Query(30)):
    q_raw = q.lower().strip(); q_norm = _normalizar_busca(q); resultados = []
    def match(item):
        tit = item.get("titulo") or ""
        return q_raw in tit.lower() or (q_norm and q_norm in _normalizar_busca(tit))
    if tipo in (None, "filme", "movie"): resultados += [_card_app(f, "filme") for f in _FILMES if match(f)]
    if tipo in (None, "serie", "series"): resultados += [_card_app(s, "serie") for s in _SERIES if match(s)]
    if tipo in (None, "canal", "channel"): resultados += [_card_app(c, "canal") for c in _CANAIS if match(c)]
    return paginate(resultados, page, size)

@app.get("/categorias")
def categorias(): return {"filmes": sorted({f["categoria"] for f in _FILMES if f.get("categoria")}), "series": sorted({s["categoria"] for s in _SERIES if s.get("categoria")}), "canais": sorted({c["categoria"] for c in _CANAIS if c.get("categoria")})}

@app.get("/info/filme/{filme_id}")
async def info_filme(filme_id: str): return await fetch_movie_info(filme_id)

@app.get("/info/serie/{serie_id}")
async def info_serie(serie_id: str): return await fetch_series_info(serie_id)

@app.get("/admin/filmes")
def admin_filmes(q: str | None = None, page: int = 1, size: int = 30, db: Session = Depends(get_db)):
    filmes = _FILMES
    if q: filmes = [f for f in filmes if q.lower() in (f.get("titulo") or "").lower()]
    resp = paginate(filmes, page, size)
    itens_db = {f.iptv_id: f.id for f in db.query(FilmeDB).all()}
    for it in resp["items"]: it["db_id"] = itens_db.get(str(it.get("id")), 0); it["editado"] = False
    return resp

@app.get("/admin/series")
def admin_series(q: str | None = None, page: int = 1, size: int = 30, db: Session = Depends(get_db)):
    series = _SERIES
    if q: series = [s for s in series if q.lower() in (s.get("titulo") or "").lower()]
    resp = paginate(series, page, size)
    itens_db = {s.iptv_id: s.id for s in db.query(SerieDB).all()}
    for it in resp["items"]: it["db_id"] = itens_db.get(str(it.get("id")), 0)
    return resp

@app.get("/admin/canais")
def admin_canais(q: str | None = None, page: int = 1, size: int = 30, db: Session = Depends(get_db)):
    canais = _CANAIS
    if q: canais = [c for c in canais if q.lower() in (c.get("titulo") or "").lower()]
    resp = paginate(canais, page, size)
    itens_db = {c.iptv_id: c.id for c in db.query(CanalDB).all()}
    for it in resp["items"]: it["db_id"] = itens_db.get(str(it.get("id")), 0)
    return resp

@app.get("/admin/item/{tipo}/{item_id}")
def admin_item(tipo: str, item_id: str, db: Session = Depends(get_db)):
    if tipo == "filme": it = db.query(FilmeDB).filter(FilmeDB.iptv_id == item_id).first()
    elif tipo == "serie": it = db.query(SerieDB).filter(SerieDB.iptv_id == item_id).first()
    elif tipo == "canal": it = db.query(CanalDB).filter(CanalDB.iptv_id == item_id).first()
    else: raise HTTPException(400, "inválido")
    if it: return {c.name: getattr(it, c.name) for c in it.__table__.columns}
    lista = _FILMES if tipo == "filme" else (_SERIES if tipo == "serie" else _CANAIS)
    mem_item = next((x for x in lista if str(x.get("id")) == str(item_id)), None)
    if not mem_item: raise HTTPException(404, "Não encontrado")
    return mem_item

@app.delete("/admin/item/{tipo}/{item_id}")
def deletar_item(tipo: str, item_id: str, db: Session = Depends(get_db)):
    global _FILMES, _SERIES, _CANAIS
    if tipo == "filme":
        it = db.query(FilmeDB).filter(FilmeDB.iptv_id == item_id).first()
        if it: db.delete(it); db.commit()
        _FILMES[:] = [f for f in _FILMES if str(f.get("id")) != str(item_id)]
    elif tipo == "serie":
        it = db.query(SerieDB).filter(SerieDB.iptv_id == item_id).first()
        if it: db.delete(it); db.commit()
        _SERIES[:] = [s for s in _SERIES if str(s.get("id")) != str(item_id)]
    elif tipo == "canal":
        it = db.query(CanalDB).filter(CanalDB.iptv_id == item_id).first()
        if it: db.delete(it); db.commit()
        _CANAIS[:] = [c for c in _CANAIS if str(c.get("id")) != str(item_id)]
    _rebuild_slugs(); _save_all(); return {"ok": True}

async def _salvar_upload(arq: Optional[UploadFile], sub: str) -> Optional[str]:
    if not arq or not arq.filename: return None
    ext = Path(arq.filename).suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"): raise HTTPException(400, "inválido")
    nome = f"{secrets.token_hex(8)}{ext}"; destino = UPLOAD_DIR / sub / nome
    with destino.open("wb") as f:
        while chunk := await arq.read(1024 * 1024): f.write(chunk)
    return f"/uploads/{sub}/{nome}"

def _upsert_db_item(db, tipo, item_id, form_data, uploads):
    global _FILMES, _SERIES, _CANAIS
    obj = None
    if tipo == "filme": obj = db.query(FilmeDB).filter(FilmeDB.iptv_id == item_id).first()
    elif tipo == "serie": obj = db.query(SerieDB).filter(SerieDB.iptv_id == item_id).first()
    elif tipo == "canal": obj = db.query(CanalDB).filter(CanalDB.iptv_id == item_id).first()
    if not obj:
        if tipo == "filme": obj = FilmeDB(iptv_id=item_id, url="")
        elif tipo == "serie": obj = SerieDB(iptv_id=item_id)
        elif tipo == "canal": obj = CanalDB(iptv_id=item_id, url="")
        db.add(obj)
    for k, v in form_data.items():
        if v is not None: setattr(obj, k, v)
    for k, v in uploads.items():
        if v: setattr(obj, k, v)
    if hasattr(obj, 'editado'): obj.editado = True
    db.commit(); db.refresh(obj)
    lista = _FILMES if tipo == "filme" else (_SERIES if tipo == "serie" else _CANAIS)
    for it in lista:
        if str(it.get("id")) == str(item_id):
            for k, v in form_data.items():
                if v is not None: it[k] = v
            for k, v in uploads.items():
                if v: it[k] = v
            break
    _save_all(); _rebuild_slugs(); return obj

@app.put("/admin/filme/{item_id}")
async def editar_filme(item_id: str, titulo: Optional[str] = Form(None), ano: Optional[int] = Form(None), categoria: Optional[str] = Form(None), sinopse: Optional[str] = Form(None), url: Optional[str] = Form(None), capa: Optional[UploadFile] = File(None), banner: Optional[UploadFile] = File(None), logo: Optional[UploadFile] = File(None), db: Session = Depends(get_db)):
    uploads = {"capa": await _salvar_upload(capa, "capas"), "banner": await _salvar_upload(banner, "banners"), "logo": await _salvar_upload(logo, "logos")}
    form = {"titulo": titulo, "ano": ano, "categoria": categoria, "sinopse": sinopse, "url": url}
    obj = _upsert_db_item(db, "filme", item_id, form, uploads)
    await manager.broadcast("item_editado", {"tipo": "filme", "id": obj.id, "titulo": obj.titulo})
    return {"ok": True, "id": obj.id, "titulo": obj.titulo}

@app.put("/admin/serie/{item_id}")
async def editar_serie(item_id: str, titulo: Optional[str] = Form(None), ano: Optional[int] = Form(None), categoria: Optional[str] = Form(None), sinopse: Optional[str] = Form(None), capa: Optional[UploadFile] = File(None), banner: Optional[UploadFile] = File(None), logo: Optional[UploadFile] = File(None), db: Session = Depends(get_db)):
    uploads = {"capa": await _salvar_upload(capa, "capas"), "banner": await _salvar_upload(banner, "banners"), "logo": await _salvar_upload(logo, "logos")}
    form = {"titulo": titulo, "ano": ano, "categoria": categoria, "sinopse": sinopse}
    obj = _upsert_db_item(db, "serie", item_id, form, uploads)
    await manager.broadcast("item_editado", {"tipo": "serie", "id": obj.id, "titulo": obj.titulo})
    return {"ok": True, "id": obj.id, "titulo": obj.titulo}

@app.put("/admin/canal/{item_id}")
async def editar_canal(item_id: str, titulo: Optional[str] = Form(None), categoria: Optional[str] = Form(None), url: Optional[str] = Form(None), capa: Optional[UploadFile] = File(None), db: Session = Depends(get_db)):
    uploads = {"capa": await _salvar_upload(capa, "capas")}
    form = {"titulo": titulo, "categoria": categoria, "url": url}
    obj = _upsert_db_item(db, "canal", item_id, form, uploads)
    await manager.broadcast("item_editado", {"tipo": "canal", "id": obj.id, "titulo": obj.titulo})
    return {"ok": True, "id": obj.id, "titulo": obj.titulo}

async def _wipe_all_bg():
    global _SYNC_ATIVO, _FILMES, _SERIES, _CANAIS
    _SYNC_ATIVO = True
    for f in [FILMES_FILE, SERIES_FILE, CANAIS_FILE, META_FILE, NOVIDADES_FILE]:
        if f.exists(): f.unlink()
    for f in CACHE_DIR.glob("slug_map_*.json"): f.unlink()
    if (CACHE_DIR / "hero_cache.json").exists(): (CACHE_DIR / "hero_cache.json").unlink()
    Base.metadata.drop_all(bind=engine, tables=[FilmeDB.__table__, SerieDB.__table__, CanalDB.__table__, SlideDB.__table__])
    Base.metadata.create_all(bind=engine)
    _FILMES.clear(); _SERIES.clear(); _CANAIS.clear(); limpar_proxy()
    await manager.broadcast("catalogo_sincronizado", _stats()); _SYNC_ATIVO = False

@app.post("/admin/wipe-all")
async def wipe_all():
    if _SYNC_ATIVO: return {"ok": False, "msg": "Aguarde."}
    asyncio.create_task(_wipe_all_bg()); return {"ok": True}

@app.get("/admin/force-sync/status")
async def force_sync_status(): return {"ativo": _SYNC_ATIVO, "stats": _stats(), "logs": _SYNC_LOGS}

@app.get("/admin/slider")
def admin_slider_listar(db: Session = Depends(get_db)):
    slides = db.query(SlideDB).order_by(SlideDB.ordem).all(); out = []
    for s in slides:
        ref = None
        if s.ref_tipo == "filme":
            r = db.query(FilmeDB).get(s.ref_id); ref = {"titulo": r.titulo, "capa": r.capa, "ano": r.ano} if r else None
        elif s.ref_tipo == "serie":
            r = db.query(SerieDB).get(s.ref_id); ref = {"titulo": r.titulo, "capa": r.capa, "ano": r.ano} if r else None
        out.append({"id": s.id, "ordem": s.ordem, "ref_tipo": s.ref_tipo, "ref_id": s.ref_id, "titulo": s.titulo or (ref["titulo"] if ref else "?"), "banner": s.banner or (ref.get("capa") if ref else None), "logo": s.logo, "sinopse": s.sinopse, "ref": ref, "ativo": s.ativo})
    return {"total": len(out), "items": out}

@app.post("/admin/slider")
async def admin_slider_adicionar(ref_tipo: str = Form(...), ref_id: str = Form(...), db: Session = Depends(get_db)):
    global _HOME_CACHE_TS, _FILMES, _SERIES
    item_db = None
    if ref_tipo == "filme":
        item_db = db.query(FilmeDB).filter(FilmeDB.iptv_id == ref_id).first()
        if not item_db:
            mem_item = next((x for x in _FILMES if str(x.get("id")) == str(ref_id)), None)
            if mem_item: item_db = FilmeDB(iptv_id=ref_id, titulo=mem_item.get("titulo"), capa=mem_item.get("capa"), ano=mem_item.get("ano"), categoria=mem_item.get("categoria"), url=mem_item.get("url") or ""); db.add(item_db); db.commit(); db.refresh(item_db)
    elif ref_tipo == "serie":
        item_db = db.query(SerieDB).filter(SerieDB.iptv_id == ref_id).first()
        if not item_db:
            mem_item = next((x for x in _SERIES if str(x.get("id")) == str(ref_id)), None)
            if mem_item: item_db = SerieDB(iptv_id=ref_id, titulo=mem_item.get("titulo"), capa=mem_item.get("capa"), ano=mem_item.get("ano"), categoria=mem_item.get("categoria")); db.add(item_db); db.commit(); db.refresh(item_db)
    if not item_db: raise HTTPException(404, "Item não encontrado.")
    ultimo = db.query(SlideDB).order_by(SlideDB.ordem.desc()).first(); proxima = (ultimo.ordem + 1) if ultimo else 0
    novo = SlideDB(ordem=proxima, ref_tipo=ref_tipo, ref_id=item_db.id, ativo=True)
    db.add(novo); db.commit(); db.refresh(novo); _HOME_CACHE_TS = 0
    await manager.broadcast("slider_atualizado", {"acao": "adicionar", "id": novo.id}); return {"ok": True, "id": novo.id}

@app.put("/admin/slider/{slide_id}")
async def admin_slider_editar(slide_id: int, titulo: Optional[str] = Form(None), banner: Optional[str] = Form(None), logo: Optional[str] = Form(None), sinopse: Optional[str] = Form(None), db: Session = Depends(get_db)):
    global _HOME_CACHE_TS
    s = db.query(SlideDB).get(slide_id)
    if not s: raise HTTPException(404, "não encontrado")
    if titulo is not None: s.titulo = titulo or None
    if banner is not None: s.banner = banner or None
    if logo is not None: s.logo = logo or None
    if sinopse is not None: s.sinopse = sinopse or None
    db.commit(); _HOME_CACHE_TS = 0; await manager.broadcast("slider_atualizado", {"acao": "editar", "id": slide_id}); return {"ok": True}

@app.delete("/admin/slider/{slide_id}")
async def admin_slider_remover(slide_id: int, db: Session = Depends(get_db)):
    global _HOME_CACHE_TS
    s = db.query(SlideDB).get(slide_id)
    if not s: raise HTTPException(404, "não encontrado")
    db.delete(s); db.commit(); _HOME_CACHE_TS = 0; await manager.broadcast("slider_atualizado", {"acao": "remover", "id": slide_id}); return {"ok": True}

@app.post("/admin/slider/reordenar")
async def admin_slider_reordenar(ordem: str = Form(...), db: Session = Depends(get_db)):
    global _HOME_CACHE_TS
    try: ids = [int(x.strip()) for x in ordem.split(",") if x.strip()]
    except: raise HTTPException(400, "inválido")
    for i, sid in enumerate(ids):
        s = db.query(SlideDB).get(sid)
        if s: s.ordem = i
    db.commit(); _HOME_CACHE_TS = 0; await manager.broadcast("slider_atualizado", {"acao": "reordenar"}); return {"ok": True}

_EPISODIO_RE = re.compile(r'^(.+?)-t(\d+)-e(\d+)$')

async def _resolver_episodio_sob_demanda(slug: str):
    m = _EPISODIO_RE.match(slug)
    if not m: return None
    base = m.group(1); serie_encontrada = None
    for s in _SERIES:
        if _slugify(s.get("titulo") or "") == base: serie_encontrada = s; break
    if not serie_encontrada: return None
    serie_id = str(serie_encontrada.get("id") or "")
    if not serie_id: return None
    try: await fetch_series_info(serie_id)
    except: return None
    return get_by_slug(slug)

@app.get("/stream/id/{item_id}")
async def proxy_por_id(item_id: str, request: Request):
    cid = item_id
    for ext in (".mp4", ".mkv", ".ts", ".m3u8", ".avi", ".mov"):
        if cid.lower().endswith(ext): cid = cid[:-len(ext)]; break
    item = get_by_id(cid)
    if not item: raise HTTPException(404, "não encontrado")
    return await stream_proxy(item["url"], request)

@app.get("/stream/{slug_path:path}")
async def proxy_por_slug(slug_path: str, request: Request):
    slug = slug_path
    for ext in (".mp4", ".mkv", ".ts", ".m3u8", ".avi", ".mov", ".flv", ".webm"):
        if slug.lower().endswith(ext): slug = slug[:-len(ext)]; break
    item = get_by_slug(slug)
    if not item: item = await _resolver_episodio_sob_demanda(slug)
    if not item: raise HTTPException(404, "não encontrado")
    return await stream_proxy(item["url"], request)

@app.get("/proxy/stats")
def proxy_monitor(): return {"slugs_carregados": total_slugs(), **proxy_stats()}

# ==========================================================
# ROTAS DE CLIENTES E FATURAMENTO (DO MÓDULO DE MONETIZAÇÃO)
# ==========================================================
class LoginPayload(BaseModel): email: str; senha: str
class RegistrarPayload(BaseModel): nome: str; email: str; senha: str; empresa: Optional[str] = None
class CriarChavePayload(BaseModel): nome: str
class AssinarPayload(BaseModel): plano: str

@app.post("/v1/auth/register")
def registrar(payload: RegistrarPayload, db: Session = Depends(get_db)):
    if db.query(Cliente).filter_by(email=payload.email.lower()).first(): raise HTTPException(400, "Email já cadastrado.")
    plano_free = db.query(Plano).filter_by(slug="free").first()
    if not plano_free: raise HTTPException(500, "Plano free não configurado.")
    cliente = Cliente(nome=payload.nome, email=payload.email.lower(), senha_hash=hash_senha(payload.senha), empresa=payload.empresa, plano_id=plano_free.id, ativo=True, plano_expira_em=datetime.utcnow() + timedelta(days=7))
    db.add(cliente); db.commit(); db.refresh(cliente)
    raw, h, prefixo = gerar_chave()
    db.add(ApiKey(cliente_id=cliente.id, chave_hash=h, prefixo=prefixo, nome="primeira-chave", ativa=True)); db.commit()
    token = criar_token_sessao(cliente.id)
    resp = JSONResponse({"ok": True, "cliente_id": cliente.id, "api_key": raw, "prefixo": prefixo, "mensagem": "Conta criada!"})
    resp.set_cookie("sessao", token, httponly=True, secure=False, samesite="lax", max_age=30*24*3600)
    return resp

@app.post("/v1/auth/login")
def login(payload: LoginPayload, db: Session = Depends(get_db)):
    c = db.query(Cliente).filter_by(email=payload.email.lower()).first()
    if not c or not c.senha_hash or not verificar_senha(payload.senha, c.senha_hash): raise HTTPException(401, "Email ou senha inválidos.")
    if not c.ativo: raise HTTPException(403, "Conta suspensa.")
    token = criar_token_sessao(c.id)
    resp = JSONResponse({"ok": True, "cliente_id": c.id})
    resp.set_cookie("sessao", token, httponly=True, secure=False, samesite="lax", max_age=30*24*3600)
    return resp

@app.post("/v1/auth/logout")
def logout():
    resp = JSONResponse({"ok": True}); resp.delete_cookie("sessao"); return resp

@app.get("/v1/auth/me")
def me(cliente: Cliente = Depends(get_cliente_sessao), db: Session = Depends(get_db)):
    plano = db.query(Plano).get(cliente.plano_id)
    uso = db.query(UsoMensal).filter_by(cliente_id=cliente.id, ano_mes=ano_mes_atual()).first()
    return {"id": cliente.id, "nome": cliente.nome, "email": cliente.email, "empresa": cliente.empresa, "criado_em": cliente.criado_em.isoformat() if cliente.criado_em else None, "uso_mes": uso.req_total if uso else 0, "plano": {"slug": plano.slug, "nome": plano.nome, "req_mes": plano.req_mes, "req_por_segundo": plano.req_por_segundo, "proxy_habilitado": plano.proxy_habilitado, "proxy_ip_limite": plano.proxy_ip_limite, "preco_mensal": plano.preco_mensal, "expira_em": cliente.plano_expira_em.isoformat() if cliente.plano_expira_em else None}}

@app.get("/v1/auth/keys")
def listar_chaves(cliente: Cliente = Depends(get_cliente_sessao), db: Session = Depends(get_db)):
    ks = db.query(ApiKey).filter_by(cliente_id=cliente.id).order_by(ApiKey.criada_em.desc()).all()
    return {"items": [{"id": k.id, "nome": k.nome, "prefixo": k.prefixo, "ativa": k.ativa, "criada_em": k.criada_em.isoformat() if k.criada_em else None, "ultimo_uso": k.ultimo_uso.isoformat() if k.ultimo_uso else None} for k in ks]}

@app.post("/v1/auth/keys")
def criar_chave(payload: CriarChavePayload, cliente: Cliente = Depends(get_cliente_sessao), db: Session = Depends(get_db)):
    if db.query(ApiKey).filter_by(cliente_id=cliente.id, ativa=True).count() >= 5: raise HTTPException(400, "Máximo de 5 chaves ativas.")
    raw, h, prefixo = gerar_chave()
    db.add(ApiKey(cliente_id=cliente.id, chave_hash=h, prefixo=prefixo, nome=payload.nome, ativa=True)); db.commit()
    return {"chave": raw, "prefixo": prefixo, "aviso": "Guarde agora."}

@app.delete("/v1/auth/keys/{kid}")
def revogar_chave(kid: int, cliente: Cliente = Depends(get_cliente_sessao), db: Session = Depends(get_db)):
    k = db.query(ApiKey).filter_by(id=kid, cliente_id=cliente.id).first()
    if not k: raise HTTPException(404, "Chave não encontrada.")
    k.ativa = False; db.commit(); return {"ok": True}

@app.get("/v1/plans")
def listar_planos(db: Session = Depends(get_db)):
    ps = db.query(Plano).order_by(Plano.ordem).all()
    return {"items": [{"slug": p.slug, "nome": p.nome, "preco_mensal": p.preco_mensal, "req_mes": p.req_mes, "req_por_segundo": p.req_por_segundo, "proxy_habilitado": p.proxy_habilitado, "proxy_ip_limite": p.proxy_ip_limite} for p in ps]}

@app.post("/v1/billing/checkout")
def checkout(payload: AssinarPayload, cliente: Cliente = Depends(get_cliente_sessao), db: Session = Depends(get_db)):
    plano = db.query(Plano).filter_by(slug=payload.plano).first()
    if not plano: raise HTTPException(404, "Plano não encontrado.")
    if plano.slug == "free": raise HTTPException(400, "Plano free é automático.")
    try: return pagamentos.criar_checkout(cliente, plano)
    except Exception as e: raise HTTPException(500, f"Erro: {e}")

@app.get("/v1/billing/invoices")
def listar_faturas(cliente: Cliente = Depends(get_cliente_sessao), db: Session = Depends(get_db)):
    fs = db.query(Fatura).filter_by(cliente_id=cliente.id).order_by(Fatura.criada_em.desc()).all()
    return {"items": [{"id": f.id, "valor": f.valor, "status": f.status, "periodo": f.periodo, "criada_em": f.criada_em.isoformat() if f.criada_em else None, "paga_em": f.paga_em.isoformat() if f.paga_em else None} for f in fs]}

@app.post("/v1/billing/cancel")
def cancelar(cliente: Cliente = Depends(get_cliente_sessao), db: Session = Depends(get_db)):
    c = db.query(Cliente).get(cliente.id); c.ativo = False; db.commit(); return {"ok": True, "mensagem": "Assinatura cancelada."}

@app.get("/v1/usage")
def uso_detalhado(cliente: Cliente = Depends(get_cliente_sessao), db: Session = Depends(get_db)):
    uso = db.query(UsoMensal).filter_by(cliente_id=cliente.id, ano_mes=ano_mes_atual()).first()
    return {"req_total": uso.req_total if uso else 0, "req_bloqueadas": uso.req_bloqueadas if uso else 0, "serie_7dias": [{"dia": (datetime.utcnow() - timedelta(days=i)).strftime("%d/%m"), "valor": 0} for i in range(6, -1, -1)], "top_endpoints": []}

@app.post("/webhooks/stripe")
async def webhook_stripe(request: Request):
    if not pagamentos.stripe: raise HTTPException(503, "Stripe não configurado.")
    import stripe as st
    payload = await request.body(); sig = request.headers.get("stripe-signature")
    try: event = st.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except Exception as e: raise HTTPException(400, f"Assinatura inválida: {e}")
    if event["type"] == "checkout.session.completed":
        s = event["data"]["object"]; cid = int(s.get("metadata", {}).get("cliente_id", 0)); slug = s.get("metadata", {}).get("plano_slug", ""); valor = (s.get("amount_total") or 0) / 100.0
        if cid and slug: pagamentos.ativar_plano_pago(cid, slug, "stripe", s.get("id"), valor)
    return {"ok": True}

@app.post("/webhooks/mercadopago")
async def webhook_mp(request: Request):
    if not pagamentos._mp_sdk: raise HTTPException(503, "Mercado Pago não configurado.")
    body = await request.json()
    try:
        if body.get("type") == "preapproval":
            preapproval_id = body.get("data", {}).get("id")
            if not preapproval_id: return {"ok": True}
            r = pagamentos._mp_sdk.preapproval().get(preapproval_id); resp = r.get("response", {}); status = resp.get("status")
            if status == "authorized":
                db = SessionLocal()
                try:
                    c = db.query(Cliente).filter_by(mp_id=preapproval_id).first()
                    if c:
                        plano_slug = "starter"; valor = float(resp.get("auto_recurring", {}).get("transaction_amount") or 0)
                        if valor >= 499: plano_slug = "enterprise"
                        elif valor >= 149: plano_slug = "pro"
                        elif valor >= 49: plano_slug = "starter"
                        pagamentos.ativar_plano_pago(c.id, plano_slug, "mercadopago", preapproval_id, valor)
                finally: db.close()
    except Exception as e: print(f"[MP Webhook] Erro: {e}")
    return {"ok": True}

@app.get("/v1/status")
def v1_status(auth = Depends(verificar_api_key)): return {"stats": _stats(), "ok": True}

@app.get("/v1/home")
async def v1_home(force: bool = False, auth = Depends(verificar_api_key)):
    global _HOME_CACHE, _HOME_CACHE_TS
    if not _FILMES and not _SERIES: return {"hero": [], "colecoes": [], "trailers": [], "stats": _stats()}
    if not force and _HOME_CACHE and (time.time() - _HOME_CACHE_TS) < 300: return _HOME_CACHE
    _HOME_CACHE = await build_home_async({"filmes": _FILMES, "series": _SERIES, "stats": _stats()}, force_hero=force); _HOME_CACHE_TS = time.time(); return _HOME_CACHE

@app.get("/v1/filmes")
def v1_filmes(page: int = Query(1), size: int = Query(30), categoria: str | None = None, auth = Depends(verificar_api_key)):
    filmes = _FILMES
    if categoria: filmes = [f for f in filmes if f.get("categoria", "").lower() == categoria.lower()]
    resp = paginate(filmes, page, size); resp["items"] = [_card_app(it, "filme") for it in resp["items"]]; return resp

@app.get("/v1/series")
def v1_series(page: int = Query(1), size: int = Query(30), categoria: str | None = None, auth = Depends(verificar_api_key)):
    series = _SERIES
    if categoria: series = [s for s in series if s.get("categoria", "").lower() == categoria.lower()]
    resp = paginate(series, page, size); resp["items"] = [_card_app(it, "serie") for it in resp["items"]]; return resp

@app.get("/v1/canais")
def v1_canais(page: int = Query(1), size: int = Query(50), categoria: str | None = None, auth = Depends(verificar_api_key)):
    canais = _CANAIS
    if categoria: canais = [c for c in canais if c.get("categoria", "").lower() == categoria.lower()]
    resp = paginate(canais, page, size); resp["items"] = [_card_app(it, "canal") for it in resp["items"]]; return resp

@app.get("/v1/buscar")
def v1_buscar(q: str, tipo: str | None = None, page: int = Query(1), size: int = Query(30), auth = Depends(verificar_api_key)):
    q_raw = q.lower().strip(); q_norm = _normalizar_busca(q); resultados = []
    def match(item):
        tit = item.get("titulo") or ""
        return q_raw in tit.lower() or (q_norm and q_norm in _normalizar_busca(tit))
    if tipo in (None, "filme", "movie"): resultados += [_card_app(f, "filme") for f in _FILMES if match(f)]
    if tipo in (None, "serie", "series"): resultados += [_card_app(s, "serie") for s in _SERIES if match(s)]
    if tipo in (None, "canal", "channel"): resultados += [_card_app(c, "canal") for c in _CANAIS if match(c)]
    return paginate(resultados, page, size)

@app.get("/v1/categorias")
def v1_categorias(auth = Depends(verificar_api_key)):
    return {"filmes": sorted({f["categoria"] for f in _FILMES if f.get("categoria")}), "series": sorted({s["categoria"] for s in _SERIES if s.get("categoria")}), "canais": sorted({c["categoria"] for c in _CANAIS if c.get("categoria")})}

@app.get("/v1/info/filme/{filme_id}")
async def v1_info_filme(filme_id: str, auth = Depends(verificar_api_key)): return await fetch_movie_info(filme_id)

@app.get("/v1/info/serie/{serie_id}")
async def v1_info_serie(serie_id: str, auth = Depends(verificar_api_key)): return await fetch_series_info(serie_id)

@app.get("/v1/stream/{slug_path:path}")
async def v1_stream(slug_path: str, request: Request, t: str = Query(None)):
    if t: data = validar_stream(t); slug = data["slug"]
    else:
        chave = request.headers.get("X-API-Key") or request.query_params.get("api_key")
        if not chave: raise HTTPException(401, "Stream precisa de token ou api_key")
        from auth import hash_chave as _h
        db = SessionLocal()
        try:
            ak = db.query(ApiKey).filter(ApiKey.chave_hash == _h(chave), ApiKey.ativa == True).first()
            if not ak: raise HTTPException(401, "API key inválida")
            c = db.query(Cliente).get(ak.cliente_id)
            if not c or not c.ativo: raise HTTPException(403, "Conta inativa")
            if c.plano_expira_em and c.plano_expira_em < datetime.utcnow(): raise HTTPException(402, "Plano expirado")
            p = db.query(Plano).get(c.plano_id)
            if not p or not p.proxy_habilitado: raise HTTPException(403, "Proxy não disponível no seu plano")
        finally: db.close()
        slug = slug_path
    for ext in (".mp4", ".mkv", ".ts", ".m3u8", ".avi", ".mov", ".flv", ".webm"):
        if slug.lower().endswith(ext): slug = slug[:-len(ext)]; break
    item = get_by_slug(slug) or get_by_id(slug)
    if not item: raise HTTPException(404, "Item não encontrado")
    return await stream_proxy(item["url"], request)

@app.get("/v1/playlist.m3u")
def v1_playlist(request: Request, api_key: str = Query(...)):
    from auth import hash_chave as _h
    db = SessionLocal()
    try:
        ak = db.query(ApiKey).filter(ApiKey.chave_hash == _h(api_key), ApiKey.ativa == True).first()
        if not ak: raise HTTPException(401, "API key inválida")
        c = db.query(Cliente).get(ak.cliente_id)
        if not c or not c.ativo: raise HTTPException(403, "Conta inativa")
        if c.plano_expira_em and c.plano_expira_em < datetime.utcnow(): raise HTTPException(402, "Plano expirado")
    finally: db.close()
    base = str(request.base_url).rstrip("/"); lines = ["#EXTM3U"]
    for f in _FILMES:
        slug = _slugify(f.get("titulo") or "", f.get("ano"))
        if not slug: continue
        url = f"{base}/v1/stream/{slug}.mp4?api_key={api_key}"
        lines.append(f'#EXTINF:-1 tvg-logo="{f.get("capa") or ""}" group-title="{f.get("categoria") or "Filmes"}",{f.get("titulo")}'); lines.append(url)
    for s_ in _SERIES:
        slug = _slugify(s_.get("titulo") or "", s_.get("ano"))
        if not slug: continue
        url = f"{base}/v1/stream/{slug}.mp4?api_key={api_key}"
        lines.append(f'#EXTINF:-1 tvg-logo="{s_.get("capa") or ""}" group-title="{s_.get("categoria") or "Séries"} - Série",{s_.get("titulo")}'); lines.append(url)
    for c_ in _CANAIS:
        slug = _slugify(c_.get("titulo") or "")
        if not slug: continue
        url = f"{base}/v1/stream/{slug}.ts?api_key={api_key}"
        lines.append(f'#EXTINF:-1 tvg-logo="{c_.get("capa") or ""}" group-title="{c_.get("categoria") or "Canais"}",{c_.get("titulo")}'); lines.append(url)
    return Response("\n".join(lines), media_type="audio/x-mpegurl")

class AdminClientePayload(BaseModel):
    nome: str; email: str; senha: str; plano: str = "free"; empresa: Optional[str] = None; dias: Optional[int] = None

@app.post("/admin/clientes")
def admin_criar_cliente(payload: AdminClientePayload, db: Session = Depends(get_db)):
    if db.query(Cliente).filter_by(email=payload.email.lower()).first(): raise HTTPException(400, "Email já cadastrado.")
    plano = db.query(Plano).filter_by(slug=payload.plano).first()
    if not plano: raise HTTPException(404, "Plano inválido.")
    dias = payload.dias if payload.dias is not None else (7 if plano.slug == "free" else 30)
    c = Cliente(nome=payload.nome, email=payload.email.lower(), senha_hash=hash_senha(payload.senha), empresa=payload.empresa, plano_id=plano.id, ativo=True, plano_expira_em=datetime.utcnow() + timedelta(days=dias))
    db.add(c); db.commit(); db.refresh(c)
    raw, h, prefixo = gerar_chave()
    db.add(ApiKey(cliente_id=c.id, chave_hash=h, prefixo=prefixo, nome="default", ativa=True)); db.commit()
    return {"id": c.id, "api_key": raw, "prefixo": prefixo, "aviso": "Guarde a chave."}

@app.get("/admin/clientes")
def admin_listar_clientes(db: Session = Depends(get_db)):
    cs = db.query(Cliente).order_by(Cliente.criado_em.desc()).all(); out = []
    for c in cs:
        plano = db.query(Plano).get(c.plano_id); uso = db.query(UsoMensal).filter_by(cliente_id=c.id, ano_mes=ano_mes_atual()).first()
        out.append({"id": c.id, "nome": c.nome, "email": c.email, "empresa": c.empresa, "ativo": c.ativo, "plano": plano.slug if plano else None, "plano_nome": plano.nome if plano else None, "plano_expira_em": c.plano_expira_em.isoformat() if c.plano_expira_em else None, "req_mes": uso.req_total if uso else 0, "criado_em": c.criado_em.isoformat() if c.criado_em else None, "chaves_ativas": db.query(ApiKey).filter_by(cliente_id=c.id, ativa=True).count()})
    return {"items": out, "total": len(out)}

@app.put("/admin/clientes/{cid}")
def admin_atualizar_cliente(cid: int, plano: str | None = Body(None), ativo: bool | None = Body(None), dias: int | None = Body(None), db: Session = Depends(get_db)):
    c = db.query(Cliente).get(cid)
    if not c: raise HTTPException(404)
    if plano is not None:
        p = db.query(Plano).filter_by(slug=plano).first()
        if not p: raise HTTPException(404, "Plano inválido.")
        c.plano_id = p.id
    if ativo is not None: c.ativo = ativo
    if dias is not None: c.plano_expira_em = datetime.utcnow() + timedelta(days=dias)
    db.commit(); return {"ok": True}

@app.delete("/admin/clientes/{cid}")
def admin_deletar_cliente(cid: int, db: Session = Depends(get_db)):
    c = db.query(Cliente).get(cid)
    if not c: raise HTTPException(404)
    db.query(ApiKey).filter_by(cliente_id=cid).delete(); db.query(UsoMensal).filter_by(cliente_id=cid).delete(); db.query(Fatura).filter_by(cliente_id=cid).delete(); db.delete(c); db.commit(); return {"ok": True}

@app.post("/admin/clientes/{cid}/resetar-senha")
def admin_resetar_senha(cid: int, nova_senha: str = Body(..., embed=True), db: Session = Depends(get_db)):
    c = db.query(Cliente).get(cid)
    if not c: raise HTTPException(404)
    c.senha_hash = hash_senha(nova_senha); db.commit(); return {"ok": True}

@app.post("/admin/clientes/{cid}/nova-chave")
def admin_nova_chave(cid: int, nome: str = Body("admin-gerada", embed=True), db: Session = Depends(get_db)):
    c = db.query(Cliente).get(cid)
    if not c: raise HTTPException(404)
    raw, h, prefixo = gerar_chave()
    db.add(ApiKey(cliente_id=cid, chave_hash=h, prefixo=prefixo, nome=nome, ativa=True)); db.commit()
    return {"chave": raw, "prefixo": prefixo}

@app.get("/admin/faturas")
def admin_listar_faturas(db: Session = Depends(get_db)):
    fs = db.query(Fatura).order_by(Fatura.criada_em.desc()).limit(200).all(); out = []
    for f in fs:
        c = db.query(Cliente).get(f.cliente_id)
        out.append({"id": f.id, "cliente": c.nome if c else "?", "cliente_email": c.email if c else "?", "valor": f.valor, "status": f.status, "gateway": f.gateway, "periodo": f.periodo, "criada_em": f.criada_em.isoformat() if f.criada_em else None, "paga_em": f.paga_em.isoformat() if f.paga_em else None})
    return {"items": out}

@app.post("/admin/faturas/{fid}/marcar-paga")
def admin_marcar_paga(fid: int, db: Session = Depends(get_db)):
    f = db.query(Fatura).get(fid)
    if not f: raise HTTPException(404)
    f.status = "paga"; f.paga_em = datetime.utcnow(); db.commit(); return {"ok": True}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT, reload=False)