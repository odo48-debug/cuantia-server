from fastapi import FastAPI, Request
from routers import pdf2img, ine, risk, html2pdf

app = FastAPI(
    title="Cuantia Server",
    description="Backend unificado para PDF → Imagen, INE y Riesgos Naturales",
    version="1.0.0"
)

# 🧩 Middleware global: desactiva compresión Brotli/Gzip en /pdf2img/convert
@app.middleware("http")
async def disable_compression_for_pdf2img(request: Request, call_next):
    response = await call_next(request)

    # Solo aplicar a la ruta de conversión de PDF a imagen
    if request.url.path.startswith("/pdf2img/convert"):
        response.headers["Content-Encoding"] = "identity"

    return response


# 🔗 Routers principales
app.include_router(html2pdf.router, prefix="/convert", tags=["HTML to PDF"])
app.include_router(pdf2img.router, prefix="/pdf2img", tags=["PDF → Imagen"])
app.include_router(ine.router, prefix="/ine", tags=["INE Municipios"])
app.include_router(risk.router, prefix="/risk", tags=["Riesgos Naturales"])


# 🏠 Ruta raíz informativa
@app.get("/")
def root():
    return {
        "message": "🚀 Cuantia-server en funcionamiento",
        "endpoints": {
            "pdf2img": "/pdf2img/convert",
            "ine": "/ine/municipio/{municipio}",
            "risk": "/risk/api/risk_clean",
            "html2pdf": "/convert/html-to-pdf"
        }
    }
