import hashlib
import secrets
import time
from datetime import datetime, timedelta
from typing import Optional

import jwt
import bcrypt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from config import PAINEL_SECRET, STREAM_SECRET
from db import get_db
from billing import ApiKey, Cliente, Plano, UsoMensal

_RATE_BUCKET: dict[int, list[float]] = {}

def hash_senha(senha: str) -> str:
    # O bcrypt tem limite de 72 bytes. Truncamos para evitar erro.
    senha_bytes = senha.encode('utf-8')[:72]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(senha_bytes, salt).decode('utf-8')

def verificar_senha(senha: str, hash_: str) -> bool:
    if not hash_:
        return False
    try:
        senha_bytes = senha.encode('utf-8')[:72]
        return bcrypt.checkpw(senha_bytes, hash_.encode('utf-8'))
    except Exception:
        return False

def gerar_chave() -> tuple[str, str, str]:
    raw = "vn_live_" + secrets.token_urlsafe(32)
    h = hashlib.sha256(raw.encode()).hexdigest()
    return raw, h, raw[:16]

def hash_chave(chave: str) -> str:
    return hashlib.sha256(chave.encode()).hexdigest()

def ano_mes_atual() -> str:
    return datetime.utcnow().strftime("%Y-%m")

async def verificar_api_key(request: Request, db: Session = Depends(get_db)):
    chave = (
        request.headers.get("X-API-Key")
        or request.headers.get("Authorization", "").replace("Bearer ", "").strip()
        or request.query_params.get("api_key")
    )
    if not chave or not chave.startswith("vn_live_"):
        raise HTTPException(401, "API key ausente ou inválida.")

    h = hash_chave(chave)
    apikey = db.query(ApiKey).filter(ApiKey.chave_hash == h, ApiKey.ativa == True).first()
    if not apikey:
        raise HTTPException(401, "API key inválida ou revogada.")

    cliente = db.query(Cliente).filter(Cliente.id == apikey.cliente_id).first()
    if not cliente:
        raise HTTPException(401, "Cliente não encontrado.")
    if not cliente.ativo:
        raise HTTPException(403, "Conta suspensa. Regularize o pagamento.")

    if cliente.plano_expira_em and cliente.plano_expira_em < datetime.utcnow():
        raise HTTPException(402, "Plano expirado. Renove sua assinatura.")

    plano = db.query(Plano).filter(Plano.id == cliente.plano_id).first()
    if not plano:
        raise HTTPException(500, "Plano não configurado.")

    agora = time.time()
    bucket = _RATE_BUCKET.setdefault(cliente.id, [])
    bucket[:] = [t for t in bucket if agora - t < 1.0]
    if len(bucket) >= plano.req_por_segundo:
        raise HTTPException(429, f"Rate limit excedido ({plano.req_por_segundo} req/s).")
    bucket.append(agora)

    uso = db.query(UsoMensal).filter_by(cliente_id=cliente.id, ano_mes=ano_mes_atual()).first()
    if not uso:
        uso = UsoMensal(cliente_id=cliente.id, ano_mes=ano_mes_atual())
        db.add(uso)
        db.commit()
        db.refresh(uso)

    if plano.req_mes >= 0 and uso.req_total >= plano.req_mes:
        uso.req_bloqueadas += 1
        db.commit()
        raise HTTPException(429, f"Cota mensal esgotada ({plano.req_mes} req). Faça upgrade.")

    uso.req_total += 1
    apikey.ultimo_uso = datetime.utcnow()
    db.commit()

    request.state.cliente = cliente
    request.state.plano = plano
    request.state.apikey = apikey
    return cliente, plano, apikey

def criar_token_sessao(cliente_id: int, dias: int = 30) -> str:
    return jwt.encode(
        {"cid": cliente_id, "exp": datetime.utcnow() + timedelta(days=dias), "tipo": "sessao"},
        PAINEL_SECRET,
        algorithm="HS256",
    )

async def get_cliente_sessao(request: Request, db: Session = Depends(get_db)) -> Cliente:
    # Tenta pegar do Cookie
    tok = request.cookies.get("sessao")
    
    # Se não tiver no Cookie, tenta pegar do Header (Authorization: Bearer)
    if not tok:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            tok = auth_header.split(" ")[1]

    if not tok:
        raise HTTPException(401, "Não autenticado. Faça login.")
        
    try:
        data = jwt.decode(tok, PAINEL_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Sessão expirada. Faça login novamente.")
    except Exception:
        raise HTTPException(401, "Sessão inválida.")

    c = db.query(Cliente).filter_by(id=data.get("cid")).first()
    if not c:
        raise HTTPException(401, "Cliente não encontrado.")
    if not c.ativo:
        raise HTTPException(403, "Conta suspensa.")
        
    # BLOQUEIO: Se o plano expirou, não deixa logar no painel
    if c.plano_expira_em and c.plano_expira_em < datetime.utcnow():
        raise HTTPException(402, "Seu plano expirou. Renove para acessar o painel.")
        
    return c

def assinar_stream(slug: str, cliente_id: int, ttl_min: int = 5) -> str:
    return jwt.encode(
        {"slug": slug, "cid": cliente_id, "exp": datetime.utcnow() + timedelta(minutes=ttl_min), "tipo": "stream"},
        STREAM_SECRET,
        algorithm="HS256",
    )

def validar_stream(token: str) -> dict:
    try:
        data = jwt.decode(token, STREAM_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Link de stream expirado. Renove a requisição.")
    except Exception:
        raise HTTPException(401, "Link de stream inválido.")
    if data.get("tipo") != "stream":
        raise HTTPException(401, "Token não é de stream.")
    return data