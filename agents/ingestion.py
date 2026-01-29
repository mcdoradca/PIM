"""
Ingestion Agent (Data Steward)
Odpowiedzialność: Import, czyszczenie i normalizacja danych wejściowych (Excel, CSV, JSON).
Zasada: Fail-fast (odrzucanie błędnych rekordów), inteligentne mapowanie kolumn.
"""

import pandas as pd
import logging
from typing import List, Dict, Any
from io import BytesIO

logger = logging.getLogger("IngestionAgent")

class DataIngestionAgent:
    """
    Agent odpowiedzialny za parsowanie plików zrzutowych z systemów ERP (Subiekt GT, Comarch XL).
    """

    # Słownik synonimów do inteligentnego mapowania kolumn
    COLUMN_MAPPING_RULES = {
        "sku": ["symbol", "kod", "indeks", "item_no"],
        "ean": ["barcode", "kod_kreskowy", "ean13"],
        "name": ["nazwa", "opis", "description", "product_name"],
        "price_net": ["cena_netto", "price", "net_price"],
        "stock": ["stan", "ilosc", "quantity", "stock_level"]
    }

    def _normalize_headers(self, df: pd.DataFrame) -> pd.DataFrame:
        """Standaryzuje nagłówki kolumn używając słownika synonimów."""
        normalized_cols = {}
        for col in df.columns:
            clean_col = str(col).lower().strip().replace(" ", "_")
            mapped = False
            
            # Szukanie w regułach
            for standard_name, synonyms in self.COLUMN_MAPPING_RULES.items():
                if clean_col == standard_name or clean_col in synonyms:
                    normalized_cols[col] = standard_name
                    mapped = True
                    break
            
            if not mapped:
                normalized_cols[col] = f"attr_{clean_col}" # Dynamiczne atrybuty
        
        logger.info(f"Column mapping applied: {normalized_cols}")
        return df.rename(columns=normalized_cols)

    def process_file(self, file_content: bytes, filename: str) -> List[Dict[str, Any]]:
        """
        Główna metoda przetwarzająca plik binarny na listę słowników (rekordów PIM).
        """
        try:
            if filename.endswith(".xlsx") or filename.endswith(".xls"):
                df = pd.read_excel(BytesIO(file_content))
            elif filename.endswith(".csv"):
                df = pd.read_csv(BytesIO(file_content), sep=None, engine='python') # Auto-detekcja separatora
            else:
                raise ValueError("Unsupported file format. Use .xlsx or .csv")

            # 1. Normalizacja nagłówków
            df = self._normalize_headers(df)

            # 2. Czyszczenie danych (Data Cleaning)
            # Usuwanie wierszy bez SKU (klucz główny)
            if 'sku' in df.columns:
                initial_count = len(df)
                df = df.dropna(subset=['sku'])
                dropped = initial_count - len(df)
                if dropped > 0:
                    logger.warning(f"Dropped {dropped} rows due to missing SKU")
            
            # Konwersja EAN na string (częsty błąd Excela zamieniającego EAN na notację naukową 5.90123E+12)
            if 'ean' in df.columns:
                df['ean'] = df['ean'].apply(lambda x: str(int(float(x))) if pd.notnull(x) and isinstance(x, (float, int)) else str(x))

            # 3. Konwersja NaN na None (dla JSON)
            df = df.where(pd.notnull(df), None)

            records = df.to_dict(orient='records')
            logger.info(f"Successfully ingested {len(records)} records from {filename}")
            return records

        except Exception as e:
            logger.error(f"Ingestion failed: {str(e)}")
            raise e
