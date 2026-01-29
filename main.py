"""
AI PIM Enterprise Core
Author: AI Architect
Architecture: Hexagonal (Ports & Adapters)
Dependencies: FastAPI, Pydantic, SQLAlchemy, Celery, Pillow, PyGithub
"""

import os
import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Any
from enum import Enum
from datetime import datetime
from uuid import UUID, uuid4

from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field, validator, HttpUrl
from sqlalchemy import create_engine, Column, String, Boolean, DateTime, Float, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import sessionmaker, declarative_base, Session, relationship

# --- KONFIGURACJA LOGOWANIA ENTERPRISE ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("EnterprisePIM")

# --- WARSTWA DOMENY (DOMAIN LAYER) ---
# Czysta logika biznesowa, niezależna od bazy danych czy frameworka.

class ProductStatus(str, Enum):
    DRAFT = "DRAFT"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    APPROVED = "APPROVED"
    ARCHIVED = "ARCHIVED"

class MaterialType(str, Enum):
    PAPER_CARDBOARD = "PAPER_CARDBOARD"
    PLASTIC_PET = "PLASTIC_PET"
    PLASTIC_HDPE = "PLASTIC_HDPE"
    GLASS_COLOR = "GLASS_COLOR"
    ALUMINIUM = "ALUMINIUM"

class ChannelType(str, Enum):
    BASELINKER = "BASELINKER"
    ROSSMANN_PL = "ROSSMANN_PL"
    HEBE_PL = "HEBE_PL"
    PHARMACY_NETWORK = "PHARMACY_NETWORK"

# --- VALUE OBJECTS & MODELS (Pydantic v2) ---

class PackagingComponent(BaseModel):
    """Składnik opakowania do wyliczeń BDO/LUCID"""
    material: MaterialType
    weight_grams: float = Field(gt=0, description="Waga netto materiału w gramach")
    is_recyclable: bool = True

class ProductAttributes(BaseModel):
    """Atrybuty rozszerzone (EAV)"""
    inci_composition: Optional[str] = None
    storage_temperature: Optional[str] = None # np. "15-25C"
    country_of_origin: str = "PL"
    marketing_description_pl: Optional[str] = None
    marketing_description_en: Optional[str] = None
    
    # Obsługa dynamicznych pól
    class Config:
        extra = "allow" 

class ProductDomainModel(BaseModel):
    sku: str
    ean: str
    name: str
    status: ProductStatus = ProductStatus.DRAFT
    attributes: ProductAttributes
    packaging: List[PackagingComponent] = []
    
    def calculate_total_waste_weight(self) -> float:
        return sum(p.weight_grams for p in self.packaging)

# --- WARSTWA APLIKACJI (APPLICATION LAYER / PORTY) ---
# Interfejsy (Abstrakcje) dla zewnętrznych serwisów

class IChannelAdapter(ABC):
    """Interfejs dla adapterów kanałów sprzedaży (Rossmann, BaseLinker, etc.)"""
    
    @abstractmethod
    def validate_product(self, product: ProductDomainModel) -> List[str]:
        """Zwraca listę błędów walidacji specyficznych dla kanału"""
        pass

    @abstractmethod
    def export_product(self, product: ProductDomainModel) -> Any:
        """Eksportuje produkt do formatu kanału"""
        pass

class IStorageAdapter(ABC):
    """Interfejs dla przechowywania plików"""
    @abstractmethod
    def save_file(self, path: str, content: bytes, message: str) -> str:
        pass

# --- WARSTWA INFRASTRUKTURY (ADAPTERY) ---

class RossmannAdapter(IChannelAdapter):
    """
    Implementacja specyficznych wymagań sieci Rossmann.
    Przykład: Wymagane INCI, zdjęcie min 2400px, opis min 500 znaków.
    """
    def validate_product(self, product: ProductDomainModel) -> List[str]:
        errors = []
        # Walidacja 1: INCI obowiązkowe dla kosmetyków
        if not product.attributes.inci_composition:
            errors.append("Rossmann Error: Brak składu INCI (wymagane dla kosmetyków).")
            
        # Walidacja 2: Długość opisu
        desc = product.attributes.marketing_description_pl or ""
        if len(desc) < 200: # Przykładowa wartość
            errors.append(f"Rossmann Error: Opis zbyt krótki ({len(desc)}/200 znaków).")
            
        return errors

    def export_product(self, product: ProductDomainModel) -> Dict:
        # Rossmann często wymaga specyficznego XML lub Excela w standardzie GDSN
        # Tutaj mockup transformacji danych
        return {
            "VendorProductNumber": product.sku,
            "GTIN": product.ean,
            "Description_Long": product.attributes.marketing_description_pl,
            "Ingredients": product.attributes.inci_composition,
            "Waste_Paper_Weight": sum(p.weight_grams for p in product.packaging if p.material == MaterialType.PAPER_CARDBOARD)
        }

class BaseLinkerAdapter(IChannelAdapter):
    """
    Adapter do API BaseLinker (Integration API).
    """
    def __init__(self, api_token: str):
        self.api_token = api_token

    def validate_product(self, product: ProductDomainModel) -> List[str]:
        errors = []
        if not product.ean:
            errors.append("BaseLinker Error: EAN jest wymagany do synchronizacji.")
        return errors

    def export_product(self, product: ProductDomainModel) -> Dict:
        # Mapowanie na strukturę API BaseLinkera (addProduct)
        return {
            "sku": product.sku,
            "ean": product.ean,
            "name": product.name,
            "description": product.attributes.marketing_description_pl,
            # BaseLinker przyjmuje parametry jako listę
            "features": {k: v for k, v in product.attributes.dict().items() if v}
        }

# --- BAZA DANYCH (SQLAlchemy) ---

Base = declarative_base()

class ProductEntity(Base):
    __tablename__ = "products"
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    sku = Column(String(50), unique=True, index=True, nullable=False)
    ean = Column(String(20), index=True)
    name = Column(String(255), nullable=False)
    status = Column(String(50), default=ProductStatus.DRAFT.value)
    
    # JSONB to klucz do elastyczności PIM
    attributes = Column(JSONB, default={})
    packaging_data = Column(JSONB, default=[]) 
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# --- SERWISY APLIKACYJNE (SERVICES) ---

class DataQualityService:
    """Strażnik jakości danych (Quality Gate)"""
    
    def __init__(self):
        self.adapters = {
            ChannelType.ROSSMANN_PL: RossmannAdapter(),
            ChannelType.BASELINKER: BaseLinkerAdapter("dummy_token")
        }
    
    def check_readiness(self, product: ProductDomainModel, channels: List[ChannelType]) -> Dict[str, List[str]]:
        report = {}
        for channel in channels:
            adapter = self.adapters.get(channel)
            if adapter:
                errors = adapter.validate_product(product)
                report[channel.value] = errors
            else:
                report[channel.value] = ["Adapter not configured"]
        return report

# --- API (FastAPI) ---

app = FastAPI(
    title="Enterprise AI PIM",
    version="2.0.0",
    description="Central Product Information Management System with GDSN/Compliance support."
)

# Dependency Injection Bazy Danych
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:pass@localhost/pim_db")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/products/onboard", status_code=201)
async def onboard_product(product_in: ProductDomainModel, db: Session = Depends(get_db)):
    """
    Przyjęcie nowego produktu do systemu (Draft).
    """
    # 1. Sprawdzenie duplikatów
    existing = db.query(ProductEntity).filter(ProductEntity.sku == product_in.sku).first()
    if existing:
        raise HTTPException(400, "SKU already exists")
    
    # 2. Zapis encji
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
    
    return {"id": db_product.id, "status": "Created", "message": "Product currently in Draft. Validation required."}

@app.get("/products/{sku}/compliance-report")
async def get_compliance_report(sku: str, db: Session = Depends(get_db)):
    """
    Generuje raport BDO dla pojedynczego produktu.
    """
    product_ent = db.query(ProductEntity).filter(ProductEntity.sku == sku).first()
    if not product_ent:
        raise HTTPException(404, "Product not found")
        
    # Mapowanie na domenę
    packaging = [PackagingComponent(**p) for p in product_ent.packaging_data]
    
    report = {
        "sku": sku,
        "total_weight_g": sum(p.weight_grams for p in packaging),
        "breakdown": {}
    }
    
    # Agregacja materiałowa
    for p in packaging:
        if p.material not in report["breakdown"]:
            report["breakdown"][p.material] = 0
        report["breakdown"][p.material] += p.weight_grams
        
    return report

@app.post("/products/{sku}/syndicate/{channel}")
async def syndicate_product(sku: str, channel: ChannelType, db: Session = Depends(get_db)):
    """
    Próba wysłania produktu do zewnętrznego kanału (np. Rossmann).
    Uruchamia Quality Gates.
    """
    product_ent = db.query(ProductEntity).filter(ProductEntity.sku == sku).first()
    if not product_ent:
        raise HTTPException(404, "Product not found")

    # Rekonstrukcja modelu domenowego
    domain_product = ProductDomainModel(
        sku=product_ent.sku,
        ean=product_ent.ean,
        name=product_ent.name,
        attributes=ProductAttributes(**product_ent.attributes),
        packaging=[PackagingComponent(**p) for p in product_ent.packaging_data]
    )
    
    # Quality Gate
    dq_service = DataQualityService()
    validation_results = dq_service.check_readiness(domain_product, [channel])
    
    if validation_results[channel.value]:
        # Jeśli są błędy, nie puszczamy dalej
        return {
            "status": "BLOCKED",
            "reason": "Quality Gate Failed",
            "errors": validation_results[channel.value]
        }
        
    # Symulacja wysyłki
    # W produkcji tutaj byłoby wywołanie Celery task: export_to_rossmann.delay(sku)
    
    return {
        "status": "SUCCESS",
        "message": f"Product {sku} valid and queued for export to {channel.value}"
    }

# --- PRZYKŁAD UŻYCIA (WORKFLOW) ---
if __name__ == "__main__":
    # To tylko dla testów lokalnych
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)