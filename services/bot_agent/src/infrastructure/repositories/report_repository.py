import httpx
from src.core.config import settings
import datetime

class ReportRepository:
    @staticmethod
    def create_report(nombre: str, numero: str, problema: str, link_whatsapp: str):
        now = datetime.datetime.now()
        report_data = {
            "hora": now.strftime("%H:%M:%S"),
            "fecha": now.strftime("%Y-%m-%d"),
            "nombre": nombre,
            "numero": str(numero),
            "problema": problema,
            "link a whatsapp": link_whatsapp
        }
        
        try:
            response = httpx.post(
                f"{settings.NOCODB_REPORTES_URL}?insertAt=0",
                headers={"xc-token": settings.NOCODB_TOKEN},
                json={"fields": report_data},
                timeout=10.0
            )
            response.raise_for_status()
            return True, response.json()
        except Exception as e:
            print(f"Error enviando reporte NocoDB: {e}")
            return False, str(e)
