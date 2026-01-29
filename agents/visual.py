"""
Visual Agent (DAM Steward)
Odpowiedzialność: Deterministyczne przetwarzanie obrazów zgodnie z wymogami sieci handlowych.
Zasada: Zero inwencji własnej (brak generatywnego AI zmieniającego produkt), 
pełna zgodność techniczna (wymiary, waga, profil kolorów).
"""

import io
import logging
from typing import Tuple, Optional
from PIL import Image, ImageOps, ImageCms

# Konfiguracja logowania
logger = logging.getLogger("VisualAgent")

class ImageStandardizer:
    """
    Klasa realizująca logikę 'Smart Resizing & Padding'.
    """
    
    @staticmethod
    def validate_image_quality(image: Image.Image, min_resolution: Tuple[int, int] = (1000, 1000)) -> bool:
        """Sprawdza czy zdjęcie spełnia minimalne wymogi jakościowe (np. dla Rossmanna/Hebe)."""
        w, h = image.size
        if w < min_resolution[0] or h < min_resolution[1]:
            logger.warning(f"Image rejected: Resolution {w}x{h} is below required {min_resolution}")
            return False
        return True

    @staticmethod
    def normalize_for_marketplace(
        image_bytes: bytes, 
        target_size: Tuple[int, int] = (2500, 2500), 
        output_format: str = "JPEG",
        force_white_background: bool = True
    ) -> bytes:
        """
        Przetwarza surowy obraz do formatu 'Golden Record' akceptowanego przez Enterprise.
        
        Algorytm:
        1. Konwersja przestrzeni barw (CMYK -> sRGB dla Web).
        2. Skalowanie z zachowaniem proporcji (LANCZOS).
        3. Dodanie białego paddingu (canvas), aby uzyskać idealny kwadrat.
        4. Usunięcie metadanych EXIF (sanityzacja).
        """
        try:
            img = Image.open(io.BytesIO(image_bytes))

            # 1. Zarządzanie kolorem (Color Management)
            if img.mode == "CMYK":
                logger.info("Converting CMYK to sRGB")
                img = ImageCms.profileToProfile(img, "USWebCoatedSWOP.icc", "sRGB.icc", renderingIntent=0, outputMode="RGB")
            elif img.mode == "P" or img.mode == "RGBA":
                # Konwersja przezroczystości na białe tło
                if force_white_background:
                    background = Image.new("RGB", img.size, (255, 255, 255))
                    background.paste(img, mask=img.split()[3] if img.mode == 'RGBA' else None)
                    img = background
                else:
                    img = img.convert("RGB")

            # 2. Skalowanie (Thumbnail) z zachowaniem aspektu
            img.thumbnail(target_size, Image.Resampling.LANCZOS)

            # 3. Padding (Smart Canvas)
            # Obliczamy ile brakuje do idealnego kwadratu lub zadanego wymiaru
            delta_w = target_size[0] - img.size[0]
            delta_h = target_size[1] - img.size[1]
            
            # Centrowanie
            padding = (
                delta_w // 2, 
                delta_h // 2, 
                delta_w - (delta_w // 2), 
                delta_h - (delta_h // 2)
            )
            
            new_img = ImageOps.expand(img, padding, fill="white")

            # 4. Zapis do bufora
            output = io.BytesIO()
            new_img.save(
                output, 
                format=output_format, 
                quality=92,  # High Quality dla eCommerce
                optimize=True,
                subsampling=0 # Zachowanie ostrości kolorów (4:4:4 chroma subsampling)
            )
            
            logger.info(f"Image processed successfully. Final size: {new_img.size}")
            return output.getvalue()

        except Exception as e:
            logger.error(f"Critical Error in Image Processing: {str(e)}")
            raise ValueError("Image processing failed due to technical error.")

# Przykład użycia w serwisie (Service Layer):
# agent = ImageStandardizer()
# clean_bytes = agent.normalize_for_marketplace(raw_bytes, target_size=(2500, 2500))
