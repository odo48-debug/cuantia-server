import os, tempfile, io, zipfile, json
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import Response, StreamingResponse
from landingai_ade import LandingAIADE
from PIL import Image, ImageDraw
import pymupdf

router = APIRouter()
client = LandingAIADE(apikey=os.environ.get("VISION_AGENT_API_KEY"))

CHUNK_TYPE_COLORS = {
    "chunkText": (40, 167, 69), "chunkTable": (0, 123, 255), 
    "chunkMarginalia": (111, 66, 193), "chunkFigure": (255, 0, 255),
    "chunkLogo": (144, 238, 144), "chunkCard": (255, 165, 0),
    "chunkAttestation": (0, 255, 255), "chunkScanCode": (255, 193, 7),
    "chunkForm": (220, 20, 60), "tableCell": (173, 216, 230),
    "table": (70, 130, 180)
}

@router.post("/extract", summary="Procesado IA + Generación de visualización")
async def extract_and_visualize(file: UploadFile = File(...)):
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Solo se admiten archivos PDF")

    try:
        suffix = Path(file.filename).suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(await file.read())
            tmp_path = Path(tmp.name)

        # 1. LLAMADA A LANDINGAI
        parse_response = client.parse(document=tmp_path, model="dpt-2-latest")

        # 2. DIBUJO LOCAL
        pdf = pymupdf.open(tmp_path)
        processed_images = []

        for page_num in range(len(pdf)):
            page = pdf[page_num]
            pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2)) 
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            draw = ImageDraw.Draw(img)
            
            for gid, grounding in parse_response.grounding.items():
                if grounding.page == page_num:
                    box = grounding.box
                    x1, y1 = int(box.left * img.width), int(box.top * img.height)
                    x2, y2 = int(box.right * img.width), int(box.bottom * img.height)
                    color = CHUNK_TYPE_COLORS.get(grounding.type, (128, 128, 128))
                    draw.rectangle([x1, y1, x2, y2], outline=color, width=4)
            
            processed_images.append(img)

        # 3. RESPUESTA (PNG o ZIP)
        if len(processed_images) == 1:
            img_byte_arr = io.BytesIO()
            processed_images[0].save(img_byte_arr, format='PNG')
            pdf.close()
            os.unlink(tmp_path)
            return Response(content=img_byte_arr.getvalue(), media_type="image/png")

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
            for i, img in enumerate(processed_images):
                img_byte_arr = io.BytesIO()
                img.save(img_byte_arr, format='PNG')
                zip_file.writestr(f"paginas_anotadas/pagina_{i+1}.png", img_byte_arr.getvalue())
            zip_file.writestr("texto_extraido.md", parse_response.markdown)
            zip_file.writestr("datos_completos.json", parse_response.model_dump_json())

        pdf.close()
        os.unlink(tmp_path)
        zip_buffer.seek(0)
        
        return StreamingResponse(
            zip_buffer, 
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename=analisis_{file.filename}.zip"}
        )

    except Exception as e:
        if 'tmp_path' in locals() and tmp_path.exists(): os.unlink(tmp_path)
        raise HTTPException(status_code=500, detail=str(e))
