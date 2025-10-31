from fastapi import FastAPI
from routers import pdf2img, ine, risk

app = FastAPI(
    title="Cuantia Server",
    description="Backend unificado para PDF → Imagen, INE y Riesgos Naturales",
    version="1.0.0"
)

app.include_router(pdf2img.router, prefix="/pdf2img", tags=["PDF → Imagen"])
app.include_router(ine.router, prefix="/ine", tags=["INE Municipios"])
app.include_router(risk.router, prefix="/risk", tags=["Riesgos Naturales"])

@app.get("/")
def root():
    return {
        "message": "🚀 Cuantia-server en funcionamiento",
        "endpoints": {
            "pdf2img": "/pdf2img/convert",
            "ine": "/ine/municipio/{municipio}",
            "risk": "/risk/api/risk_clean"
        }
    }
