from fastapi import APIRouter, Query
import httpx, asyncio, time, re, unicodedata

router = APIRouter()

TABLAS_MUNICIPALES = {
    "poblacion_municipio": "29005",
    "viviendas_por_municipio": "3456",
    "indicadores_urbanos": "69303",
    "indicadores_urbanos_2": "69336",
    "hogares_vivienda": "69302",
    "superficie_uso_suelo": "69305",
    "distribucion_renta": "30904"
}

FILTRO_EXCLUIR = [
    "ocupados", "consumo", "Censo", "censo", "vacías",
    "convencionales", "Mediana", "cuartil"
]

CACHE_TTL = 3600
cache = {}

def normalizar(texto: str) -> str:
    if not texto:
        return ""
    texto = texto.lower().strip()
    return "".join(c for c in unicodedata.normalize("NFD", texto)
                   if unicodedata.category(c) != "Mn")

def coincide_municipio(nombre_serie: str, municipio: str) -> bool:
    nombre = re.sub(r"[^a-z0-9áéíóúüñ\s-]", " ", normalizar(nombre_serie))
    muni = re.sub(r"[^a-z0-9áéíóúüñ\s-]", " ", normalizar(municipio))
    nombre = re.sub(r"\s+", " ", nombre).strip()
    muni = re.sub(r"\s+", " ", muni).strip()
    return nombre.startswith(muni + " ") or nombre == muni

async def get_json_async(url: str, timeout: int = 15):
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.json()

async def get_series_municipio(tabla_id: str, municipio: str):
    url = f"https://servicios.ine.es/wstempus/jsCache/ES/SERIES_TABLA/{tabla_id}"
    data = await get_json_async(url)
    return [s for s in data if coincide_municipio(s.get("Nombre", ""), municipio)]

def filtrar_series(series, excluir=None):
    if not excluir:
        return series
    return [s for s in series if not any(p.lower() in s.get("Nombre", "").lower() for p in excluir)]

async def get_datos_serie(codigo: str, n_last: int = 3):
    url = f"https://servicios.ine.es/wstempus/jsCache/ES/DATOS_SERIE/{codigo}?nult={n_last}"
    return await get_json_async(url)

async def get_datos_municipio(municipio: str, n_last: int = 3):
    now = time.time()
    if municipio in cache:
        ts, data = cache[municipio]
        if now - ts < CACHE_TTL:
            return data
    resultados = {}
    tareas = [asyncio.create_task(get_series_municipio(t, municipio)) for t in TABLAS_MUNICIPALES.values()]
    todas_series = await asyncio.gather(*tareas, return_exceptions=True)
    for idx, series in enumerate(todas_series):
        nombre_indicador = list(TABLAS_MUNICIPALES.keys())[idx]
        if isinstance(series, Exception):
            resultados[nombre_indicador] = {"error": str(series)}
            continue
        datos_tabla = {}
        for s in series[:3]:
            cod = s.get("COD")
            nombre = s.get("Nombre")
            if not cod or not nombre:
                continue
            try:
                datos = await get_datos_serie(cod, n_last=n_last)
                datos_tabla[nombre] = datos
            except Exception as e:
                datos_tabla[nombre] = {"error": str(e)}
        resultados[nombre_indicador] = datos_tabla
    cache[municipio] = (now, resultados)
    return resultados

@router.get("/municipio/{municipio}")
async def consulta_municipio(municipio: str, n_last: int = Query(3)):
    try:
        datos = await get_datos_municipio(municipio, n_last=n_last)
        return {"status": "ok", "municipio": municipio, "datos": datos}
    except Exception as e:
        return {"status": "error", "message": str(e)}
