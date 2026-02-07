import os
import tempfile
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException
from landingai_ade import LandingAIADE

router = APIRouter()

# El cliente se inicializa una sola vez
# Asegúrate de que VISION_AGENT_API_KEY esté en las variables de entorno de Render
client = LandingAIADE(apikey=os.environ.get("VISION_AGENT_API_KEY"))

@router.post("/extract", summary="Extraer JSON estructurado de un documento")
async def extract_document(file: UploadFile = File(...)):
    """
    Sube un PDF o Imagen para obtener su análisis estructurado (DPT-2).
    """
    if not file.filename.lower().endswith(('.pdf', '.png', '.jpg', '.jpeg')):
        raise HTTPException(status_code=400, detail="Formato no soportado. Use PDF o Imagen.")

    try:
        # Guardar temporalmente el archivo subido
        suffix = Path(file.filename).suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = Path(tmp.name)

        # Llamada síncrona al SDK de LandingAI
        # Nota: .parse() bloquea hasta que el documento está listo
        parse_response = client.parse(
            document=tmp_path,
            model="dpt-2"
        )

        # Limpieza
        os.unlink(tmp_path)

        # Retornamos el resultado (FastAPI serializa automáticamente el objeto Pydantic)
        return parse_response

    except Exception as e:
        if 'tmp_path' in locals() and tmp_path.exists():
            os.unlink(tmp_path)
        raise HTTPException(status_code=500, detail=f"Error en LandingAI: {str(e)}")
