from fastapi import APIRouter
from pydantic import BaseModel
import fitz  # PyMuPDF
import base64
import tempfile
import os

router = APIRouter()

class PdfPayload(BaseModel):
    pdf_base64: str
    dpi: int = 150

@router.post("/convert")
async def convert_pdf_to_images(payload: PdfPayload):
    """
    Convierte un PDF (base64) a una lista de imágenes en base64 limpio.
    Ideal para que Deno las suba a Base44.
    """
    try:
        # 1. Crear archivo temporal con el PDF
        pdf_bytes = base64.b64decode(payload.pdf_base64)
        temp_pdf = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        temp_pdf.write(pdf_bytes)
        temp_pdf.close()

        # 2. Abrir PDF
        pdf_doc = fitz.open(temp_pdf.name)
        images_base64 = []

        # 3. Convertir cada página a JPG base64
        for page_number in range(len(pdf_doc)):
            page = pdf_doc.load_page(page_number)

            # Renderizamos con la escala correcta según DPI
            zoom = payload.dpi / 72
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)

            image_bytes = pix.tobytes("jpg")
            image_b64 = base64.b64encode(image_bytes).decode("utf-8")

            images_base64.append(image_b64)

        # 4. Limpiar
        pdf_doc.close()
        os.remove(temp_pdf.name)

        return {
            "success": True,
            "page_count": len(images_base64),
            "images_base64": images_base64
        }

    except Exception as e:
        return {"success": False, "error": str(e)}

