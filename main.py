"""
AI PIM Enterprise Core + Frontend Service
Author: AI Architect
Dependencies: FastAPI, Pydantic, SQLAlchemy, Celery, Pillow, PyGithub, Aiofiles
"""

import os
import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Any
from enum import Enum
from datetime import datetime
from uuid import UUID, uuid4

from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, String, DateTime, Float
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import sessionmaker, declarative_base, Session

# --- KONFIGURACJA LOGOWANIA ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("EnterprisePIM")

# --- MODELE DANYCH ---

class ProductStatus(str, Enum):
    DRAFT = "DRAFT"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    APPROVED = "APPROVED"
    ARCHIVED = "ARCHIVED"

class PackagingComponent(BaseModel):
    material: str
    weight_grams: float
    is_recyclable: bool = True

class ProductAttributes(BaseModel):
    inci_composition: Optional[str] = None
    marketing_description_pl: Optional[str] = None
    country_of_origin: str = "PL"
    class Config:
        extra = "allow"

class ProductDomainModel(BaseModel):
    sku: str
    ean: str
    name: str
    status: ProductStatus = ProductStatus.DRAFT
    attributes: ProductAttributes
    packaging: List[PackagingComponent] = []

# --- BAZA DANYCH ---

Base = declarative_base()

class ProductEntity(Base):
    __tablename__ = "products"
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    sku = Column(String(50), unique=True, index=True, nullable=False)
    ean = Column(String(20), index=True)
    name = Column(String(255), nullable=False)
    status = Column(String(50), default=ProductStatus.DRAFT.value)
    attributes = Column(JSONB, default={})
    packaging_data = Column(JSONB, default=[]) 
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Przechowywanie URL zdjęcia
    image_url = Column(String, nullable=True)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./test.db")
# Hack dla Render (Postgres wymaga postgresql:// a nie postgres://)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)

# Tworzenie tabel (tylko dla SQLite/Dev, w produkcji używa się Alembic)
Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- API ---

app = FastAPI(title="Enterprise AI PIM")

# Montowanie plików statycznych (Frontend)
# USUNIĘTO: if not os.path.exists("static"): os.makedirs("static") - to powodowało błąd
# Zakładamy, że folder static istnieje w repozytorium (bo dodałeś index.html)

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def read_index():
    return FileResponse('static/index.html')

# --- ENDPOINTY PRODUKTOWE ---

@app.get("/api/products")
async def get_products(db: Session = Depends(get_db)):
    """Pobiera listę wszystkich produktów do Dashboardu"""
    return db.query(ProductEntity).all()

@app.post("/products/onboard")
async def onboard_product(product_in: ProductDomainModel, db: Session = Depends(get_db)):
    """Dodawanie nowego produktu"""
    existing = db.query(ProductEntity).filter(ProductEntity.sku == product_in.sku).first()
    if existing:
        raise HTTPException(400, "SKU already exists")
    
    db_product = ProductEntity(
        sku=product_in.sku,
        ean=product_in.ean,
        name=product_in.name,
        attributes=product_in.attributes.dict(),
        packaging_data=[p.dict() for p in product_in.packaging],
        status=ProductStatus.DRAFT.value
    )
    db.add(db_product)
    db.commit()
    return {"status": "Created", "sku": product_in.sku}

@app.post("/api/images/{sku}")
async def upload_image_endpoint(sku: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Prosty upload zdjęć (Symulacja Google Drive dla Dashboardu)"""
    # Tutaj normalnie byłby kod z GoogleDriveAdapter
    # Na potrzeby frontendu symulujemy sukces
    
    # 1. Znajdź produkt
    product = db.query(ProductEntity).filter(ProductEntity.sku == sku).first()
    if not product:
        raise HTTPException(404, "Product not found")
        
    # 2. Symulacja zapisu (w prawdziwym kodzie użyjemy tu GoogleDriveAdapter)
    # fake_url = "https://drive.google.com/thumbnail?id=..." 
    # Dla testu zwrócimy placeholder, żebyś widział efekt na froncie
    fake_url = "https://via.placeholder.com/150?text=" + sku
    
    product.image_url = fake_url
    db.commit()
    
    return {"status": "success", "url": fake_url}

@app.post("/products/{sku}/syndicate/{channel}")
async def syndicate_product(sku: str, channel: str, db: Session = Depends(get_db)):
    """Walidacja dla Rossmanna"""
    product = db.query(ProductEntity).filter(ProductEntity.sku == sku).first()
    if not product:
        raise HTTPException(404, "Product not found")
        
    # Prosta walidacja (Quality Gate)
    errors = []
    attrs = product.attributes or {}
    
    if channel == "ROSSMANN_PL":
        if not attrs.get("inci_composition"):
            errors.append("Błąd Rossmann: Brak składu INCI!")
        if not product.image_url:
            errors.append("Błąd Rossmann: Brak zdjęcia głównego!")

    if errors:
        return {"status": "BLOCKED", "errors": errors}
    
    return {"status": "SUCCESS", "message": "Produkt wysłany do sieci!"}
