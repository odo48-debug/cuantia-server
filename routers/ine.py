from fastapi import APIRouter, Query
import httpx, asyncio, time, re, unicodedata

router = APIRouter()

# --- CONSTANTES DE CONFIGURACIÓN ---

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

ARTICULOS_DEFINIDOS = [
    "o ", "a ", "os ", "as ",  # Gallego / Portugués
    "el ", "la ", "los ", "las ", # Castellano
    "els ", "les ", "es " # Catalán (y otras variantes)
]

CACHE_TTL = 3600
cache = {}

# --- FUNCIONES AUXILIARES ---

def normalizar(texto: str) -> str:
    """Normaliza texto eliminando tildes, pasando a minúsculas y limpiando."""
    if not texto:
        return ""
    texto = texto.lower().strip()
    return "".join(c for c in unicodedata.normalize("NFD", texto)
                   if unicodedata.category(c) != "Mn")

def coincide_municipio(nombre_serie: str, municipio: str) -> bool:
    """
    Verifica si una serie del INE corresponde al municipio dado.
    Implementa la limpieza de artículos iniciales para mejorar la coincidencia.
    """
    
    # 1. Normalización de ambos textos
    nombre = re.sub(r"[^a-z0-9áéíóúüñ\s-]", " ", normalizar(nombre_serie))
    muni = re.sub(r"[^a-z0-9áéíóúüñ\s-]", " ", normalizar(municipio))
    nombre = re.sub(r"\s+", " ", nombre).strip()
    muni = re.sub(r"\s+", " ", muni).strip()

    # 2. Eliminación Generalizada del Artículo
    muni_limpio = muni
    for art in ARTICULOS_DEFINIDOS:
        if muni_limpio.startswith(art):
            muni_limpio = muni_limpio[len(art):].strip()
            # Una vez que se elimina un artículo, se asume que es la forma correcta.
            break 
    
    # 3. Lógica de Coincidencia (usando la versión limpia)
    # Comprueba si el nombre de la serie comienza con la versión limpia + un espacio (ej: "porriño (pontevedra)")
    # O si el nombre de la serie comienza con la versión original (por si el INE sí incluyó el artículo)
    # NOTA: Se mantiene la lógica original de 'muni + " "' para asegurar que solo coincida con el nombre completo.
    
    return nombre.startswith(muni_limpio + " ") or nombre.startswith(muni + " ") or nombre == muni_limpio or nombre == muni

def filtrar_series(series, excluir=None):
    """Filtra series basándose en una lista de palabras a excluir."""
    if not excluir:
        return series
    return [s for s in series if not any(p.lower() in s.get("Nombre", "").lower() for p in excluir)]

# --- FUNCIONES PRINCIPALES DE CONSULTA ---

async def get_json_async(url: str, timeout: int = 15):
    """Realiza una petición HTTP asíncrona y devuelve JSON."""
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.json()

async def get_series_municipio(tabla_id: str, municipio: str):
    """Obtiene y filtra las series de una tabla específica para un municipio."""
    url = f"https://servicios.ine.es/wstempus/jsCache/ES/SERIES_TABLA/{tabla_id}"
    data = await get_json_async(url)
    return [s for s in data if coincide_municipio(s.get("Nombre", ""), municipio)]

async def get_datos_serie(codigo: str, n_last: int = 3):
    """Obtiene los últimos N datos de una serie por su código."""
    url = f"https://servicios.ine.es/wstempus/jsCache/ES/DATOS_SERIE/{codigo}?nult={n_last}"
    return await get_json_async(url)

async def get_datos_municipio(municipio: str, n_last: int = 3):
    """Obtiene todos los datos de las tablas municipales para un municipio, usando caché."""
    now = time.time()
    if municipio in cache:
        ts, data = cache[municipio]
        if now - ts < CACHE_TTL:
            return data
            
    resultados = {}
    
    # 1. Tareas para obtener todas las series de las tablas
    tareas = [asyncio.create_task(get_series_municipio(t, municipio)) 
              for t in TABLAS_MUNICIPALES.values()]
    
    todas_series = await asyncio.gather(*tareas, return_exceptions=True)
    
    # 2. Procesamiento de las series y obtención de datos
    for idx, series in enumerate(todas_series):
        nombre_indicador = list(TABLAS_MUNICIPALES.keys())[idx]
        
        if isinstance(series, Exception):
            resultados[nombre_indicador] = {"error": str(series)}
            continue
            
        # Opcionalmente, se podría aplicar aquí la función 'filtrar_series'
        
        datos_tabla = {}
        # Limitar a las primeras 3 series encontradas (como en el original)
        for s in series[:3]: 
            cod = s.get("COD")
            nombre = s.get("Nombre")
            
            if not cod or not nombre:
                continue
                
            try:
                datos = await get_datos_serie(cod, n_last=n_last)
                datos_tabla[nombre] = datos
            except Exception as e:
                datos_tabla[nombre] = {"error": f"Error al obtener datos: {str(e)}"}
                
        resultados[nombre_indicador] = datos_tabla
        
    cache[municipio] = (now, resultados)
    return resultados

# --- ENDPOINT DE FASTAPI ---

@router.get("/municipio/{municipio}")
async def consulta_municipio(municipio: str, n_last: int = Query(3)):
    """Endpoint para consultar todos los datos municipales del INE."""
    try:
        datos = await get_datos_municipio(municipio, n_last=n_last)
        return {"status": "ok", "municipio": municipio, "datos": datos}
    except Exception as e:
        # Esto captura errores de alto nivel, como un fallo en la conexión inicial
        return {"status": "error", "message": f"Fallo al procesar la solicitud: {str(e)}"}
