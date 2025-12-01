from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
import math
import httpx
from typing import Dict, Any, List, Optional

router = APIRouter()

# --- CONFIGURACIÓN DE PONDERACIÓN ---
RISK_WEIGHTS = {
    "R1_inundacion": 0.40,
    "R2_dpmt_costas": 0.30,
    "R3_incendios": 0.20,
    "R4_sismico": 0.10
}

FLOOD_SCORE_MAP = {
    "T10": 10,
    "T100": 7,
    "T500": 5
}

# =========================
# Utilidades
# =========================

def to_webmercator(lat: float, lon: float):
    R = 6378137.0
    x = lon * (math.pi / 180.0) * R
    y = math.log(math.tan((math.pi / 4.0) + (lat * math.pi / 360.0))) * R
    return x, y


def build_gfi_url(
    wms_url: str, layer: str, bbox: str, crs: str,
    width: int = 1, height: int = 1,
    info_format: str = "application/json",
    styles: Optional[str] = None,
    feature_count: int = 10,
    vendor_params: Optional[Dict[str, Any]] = None,
) -> str:
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
            r = await client.get(u, follow_redirects=True, timeout=15.0)
            r.raise_for_status()
            try:
                return r.json()
            except Exception:
                return {"raw": r.text}
        except Exception as e:
            last_err = str(e)
    return {"error": last_err or "unknown error"}


def remove_geometry_from_geojson(obj: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(obj, dict): return obj
    if obj.get("type") == "FeatureCollection":
        feats = []
        for f in obj.get("features", []):
            if isinstance(f, dict):
                feats.append({k: v for k, v in f.items() if k != "geometry"})
        return {"type": "FeatureCollection", "features": feats}
    if obj.get("type") == "Feature":
        return {k: v for k, v in obj.items() if k != "geometry"}
    return obj


# =========================
# LÓGICA DE CÁLCULO
# =========================

def calculate_risk_level(raw_risks: Dict[str, Any]) -> int:
    composite_score = 0.0

    # --- A. INUNDACIÓN (R1) ---
    max_flood_score = 0
    flood_data = {**raw_risks.get("inundacion_fluvial", {}), **raw_risks.get("inundacion_marina", {})}
    
    for periodo, data in flood_data.items():
        if isinstance(data, dict) and not data.get("error") and data.get("features"):
            properties = data["features"][0].get("properties", {})
            gray_val = properties.get("GRAY_INDEX", 0)
            
            # Solo puntuamos si el valor no es el código "NoData"
            if gray_val > -1000:
                score = FLOOD_SCORE_MAP.get(periodo, 0)
                max_flood_score = max(max_flood_score, score)

    composite_score += max_flood_score * RISK_WEIGHTS["R1_inundacion"]


    # --- B. DPMT (R2) ---
    dpmt_data = raw_risks.get("dominio_publico_maritimo_terrestre", {})
    score_dpmt = 0
    # Asume riesgo 0 si hay error 500 o no hay features
    if isinstance(dpmt_data, dict) and not dpmt_data.get("error") and dpmt_data.get("features"):
        score_dpmt = 10
    
    composite_score += score_dpmt * RISK_WEIGHTS["R2_dpmt_costas"]


    # --- C. INCENDIOS (R3) ---
    incendios_data = raw_risks.get("incendios", {})
    score_incendio = 0
    if isinstance(incendios_data, dict) and not incendios_data.get("error") and incendios_data.get("features"):
        score_incendio = 8
        
    composite_score += score_incendio * RISK_WEIGHTS["R3_incendios"]


    # --- D. SISMICO (R4) ---
    sismico_data = raw_risks.get("sismico", {})
    score_sismico = 0
    if isinstance(sismico_data, dict) and not sismico_data.get("error") and sismico_data.get("features"):
        score_sismico = 5
        
    composite_score += score_sismico * RISK_WEIGHTS["R4_sismico"]

    
    # --- RESULTADO FINAL ---
    if composite_score <= 3.0:
        return 1
    elif composite_score <= 6.0:
        return 2
    else: 
        return 3


# =========================
# CORE FETCH (CON URLS DIRECTAS)
# =========================

async def fetch_all_risks(lat: float, lon: float) -> Dict[str, Any]:
    results: Dict[str, Any] = {}
    async with httpx.AsyncClient() as client:
        
        d_deg = 0.00006
        bbox = f"{lon - d_deg},{lat - d_deg},{lon + d_deg},{lat + d_deg}"
        pq = {"width": 1, "height": 1}

        # 1. DPMT (URL y capa DIRECTAS, con styles corregido)
        url_dpmt = build_gfi_url(
            "https://wms.mapama.gob.es/sig/Costas/DPMT", 
            "AM.CoastalZoneManagementArea", 
            bbox=bbox, crs="CRS:84",
            info_format="application/json",
            styles="costas_dpmt",
            **pq
        )
        results["dominio_publico_maritimo_terrestre"] = await fetch_any(client, [url_dpmt])

        # 2. INCENDIOS
        url_inc = build_gfi_url(
            "https://wms.mapama.gob.es/sig/Biodiversidad/Incendios/2006_2015",
            "NZ.HazardArea", 
            bbox=bbox, crs="CRS:84",
            info_format="application/json", styles="Biodiversidad_Incendios",
            **pq
        )
        results["incendios"] = await fetch_any(client, [url_inc])

        # 3. INUNDACIONES FLUVIALES
        results["inundacion_fluvial"] = {}
        for p in ["T10", "T100", "T500"]:
            url = build_gfi_url(
                "https://servicios.idee.es/wms-inspire/riesgos-naturales/inundaciones",
                f"NZ.Flood.Fluvial{p}", 
                bbox=bbox, crs="CRS:84", info_format="application/json", **pq
            )
            results["inundacion_fluvial"][p] = await fetch_any(client, [url])
        
        # 4. INUNDACIONES MARINAS
        results["inundacion_marina"] = {}
        for p in ["T100", "T500"]:
            url = build_gfi_url(
                "https://servicios.idee.es/wms-inspire/riesgos-naturales/inundaciones",
                f"NZ.Flood.Marina{p}", 
                bbox=bbox, crs="CRS:84", info_format="application/json", **pq
            )
            results["inundacion_marina"][p] = await fetch_any(client, [url])

        # 5. SISMICO
        url_sismico = build_gfi_url(
            "https://www.ign.es/wms-inspire/geofisica",
            "HazardArea2002.NCSE-02", 
            bbox=bbox, crs="CRS:84", info_format="application/json", **pq
        )
        results["sismico"] = await fetch_any(client, [url_sismico])

        # 6. DESERTIFICACION
        url_des_pot = build_gfi_url(
            "https://wms.mapama.gob.es/sig/Biodiversidad/INESErosionPotencial/wms.aspx",
            "NZ.HazardArea", bbox=bbox, crs="CRS:84", info_format="text/plain", **pq
        )
        url_des_lam = build_gfi_url(
            "https://wms.mapama.gob.es/sig/Biodiversidad/INESErosionLaminarRaster/wms.aspx",
            "NZ.HazardArea", bbox=bbox, crs="CRS:84", info_format="text/plain", **pq
        )
        results["desertificacion_potencial"] = await fetch_any(client, [url_des_pot])
        results["desertificacion_laminar"] = await fetch_any(client, [url_des_lam])

    return results


# =========================
# ENDPOINT
# =========================

@router.get("/api/risk_clean")
async def api_risk_clean(lat: float = Query(...), lon: float = Query(...)):
    try:
        raw = await fetch_all_risks(lat, lon)
        risk_level = calculate_risk_level(raw)

        inf = raw.get("inundacion_fluvial", {})
        im = raw.get("inundacion_marina", {})

        out = {
            "lat": lat, "lon": lon,
            "risk_analysis": {
                "final_risk_level": risk_level,
                "note": "Riesgo calculado (Inundación 40%, DPMT 30%, Incendios 20%, Sismico 10%)."
            },
            "sin_geometria": {
                "dominio_publico_maritimo_terrestre": remove_geometry_from_geojson(raw.get("dominio_publico_maritimo_terrestre", {})),
                "incendios": remove_geometry_from_geojson(raw.get("incendios", {})),
                "inundacion_fluvial": {k: remove_geometry_from_geojson(v) for k, v in inf.items()},
                "inundacion_marina": {k: remove_geometry_from_geojson(v) for k, v in im.items()},
                "sismico": remove_geometry_from_geojson(raw.get("sismico", {})),
                "desertificacion_potencial": raw.get("desertificacion_potencial", {}),
                "desertificacion_laminar": raw.get("desertificacion_laminar", {}),
            }
        }
        return out
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
