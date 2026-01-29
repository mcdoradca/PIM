"""
Google Drive Storage Agent (Adapter)
Odpowiedzialność: Przesyłanie przetworzonych zdjęć na Google Drive (Google One)
i generowanie publicznych linków (shareable links) dla Allegro/Rossmanna.

Wymaga: Google Service Account Credentials.
"""

import os
import json
import base64
import logging
import io
from typing import Optional
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from googleapiclient.errors import HttpError

logger = logging.getLogger("GoogleDriveAgent")

# Zdefiniuj zakres uprawnień (Scope)
SCOPES = ['https://www.googleapis.com/auth/drive']

class GoogleDriveAdapter:
    def __init__(self, folder_id: str):
        """
        Inicjalizacja klienta Google Drive.
        
        :param folder_id: ID folderu na Twoim Google Drive, gdzie mają trafiać zdjęcia.
                          (Folder musi być udostępniony dla adresu email Service Account!)
        """
        self.folder_id = folder_id
        self.service = self._authenticate()

    def _authenticate(self):
        """
        Pobiera poświadczenia z zakodowanej zmiennej środowiskowej GOOGLE_CREDENTIALS_B64.
        Jest to bezpieczniejsza metoda dla platformy Render niż trzymanie pliku .json.
        """
        encoded_creds = os.getenv("GOOGLE_CREDENTIALS_B64")
        
        if not encoded_creds:
            logger.error("Brak zmiennej GOOGLE_CREDENTIALS_B64. Nie można połączyć z Drive.")
            return None

        try:
            # Dekodowanie Base64 -> JSON string -> Dict
            creds_json = base64.b64decode(encoded_creds).decode('utf-8')
            creds_dict = json.loads(creds_json)
            
            creds = service_account.Credentials.from_service_account_info(
                creds_dict, scopes=SCOPES
            )
            return build('drive', 'v3', credentials=creds)
        except Exception as e:
            logger.error(f"Błąd autoryzacji Google Drive: {str(e)}")
            raise e

    def _find_file(self, filename: str) -> Optional[str]:
        """Sprawdza, czy plik już istnieje w folderze, aby uniknąć duplikatów."""
        query = f"name = '{filename}' and '{self.folder_id}' in parents and trashed = false"
        results = self.service.files().list(q=query, fields="files(id)").execute()
        files = results.get('files', [])
        if files:
            return files[0]['id']
        return None

    def _make_public(self, file_id: str):
        """Nadaje uprawnienia publiczne plikowi, aby Rossmann/Allegro mogły go pobrać."""
        try:
            self.service.permissions().create(
                fileId=file_id,
                body={'role': 'reader', 'type': 'anyone'},
                fields='id'
            ).execute()
        except HttpError as e:
            logger.warning(f"Nie udało się nadać uprawnień publicznych: {e}")

    def upload_file(self, filename: str, content: bytes, mime_type: str = "image/jpeg") -> str:
        """
        Wysyła plik (bytes) na Google Drive i zwraca bezpośredni link (webContentLink).
        """
        if not self.service:
            raise ConnectionError("Google Drive Service not initialized")

        try:
            # Metadata pliku
            file_metadata = {
                'name': filename,
                'parents': [self.folder_id]
            }

            media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mime_type, resumable=True)

            # Sprawdź czy plik istnieje (Update vs Create)
            existing_file_id = self._find_file(filename)

            if existing_file_id:
                logger.info(f"Updating existing file: {filename} ({existing_file_id})")
                file = self.service.files().update(
                    fileId=existing_file_id,
                    media_body=media,
                    fields='id, webContentLink, webViewLink'
                ).execute()
                file_id = existing_file_id
            else:
                logger.info(f"Uploading new file: {filename}")
                file = self.service.files().create(
                    body=file_metadata,
                    media_body=media,
                    fields='id, webContentLink, webViewLink'
                ).execute()
                file_id = file.get('id')

            # Upewnij się, że plik jest publiczny
            self._make_public(file_id)

            # Pobierz świeże linki po nadaniu uprawnień
            # webContentLink = bezpośrednie pobieranie (lepsze dla API)
            # webViewLink = podgląd w przeglądarce
            res = self.service.files().get(fileId=file_id, fields='webContentLink').execute()
            
            public_link = res.get('webContentLink')
            logger.info(f"File available at: {public_link}")
            
            return public_link

        except HttpError as error:
            logger.error(f"Google Drive API Error: {error}")
            raise error

# Przykład użycia (Integration Pattern):
# drive_agent = GoogleDriveAdapter(folder_id="12345abcdef...")
# link = drive_agent.upload_file("produkt_123.jpg", image_bytes)
