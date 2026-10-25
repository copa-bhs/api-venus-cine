import hmac
import hashlib
import json
from datetime import datetime, timedelta
from typing import Optional

import httpx
from fastapi import Request, HTTPException

from config import (
    MP_ACCESS_TOKEN, MP_ENABLED, MP_WEBHOOK_SECRET,
    PUBLIC_PANEL_URL
)
from db import SessionLocal
from billing import Cliente, Plano, Fatura

MP_BASE = "https://api.mercadopago.com"


def criar_pix(cliente: Cliente, plano: Plano) -> dict:
    """Cria uma cobrança PIX via nova API de Orders e retorna o QR Code."""
    if not MP_ENABLED:
        raise RuntimeError("Mercado Pago não configurado no servidor.")

    # Cria a fatura pendente no banco
    db = SessionLocal()
    try:
        fatura = Fatura(
            cliente_id=cliente.id,
            valor=plano.preco_mensal,
            status="pendente",
            gateway="mercadopago",
            periodo=datetime.utcnow().strftime("%Y-%m"),
        )
        db.add(fatura)
        db.commit()
        db.refresh(fatura)

        headers = {
            "Authorization": f"Bearer {MP_ACCESS_TOKEN}",
            "Content-Type": "application/json",
            "X-Idempotency-Key": str(fatura.id)  # Evita duplicar a cobrança
        }

        # Nova API de Orders do Mercado Pago
        payload = {
            "type": "online",
            "total_amount": str(plano.preco_mensal),
            "description": f"Assinatura Vênus API - Plano {plano.nome}",
            "external_reference": str(fatura.id),
            "expiration_time": "PT30M",
            "config": {
                "qr": {
                    "mode": "static"
                }
            },
            "transactions": {
                "payments": [
                    {
                        "amount": str(plano.preco_mensal),
                        "payment_method": {
                            "id": "pix",
                            "type": "bank_transfer"
                        }
                    }
                ]
            },
            "payer": {
                "email": cliente.email,
                "first_name": cliente.nome.split(" ")[0],
                "last_name": " ".join(cliente.nome.split(" ")[1:]) or "Silva",
            },
            "notification_url": f"{PUBLIC_PANEL_URL}/webhooks/mercadopago"
        }

        # Faz a requisição HTTP direta (mais seguro que o SDK antigo)
        with httpx.Client(timeout=30) as client:
            r = client.post(f"{MP_BASE}/v1/orders", headers=headers, json=payload)
            
            if r.status_code not in (200, 201):
                print(f"[MP] Erro ao criar order: {r.status_code} - {r.text}")
                raise RuntimeError(f"Mercado Pago recusou: {r.text}")

            data = r.json()
            
        # Extrai o QR Code da nova resposta
        order_id = data.get("id")
        
        # A nova API pode retornar o QR em locais diferentes dependendo da configuração
        qr_data = None
        if "transactions" in data and "payments" in data["transactions"]:
            for p in data["transactions"]["payments"]:
                if "payment_method" in p and p["payment_method"].get("qr_code"):
                    qr_data = p["payment_method"]
                    break
        
        if not qr_data and "config" in data and "qr" in data["config"]:
            qr_data = data["config"]["qr"]

        if not qr_data or "qr_code" not in qr_data:
            print(f"[MP] Resposta sem QR Code: {data}")
            raise RuntimeError("Mercado Pago não retornou o QR Code. Verifique se a conta está ativa.")

        # Salva o ID da order na fatura
        fatura.gateway_id = str(order_id)
        db.commit()

        return {
            "ok": True,
            "fatura_id": fatura.id,
            "payment_id": order_id,
            "qr_code_base64": qr_data.get("qr_code_base64") or qr_data.get("qr_code_base64_image"),
            "qr_code_copia_cola": qr_data.get("qr_code") or qr_data.get("qr_code_text"),
            "valor": plano.preco_mensal,
        }
    finally:
        db.close()


def validar_webhook(request: Request, body: bytes) -> dict:
    """Valida a assinatura do webhook do Mercado Pago."""
    if not MP_WEBHOOK_SECRET:
        return json.loads(body) # Fallback se não tiver chave (só pra teste)

    signature = request.headers.get("x-signature")
    if not signature: raise HTTPException(401, "Assinatura ausente.")

    parts = dict(p.strip().split("=", 1) for p in signature.split(",") if "=" in p)
    ts = parts.get("ts")
    v1 = parts.get("v1")

    if not ts or not v1: raise HTTPException(401, "Assinatura mal formatada.")

    data_id = request.query_params.get("data.id", "")
    manifest = f"id:{data_id};request-id:{request.headers.get('x-request-id', '')};ts:{ts};"

    expected = hmac.new(MP_WEBHOOK_SECRET.encode(), manifest.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, v1):
        raise HTTPException(401, "Assinatura inválida.")

    return json.loads(body)


def processar_pagamento(payment_id: str) -> bool:
    """Consulta a order no MP e ativa o plano se aprovado."""
    if not MP_ENABLED: return False

    headers = {"Authorization": f"Bearer {MP_ACCESS_TOKEN}"}
    
    with httpx.Client(timeout=30) as client:
        # Tenta buscar como Order primeiro
        r = client.get(f"{MP_BASE}/v1/orders/{payment_id}", headers=headers)
        
        # Se não achar order, tenta buscar como Payment (legado)
        if r.status_code == 404:
            r = client.get(f"{MP_BASE}/v1/payments/{payment_id}", headers=headers)

        if r.status_code != 200:
            return False

        data = r.json()

    status = data.get("status")
    # Na API de orders, o status aprovado pode vir como "processed", "paid" ou "approved"
    if status not in ("processed", "paid", "approved"):
        # Se for payment legado, o status é "approved"
        return False

    external_ref = data.get("external_reference")
    if not external_ref: return False

    db = SessionLocal()
    try:
        fatura = db.query(Fatura).get(int(external_ref))
        if not fatura or fatura.status == "paga": return True

        cliente = db.query(Cliente).get(fatura.cliente_id)
        plano = db.query(Plano).get(cliente.plano_id)

        agora = datetime.utcnow()
        base = cliente.plano_expira_em if (cliente.plano_expira_em and cliente.plano_expira_em > agora) else agora
        cliente.plano_expira_em = base + timedelta(days=30)
        cliente.ativo = True

        fatura.status = "paga"
        fatura.paga_em = agora
        db.commit()
        return True
    finally:
        db.close()