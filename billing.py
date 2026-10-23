"""
Modelos do sistema de monetização:
- Cliente (quem assina)
- Plano   (free, starter, pro, enterprise)
- ApiKey  (chaves de acesso à API)
- UsoMensal (contadores por mês)
- Fatura  (histórico de cobranças)
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import Session
from db import Base, SessionLocal


class Plano(Base):
    __tablename__ = "planos"
    id                = Column(Integer, primary_key=True)
    slug              = Column(String, unique=True, index=True)
    nome              = Column(String, nullable=False)
    preco_mensal      = Column(Float, default=0.0)
    req_mes           = Column(Integer, default=1000)   # -1 = ilimitado
    req_por_segundo   = Column(Integer, default=5)
    proxy_habilitado  = Column(Boolean, default=False)
    proxy_ip_limite   = Column(Integer, default=0)      # 0 = ilimitado
    ordem             = Column(Integer, default=0)


class Cliente(Base):
    __tablename__ = "clientes"
    id                = Column(Integer, primary_key=True)
    nome              = Column(String, nullable=False)
    email             = Column(String, unique=True, index=True, nullable=False)
    senha_hash        = Column(String, nullable=True)      # null = precisa definir
    empresa           = Column(String, nullable=True)
    pais              = Column(String, default="BR")
    plano_id          = Column(Integer, ForeignKey("planos.id"), nullable=False)
    ativo             = Column(Boolean, default=True)
    plano_expira_em   = Column(DateTime, nullable=True)    # quando a assinatura atual expira
    stripe_id         = Column(String, nullable=True)
    mp_id             = Column(String, nullable=True)
    criado_em         = Column(DateTime, default=datetime.utcnow)


class ApiKey(Base):
    __tablename__ = "api_keys"
    id          = Column(Integer, primary_key=True)
    cliente_id  = Column(Integer, ForeignKey("clientes.id"), index=True)
    chave_hash  = Column(String, unique=True, index=True)  # SHA-256, nunca a chave crua
    prefixo     = Column(String, index=True)               # primeiros 16 chars p/ exibir
    nome        = Column(String)
    ativa       = Column(Boolean, default=True)
    criada_em   = Column(DateTime, default=datetime.utcnow)
    ultimo_uso  = Column(DateTime, nullable=True)


class UsoMensal(Base):
    __tablename__ = "uso_mensal"
    id              = Column(Integer, primary_key=True)
    cliente_id      = Column(Integer, ForeignKey("clientes.id"), index=True)
    ano_mes         = Column(String, index=True)   # "2026-10"
    req_total       = Column(Integer, default=0)
    req_bloqueadas  = Column(Integer, default=0)
    stream_seg      = Column(Float, default=0.0)
    bytes_proxy     = Column(Integer, default=0)


class Fatura(Base):
    __tablename__ = "faturas"
    id          = Column(Integer, primary_key=True)
    cliente_id  = Column(Integer, ForeignKey("clientes.id"), index=True)
    valor       = Column(Float, nullable=False)
    status      = Column(String, default="pendente")   # pendente, paga, falhou, estornada
    gateway     = Column(String)                       # stripe, mercadopago
    gateway_id  = Column(String)
    periodo     = Column(String)                       # "2026-10"
    criada_em   = Column(DateTime, default=datetime.utcnow)
    paga_em     = Column(DateTime, nullable=True)


def seed_planos():
    """Cria os 4 planos padrão se a tabela estiver vazia."""
    db = SessionLocal()
    try:
        if db.query(Plano).count() > 0:
            return
        db.add_all([
            Plano(slug="free", nome="Free", preco_mensal=0.0,
                  req_mes=1000, req_por_segundo=2,
                  proxy_habilitado=False, proxy_ip_limite=0, ordem=0),
            Plano(slug="starter", nome="Starter", preco_mensal=49.0,
                  req_mes=50_000, req_por_segundo=10,
                  proxy_habilitado=True, proxy_ip_limite=3, ordem=1),
            Plano(slug="pro", nome="Pro", preco_mensal=149.0,
                  req_mes=500_000, req_por_segundo=50,
                  proxy_habilitado=True, proxy_ip_limite=10, ordem=2),
            Plano(slug="enterprise", nome="Enterprise", preco_mensal=499.0,
                  req_mes=-1, req_por_segundo=200,
                  proxy_habilitado=True, proxy_ip_limite=0, ordem=3),
        ])
        db.commit()
        print("[Billing] ✓ 4 planos criados (free, starter, pro, enterprise)")
    finally:
        db.close()


def get_plano_por_slug(slug: str) -> Plano | None:
    db = SessionLocal()
    try:
        return db.query(Plano).filter_by(slug=slug).first()
    finally:
        db.close()