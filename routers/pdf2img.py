from fastapi import APIRouter, UploadFile, File
from typing import List
import fitz  # PyMuPDF
import base64
import tempfile
import os

router = APIRouter()

@router.post("/convert/")
async def convert_multiple_pdfs(files: List[UploadFile] = File(...)):
    """
    Convierte múltiples PDFs a imágenes (una por página) y devuelve base64.
    """
    all_images = []

    for file in files:
        if not file.filename.lower().endswith(".pdf"):
            continue

        temp_pdf = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        temp_pdf.write(await file.read())
        temp_pdf.close()

        pdf_doc = fitz.open(temp_pdf.name)

        for page_number in range(len(pdf_doc)):
            page = pdf_doc.load_page(page_number)
            pix = page.get_pixmap(dpi=150)
            image_bytes = pix.tobytes("jpg")
            image_b64 = base64.b64encode(image_bytes).decode("utf-8")

            all_images.append({
                "source_pdf": file.filename,
                "page": page_number + 1,
                "image_base64": image_b64
            })

        pdf_doc.close()
        os.remove(temp_pdf.name)

    return {"images": all_images}
