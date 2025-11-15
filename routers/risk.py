from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
import math
import httpx
from typing import Dict, Any, List, Optional

router = APIRouter()

# =========================
# Utilidades comunes
# =========================

def to_webmercator(lat: float, lon: float):
    """Convierte lat/lon (grados WGS84) a Web Mercator (EPSG:3857)."""
    R = 6378137.0
    x = lon * (math.pi / 180.0) * R
    y = math.log(math.tan((math.pi / 4.0) + (lat * math.pi / 360.0))) * R
    return x, y


def build_gfi_url(
    wms_url: str,
    layer: str,
    bbox: str,
    crs: str,
    width: int = 256,
    height: int = 256,
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
        f"&I={width//2}&J={height//2}"
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
                feats.append({k: v for k, v in f.items() if k != "geometry"})
        return {"type": "FeatureCollection", "features": feats}
    if obj.get("type") == "Feature":
        return {k: v for k, v in obj.items() if k != "geometry"}
    return obj


# =========================
# Core fetch
# =========================

async def fetch_all_risks(lat: float, lon: float) -> Dict[str, Any]:
    results: Dict[str, Any] = {}
    async with httpx.AsyncClient() as client:
        d_deg = 0.20
        bbox_c84_small = f"{lon - d_deg},{lat - d_deg},{lon + d_deg},{lat + d_deg}"

        # Incendios
        url_inc = build_gfi_url(
            "https://wms.mapama.gob.es/sig/Biodiversidad/Incendios/2006_2015",
            "NZ.HazardArea", bbox=bbox_c84_small, crs="CRS:84",
            info_format="application/json", styles="Biodiversidad_Incendios"
        )
        results["incendios"] = await fetch_any(client, [url_inc])

        # Inundaciones fluviales
        results["inundacion_fluvial"] = {}
        for periodo in ["T10", "T100", "T500"]:
            url = build_gfi_url(
                "https://servicios.idee.es/wms-inspire/riesgos-naturales/inundaciones",
                f"NZ.Flood.Fluvial{periodo}", bbox=bbox_c84_small, crs="CRS:84",
                info_format="application/json"
            )
            results["inundacion_fluvial"][periodo] = await fetch_any(client, [url])

        # Inundaciones marinas
        results["inundacion_marina"] = {}
        for periodo in ["T100", "T500"]:
            url = build_gfi_url(
                "https://servicios.idee.es/wms-inspire/riesgos-naturales/inundaciones",
                f"NZ.Flood.Marina{periodo}", bbox=bbox_c84_small, crs="CRS:84",
                info_format="application/json"
            )
            results["inundacion_marina"][periodo] = await fetch_any(client, [url])

        # Riesgo sísmico
        url_sismico = build_gfi_url(
            "https://www.ign.es/wms-inspire/geofisica",
            "HazardArea2002.NCSE-02", bbox=bbox_c84_small, crs="CRS:84",
            info_format="application/json"
        )
        results["sismico"] = await fetch_any(client, [url_sismico])

        # Desertificación (potencial + laminar)
        url_des_pot = build_gfi_url(
            "https://wms.mapama.gob.es/sig/Biodiversidad/INESErosionPotencial/wms.aspx",
            "NZ.HazardArea", bbox=bbox_c84_small, crs="CRS:84",
            info_format="text/plain"
        )
        url_des_lam = build_gfi_url(
            "https://wms.mapama.gob.es/sig/Biodiversidad/INESErosionLaminarRaster/wms.aspx",
            "NZ.HazardArea", bbox=bbox_c84_small, crs="CRS:84",
            info_format="text/plain"
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
        inf = raw.get("inundacion_fluvial", {})
        im = raw.get("inundacion_marina", {})

        out = {
            "lat": lat,
            "lon": lon,
            "sin_geometria": {
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
