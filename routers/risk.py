from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
import math
import httpx
from typing import Dict, Any, List, Optional

router = APIRouter()

# --- Constantes del Servicio WMS ---
DPMT_WMS_URL = "https://wms.mapama.gob.es/sig/Costas/DPMT"
DPMT_LAYER_NAME = "AM.CoastalZoneManagementArea"


# --- CONFIGURACIÓN DE PONDERACIÓN Y MAPEO DE RIESGOS ---
RISK_WEIGHTS = {
    # Peso económico/estructural asignado a cada tipo de riesgo
    "R1_inundacion": 0.40,  # Alta prioridad
    "R2_dpmt_costas": 0.30, # Media/Alta (limitaciones de uso)
    "R3_incendios": 0.20,   # Media
    "R4_sismico": 0.10      # Baja/Media (depende de la zona)
}

# Mapeo de Periodo de Retorno a Puntuación (Score) para Inundaciones
FLOOD_SCORE_MAP = {
    "T10": 10,  # Riesgo Alto (Periodo 10 años)
    "T100": 7,  # Riesgo Medio (Periodo 100 años)
    "T500": 5   # Riesgo Bajo (Periodo 500 años)
}
# -----------------------------------------------------------------


# =========================
# Utilidades comunes
# =========================

def to_webmercator(lat: float, lon: float):
    """Convierte lat/lon (grados WGS84) a Web Mercator (EPSPS:3857)."""
    R = 6378137.0
    x = lon * (math.pi / 180.0) * R
    y = math.log(math.tan((math.pi / 4.0) + (lat * math.pi / 360.0))) * R
    return x, y


def build_gfi_url(
    wms_url: str,
    layer: str,
    bbox: str,
    crs: str,
    width: int = 1,  
    height: int = 1,
    info_format: str = "application/json",
    styles: Optional[str] = None,
    feature_count: int = 10,
    vendor_params: Optional[Dict[str, Any]] = None,
) -> str:
    # Ajustamos I y J a 0, 0 ya que width=1 y height=1
    base = (
        f"{wms_url}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetFeatureInfo"
        f"&LAYERS={layer}&QUERY_LAYERS={layer}"
        f"&CRS={crs}&BBOX={bbox}"
        f"&WIDTH={width}&HEIGHT={height}"
        f"&I=0&J=0"  
        f"&INFO_FORMAT={info_format}"
        f"&FEATURE_COUNT={feature_count}"
    )
    if styles:
        base += f"&STYLES={styles}"
    if vendor_params:
        for k, v in vendor_params.items():
            base += f"&{k}={v}"
    return base


async def fetch_any(client: httpx.AsyncClient, urls: List[str]) -> Dict[str, Any]:
    last_err = None
    for u in urls:
        try:
            r = await client.get(u, follow_redirects=True, timeout=25.0)
            r.raise_for_status()
            try:
                return r.json()
            except Exception:
                # Si no es JSON, devolvemos el texto crudo 
                return {"raw": r.text}  
        except Exception as e:
            last_err = str(e)
    return {"error": last_err or "unknown error"}


def remove_geometry_from_geojson(obj: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(obj, dict):
        return obj
    if obj.get("type") == "FeatureCollection":
        feats = []
        for f in obj.get("features", []):
            if isinstance(f, dict):
                # Incluye solo las propiedades, excluyendo 'geometry'
                feats.append({k: v for k, v in f.items() if k != "geometry"})
        return {"type": "FeatureCollection", "features": feats}
    if obj.get("type") == "Feature":
        return {k: v for k, v in obj.items() if k != "geometry"}
    return obj


# =========================
# LÓGICA DE CÁLCULO DE RIESGO
# =========================

def calculate_risk_level(raw_risks: Dict[str, Any]) -> int:
    """
    Convierte el resultado crudo de WMS a una Puntuación Compuesta (0-10)
    y la mapea al Nivel de Riesgo (1=Bajo, 2=Medio, 3=Alto) para K_CM.
    """
    composite_score = 0.0

    # A. RIESGO DE INUNDACIÓN (R1: Fluvial + Marina) - Peso 0.40
    # Se toma el score más alto de cualquier tipo de inundación
    max_flood_score = 0
    
    # 1. Inundación Fluvial y Marina
    flood_data = {**raw_risks.get("inundacion_fluvial", {}), **raw_risks.get("inundacion_marina", {})}
    
    for periodo, data in flood_data.items():
        if isinstance(data, dict) and data.get("features"):
            score = FLOOD_SCORE_MAP.get(periodo, 0)
            max_flood_score = max(max_flood_score, score)
    
    composite_score += max_flood_score * RISK_WEIGHTS["R1_inundacion"]


    # B. DOMINIO PÚBLICO MARÍTIMO TERRESTRE (R2) - Peso 0.30
    # Si hay features (está dentro del DPMT o su servidumbre), es un riesgo de alto impacto legal/económico
    dpmt_data = raw_risks.get("dominio_publico_maritimo_terrestre", {})
    score_dpmt = 10 if isinstance(dpmt_data, dict) and dpmt_data.get("features") else 0
    composite_score += score_dpmt * RISK_WEIGHTS["R2_dpmt_costas"]


    # C. INCENDIOS (R3) - Peso 0.20
    incendios_data = raw_risks.get("incendios", {})
    # Si hay una feature de riesgo, puntuamos alto (8)
    score_incendio = 8 if isinstance(incendios_data, dict) and incendios_data.get("features") else 0
    composite_score += score_incendio * RISK_WEIGHTS["R3_incendios"]


    # D. RIESGO SÍSMICO (R4) - Peso 0.10
    sismico_data = raw_risks.get("sismico", {})
    # Si hay una feature (zona de riesgo), puntuamos medio (5)
    score_sismico = 5 if isinstance(sismico_data, dict) and sismico_data.get("features") else 0
    composite_score += score_sismico * RISK_WEIGHTS["R4_sismico"]

    
    # --- MAPEO FINAL DEL SCORE COMPUESTO AL NIVEL (1, 2, 3) ---
    # Escala basada en un máximo teórico de 10.0
    if composite_score <= 3.0:
        return 1  # Bajo (Usar K_CM con valor 1)
    elif composite_score <= 6.0:
        return 2  # Medio (Usar K_CM con valor 2)
    else: 
        return 3  # Alto (Usar K_CM con valor 3)


# =========================
# Core fetch
# =========================

async def fetch_all_risks(lat: float, lon: float) -> Dict[str, Any]:
    results: Dict[str, Any] = {}
    async with httpx.AsyncClient() as client:
        
        # Definición de la BBOX para consulta puntual
        d_deg_inmueble = 0.00006  
        bbox_inmueble = f"{lon - d_deg_inmueble},{lat - d_deg_inmueble},{lon + d_deg_inmueble},{lat + d_deg_inmueble}"
        point_query_params = {"width": 1, "height": 1}  
        

        # === RIESGO 1: Dominio Público Marítimo Terrestre (DPMT) ===
        url_dpmt = build_gfi_url(
            DPMT_WMS_URL, DPMT_LAYER_NAME, 
            bbox=bbox_inmueble, crs="CRS:84",
            info_format="application/json",
            **point_query_params
        )
        results["dominio_publico_maritimo_terrestre"] = await fetch_any(client, [url_dpmt])


        # === RIESGO 2: Incendios ===
        url_inc = build_gfi_url(
            "https://wms.mapama.gob.es/sig/Biodiversidad/Incendios/2006_2015",
            "NZ.HazardArea", 
            bbox=bbox_inmueble, crs="CRS:84",
            info_format="application/json", styles="Biodiversidad_Incendios",
            **point_query_params
        )
        results["incendios"] = await fetch_any(client, [url_inc])


        # === RIESGO 3: Inundaciones fluviales ===
        results["inundacion_fluvial"] = {}
        for periodo in ["T10", "T100", "T500"]:
            url = build_gfi_url(
                "https://servicios.idee.es/wms-inspire/riesgos-naturales/inundaciones",
                f"NZ.Flood.Fluvial{periodo}", 
                bbox=bbox_inmueble, crs="CRS:84",
                info_format="application/json",
                **point_query_params
            )
            results["inundacion_fluvial"][periodo] = await fetch_any(client, [url])

        
        # === RIESGO 4: Inundaciones marinas ===
        results["inundacion_marina"] = {}
        for periodo in ["T100", "T500"]:
            url = build_gfi_url(
                "https://servicios.idee.es/wms-inspire/riesgos-naturales/inundaciones",
                f"NZ.Flood.Marina{periodo}", 
                bbox=bbox_inmueble, crs="CRS:84",
                info_format="application/json",
                **point_query_params
            )
            results["inundacion_marina"][periodo] = await fetch_any(client, [url])

        
        # === RIESGO 5: Riesgo sísmico ===
        url_sismico = build_gfi_url(
            "https://www.ign.es/wms-inspire/geofisica",
            "HazardArea2002.NCSE-02", 
            bbox=bbox_inmueble, crs="CRS:84",
            info_format="application/json",
            **point_query_params
        )
        results["sismico"] = await fetch_any(client, [url_sismico])


        # === RIESGO 6: Desertificación (Potencial y Laminar) ===
        # Nota: Estos se fetchan, pero no se ponderan directamente en el score final (W=0)
        url_des_pot = build_gfi_url(
            "https://wms.mapama.gob.es/sig/Biodiversidad/INESErosionPotencial/wms.aspx",
            "NZ.HazardArea", 
            bbox=bbox_inmueble, crs="CRS:84",
            info_format="text/plain",
            **point_query_params
        )
        url_des_lam = build_gfi_url(
            "https://wms.mapama.gob.es/sig/Biodiversidad/INESErosionLaminarRaster/wms.aspx",
            "NZ.HazardArea", 
            bbox=bbox_inmueble, crs="CRS:84",
            info_format="text/plain",
            **point_query_params
        )
        results["desertificacion_potencial"] = await fetch_any(client, [url_des_pot])
        results["desertificacion_laminar"] = await fetch_any(client, [url_des_lam])

    return results


# =========================
# Endpoints
# =========================

@router.get("/api/risk_clean")
async def api_risk_clean(
    lat: float = Query(..., description="Latitud WGS84"),
    lon: float = Query(..., description="Longitud WGS84"),
):
    try:
        raw = await fetch_all_risks(lat, lon)
        
        # 💡 CALCULO E INCLUSIÓN DEL NIVEL DE RIESGO PARA HOMOGENEIZACIÓN
        risk_level = calculate_risk_level(raw) 

        inf = raw.get("inundacion_fluvial", {})
        im = raw.get("inundacion_marina", {})

        out = {
            "lat": lat,
            "lon": lon,
            "risk_analysis": {
                "final_risk_level": risk_level, # <-- Valor 1, 2 o 3 para K_CM
                "note": "Riesgo calculado por Score Ponderado (Inundación 40%, DPMT 30%, Incendios 20%, Sismico 10%)."
            },
            "sin_geometria": {
                "dominio_publico_maritimo_terrestre": remove_geometry_from_geojson(raw.get("dominio_publico_maritimo_terrestre", {})),
                "incendios": remove_geometry_from_geojson(raw.get("incendios", {})),
                "inundacion_fluvial": {k: remove_geometry_from_geojson(v) for k, v in inf.items()},
                "inundacion_marina": {k: remove_geometry_from_geojson(v) for k, v in im.items()},
                "sismico": remove_geometry_from_geojson(raw.get("sismico", {})),
                
                # Desertificación se deja en crudo ya que es de menor impacto directo
                "desertificacion_potencial": raw.get("desertificacion_potencial", {}),
                "desertificacion_laminar": raw.get("desertificacion_laminar", {}),
            }
        }

        return out
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
