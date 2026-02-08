import os
import tempfile
import io
import zipfile
import json
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse
from landingai_ade import LandingAIADE
from PIL import Image, ImageDraw
import pymupdf

router = APIRouter()

# Inicialización del cliente de LandingAI
# Render leerá automáticamente esta variable de entorno
client = LandingAIADE(apikey=os.environ.get("VISION_AGENT_API_KEY"))

# Paleta de colores para identificar cada tipo de elemento en el PDF
CHUNK_TYPE_COLORS = {
    "chunkText": (40, 167, 69),        # Verde
    "chunkTable": (0, 123, 255),       # Azul
    "chunkMarginalia": (111, 66, 193), # Púrpura
    "chunkFigure": (255, 0, 255),      # Magenta
    "chunkLogo": (144, 238, 144),      # Verde claro
    "chunkCard": (255, 165, 0),        # Naranja
    "chunkAttestation": (0, 255, 255), # Cian
    "chunkScanCode": (255, 193, 7),    # Amarillo
    "chunkForm": (220, 20, 60),        # Rojo
    "tableCell": (173, 216, 230),      # Azul claro
    "table": (70, 130, 180),           # Azul acero
}

@router.post("/extract", summary="Procesa PDF, dibuja cajas y devuelve un ZIP")
async def extract_and_visualize(file: UploadFile = File(...)):
    """
    Este endpoint hace todo el flujo:
    1. Envía el PDF a LandingAI para obtener coordenadas y texto.
    2. Renderiza el PDF localmente y dibuja las Bounding Boxes.
    3. Empaqueta imágenes, Markdown y JSON en un archivo ZIP.
    """
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Formato no soportado. Debe ser un archivo PDF.")

    try:
        # 1. Guardar el archivo subido en un directorio temporal
        suffix = Path(file.filename).suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = Path(tmp.name)

        # 2. Llamada a la API de LandingAI (Analiza la estructura del documento)
        parse_response = client.parse(document=tmp_path, model="dpt-2-latest")

        # 3. Procesamiento local de imágenes con PyMuPDF y Pillow
        pdf = pymupdf.open(tmp_path)
        processed_images = []

        for page_num in range(len(pdf)):
            page = pdf[page_num]
            # Renderizar página a imagen (escala 2x para buena resolución)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2))
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            draw = ImageDraw.Draw(img)
            
            # Dibujar los recuadros usando las coordenadas del JSON
            # El diccionario .grounding contiene las coordenadas por cada ID
            for gid, grounding in parse_response.grounding.items():
                if grounding.page == page_num:
                    box = grounding.box
                    # Convertir coordenadas normalizadas (0.0-1.0) a píxeles
                    x1 = int(box.left * img.width)
                    y1 = int(box.top * img.height)
                    x2 = int(box.right * img.width)
                    y2 = int(box.bottom * img.height)
                    
                    color = CHUNK_TYPE_COLORS.get(grounding.type, (128, 128, 128))
                    draw.rectangle([x1, y1, x2, y2], outline=color, width=4)
            
            processed_images.append(img)

        # 4. Generación del archivo ZIP en memoria
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
            # Añadir PNGs anotados
            for i, img in enumerate(processed_images):
                img_io = io.BytesIO()
                img.save(img_io, format='PNG')
                zip_file.writestr(f"paginas_anotadas/pagina_{i+1}.png", img_io.getvalue())

            # Añadir el texto Markdown extraído
            zip_file.writestr("texto_extraido.md", parse_response.markdown)

            # Añadir el JSON original con toda la estructura de datos
            zip_file.writestr("datos_completos.json", parse_response.model_dump_json())

        # 5. Limpieza y Retorno
        pdf.close()
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

        zip_buffer.seek(0)
        filename_base = Path(file.filename).stem
        
        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={
                "Content-Disposition": f"attachment; filename=analisis_{filename_base}.zip"
            }
        )

    except Exception as e:
        if 'tmp_path' in locals() and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise HTTPException(status_code=500, detail=f"Error en el procesamiento: {str(e)}")
