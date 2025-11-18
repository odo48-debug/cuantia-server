from fastapi import APIRouter
from pydantic import BaseModel
from weasyprint import HTML
import base64

router = APIRouter()

class HtmlPayload(BaseModel):
    html: str

@router.post("/html-to-pdf")
async def html_to_pdf(payload: HtmlPayload):
    """
    Recibe HTML con estilos completos y devuelve un PDF en base64.
    """
    try:
        # Convertir HTML a PDF (bytes)
        pdf_bytes = HTML(string=payload.html).write_pdf()

        # Convertir PDF a base64
        pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")

        return {
            "success": True,
            "pdf_base64": pdf_b64
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }
