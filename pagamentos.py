"""
Integração com gateways de pagamento.
- Mercado Pago (PIX + cartão — BR)
- Stripe (internacional)
"""
from datetime import datetime, timedelta

from config import (
    MP_ACCESS_TOKEN, MP_ENABLED,
    STRIPE_SECRET, STRIPE_ENABLED,
    PUBLIC_PANEL_URL,
)
from db import SessionLocal
from billing import Cliente, Plano, Fatura

# Importações condicionais — não quebra se não configurado
try:
    if MP_ENABLED:
        import mercadopago
        _mp_sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
    else:
        _mp_sdk = None
except Exception as e:
    print(f"[Pagamentos] Mercado Pago indisponível: {e}")
    _mp_sdk = None

try:
    if STRIPE_ENABLED:
        import stripe
        stripe.api_key = STRIPE_SECRET
    else:
        stripe = None
except Exception as e:
    print(f"[Pagamentos] Stripe indisponível: {e}")
    stripe = None


# ==========================================
# MERCADO PAGO — Preapproval (assinatura recorrente)
# ==========================================
def criar_checkout_mp(cliente: Cliente, plano: Plano) -> dict | None:
    if not _mp_sdk:
        return None
    try:
        result = _mp_sdk.preapproval().create({
            "reason": f"Vênus API - Plano {plano.nome}",
            "auto_recurring": {
                "frequency": 1,
                "frequency_type": "months",
                "transaction_amount": float(plano.preco_mensal),
                "currency_id": "BRL",
            },
            "back_url": f"{PUBLIC_PANEL_URL}/?status=sucesso",
            "payer_email": cliente.email,
            "status": "pending",
        })
        resp = result.get("response", {})
        init_point = resp.get("init_point") or resp.get("sandbox_init_point")
        preapproval_id = resp.get("id")

        # Salva o ID no cliente
        if preapproval_id:
            db = SessionLocal()
            try:
                c = db.query(Cliente).get(cliente.id)
                if c:
                    c.mp_id = preapproval_id
                    db.commit()
            finally:
                db.close()

        return {"url": init_point, "gateway": "mercadopago", "id": preapproval_id}
    except Exception as e:
        print(f"[MP] Erro ao criar checkout: {e}")
        return None


# ==========================================
# STRIPE — Checkout Session (subscription)
# ==========================================
def criar_checkout_stripe(cliente: Cliente, plano: Plano) -> dict | None:
    if not stripe:
        return None
    try:
        # Cria/reusa customer
        if not cliente.stripe_id:
            cust = stripe.Customer.create(
                email=cliente.email,
                name=cliente.nome,
                metadata={"cliente_id": cliente.id},
            )
            db = SessionLocal()
            try:
                c = db.query(Cliente).get(cliente.id)
                if c:
                    c.stripe_id = cust.id
                    db.commit()
            finally:
                db.close()
            cliente.stripe_id = cust.id

        session = stripe.checkout.Session.create(
            mode="subscription",
            customer=cliente.stripe_id,
            line_items=[{
                "price_data": {
                    "currency": "brl",
                    "product_data": {"name": f"Vênus API - Plano {plano.nome}"},
                    "unit_amount": int(plano.preco_mensal * 100),
                    "recurring": {"interval": "month"},
                },
                "quantity": 1,
            }],
            success_url=f"{PUBLIC_PANEL_URL}/?status=sucesso",
            cancel_url=f"{PUBLIC_PANEL_URL}/?status=cancelado",
            metadata={"cliente_id": cliente.id, "plano_slug": plano.slug},
        )
        return {"url": session.url, "gateway": "stripe", "id": session.id}
    except Exception as e:
        print(f"[Stripe] Erro ao criar checkout: {e}")
        return None


def criar_checkout(cliente: Cliente, plano: Plano) -> dict:
    """Tenta MP primeiro, senão Stripe."""
    if MP_ENABLED:
        r = criar_checkout_mp(cliente, plano)
        if r:
            return r
    if STRIPE_ENABLED:
        r = criar_checkout_stripe(cliente, plano)
        if r:
            return r
    raise RuntimeError(
        "Nenhum gateway configurado. Configure MP_ACCESS_TOKEN ou STRIPE_SECRET no .env."
    )


# ==========================================
# Ativação de plano pago (chamada pelos webhooks)
# ==========================================
def ativar_plano_pago(cliente_id: int, plano_slug: str, gateway: str,
                      gateway_id: str | None, valor: float) -> None:
    db = SessionLocal()
    try:
        cliente = db.query(Cliente).get(cliente_id)
        if not cliente:
            print(f"[Pagamentos] Cliente {cliente_id} não encontrado.")
            return

        plano = db.query(Plano).filter_by(slug=plano_slug).first()
        if not plano:
            print(f"[Pagamentos] Plano '{plano_slug}' não existe.")
            return

        agora = datetime.utcnow()
        base = cliente.plano_expira_em if (cliente.plano_expira_em and cliente.plano_expira_em > agora) else agora
        cliente.plano_id = plano.id
        cliente.plano_expira_em = base + timedelta(days=30)
        cliente.ativo = True

        fatura = Fatura(
            cliente_id=cliente_id,
            valor=valor,
            status="paga",
            gateway=gateway,
            gateway_id=gateway_id,
            periodo=cliente.plano_expira_em.strftime("%Y-%m"),
            paga_em=agora,
        )
        db.add(fatura)
        db.commit()

        print(f"[Pagamentos] ✓ Plano {plano_slug} ativado para cliente {cliente_id} "
              f"até {cliente.plano_expira_em.date()}")
    finally:
        db.close()


def cancelar_plano(cliente_id: int) -> None:
    db = SessionLocal()
    try:
        c = db.query(Cliente).get(cliente_id)
        if c:
            c.ativo = False
            db.commit()
    finally:
        db.close()