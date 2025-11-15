from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
import math
import httpx
from typing import Dict, Any, List, Optional
import re

router = APIRouter()

# =========================
# Utilidades comunes
# =========================

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
    """Genera una URL GetFeatureInfo estándar WMS 1.3.0."""
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
# Parsers
# =========================

def parse_incendios_summary(obj: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(obj, dict) or obj.get("error"):
        return {"resumen": "desconocido", "fuente": "MITECO", "raw": obj}

    fc = remove_geometry_from_geojson(obj)
    feats = fc.get("features", [])
    if not feats:
        return {"resumen": "sin_datos", "fuente": "MITECO"}

    props = feats[0].get("properties", feats[0])

    freq = (
        props.get("frecuencia")
        or props.get("N_INCENDIOS")
        or props.get("Nº Incendios")
        or props.get("Frecuencia Incendios Forestales")
    )

    nivel = None
    try:
        if freq is not None:
            f = float(freq)
            if f == 0:
                nivel = "ninguno"
            elif f < 5:
                nivel = "bajo"
            elif f < 20:
                nivel = "medio"
            else:
                nivel = "alto"
    except:
        pass

    return {
        "fuente": "MITECO",
        "riesgo_incendios": nivel or "desconocido",
        "frecuencia_aprox": freq,
        "props": props,
    }


NODATA = -3.4028234663852886e+38

def inundable_from_gray(fc: Dict[str, Any]) -> str:
    """Devuelve: inundable | no_inundable | nodata."""
    try:
        feats = fc.get("features", [])
        if not feats:
            return "nodata"
        gray = feats[0].get("properties", {}).get("GRAY_INDEX")
        if gray is None:
            return "nodata"
        g = float(gray)
        if g == 0:
            return "no_inundable"
        if abs(g - NODATA) < 1e-6 or g == -9999:
            return "nodata"
        return "inundable"
    except:
        return "nodata"


def parse_sismico_summary(obj: Dict[str, Any]) -> Dict[str, Any]:
    try:
        fc = remove_geometry_from_geojson(obj)
        feats = fc.get("features", [])
        if not feats:
            return {"riesgo_sismico": "sin_riesgo"}
        props = feats[0].get("properties", {})

        pga = None
        for key in ("PGA", "pga", "aceleracion", "amax"):
            if key in props:
                try:
                    pga = float(props[key])
                    break
                except:
                    pass

        if pga is None:
            return {"riesgo_sismico": "sin_riesgo"}

        if pga < 0.04:
            nivel = "bajo"
        elif pga < 0.08:
            nivel = "medio"
        else:
            nivel = "alto"

        return {"pga": pga, "riesgo_sismico": nivel}

    except:
        return {"riesgo_sismico": "sin_riesgo"}


def parse_desertificacion_summary(obj: Dict[str, Any], tipo: str) -> Dict[str, Any]:
    raw = obj.get("raw")
    if raw:
        match = re.search(r"(-?\d+(\.\d+)?)", raw)
        if match:
            valor = float(match.group(1))
            if valor <= 0:
                nivel = "nodata"
            elif valor < 50:
                nivel = "bajo"
            elif valor < 100:
                nivel = "medio"
            else:
                nivel = "alto"
            return {"tipo": tipo, "valor": valor, "nivel": nivel}
    return {"tipo": tipo, "nivel": "nodata", "raw": obj}

# =========================
# Core fetch
# =========================

async def fetch_all_risks(lat: float, lon: float) -> Dict[str, Any]:
    results: Dict[str, Any] = {}

    async with httpx.AsyncClient() as client:
        d_deg = 0.20
        bbox_c84 = f"{lon - d_deg},{lat - d_deg},{lon + d_deg},{lat + d_deg}"

        # INCENDIOS
        url_inc = build_gfi_url(
            "https://wms.mapama.gob.es/sig/Biodiversidad/Incendios/2006_2015",
            "NZ.HazardArea",
            bbox=bbox_c84,
            crs="CRS:84",
            info_format="application/json",
            styles="Biodiversidad_Incendios",
        )
        results["incendios"] = await fetch_any(client, [url_inc])

        # INUNDACIONES FLUVIALES
        results["inundacion_fluvial"] = {}
        for periodo in ["T10", "T100", "T500"]:
            url = build_gfi_url(
                "https://servicios.idee.es/wms-inspire/riesgos-naturales/inundaciones",
                f"NZ.Flood.Fluvial{periodo}",
                bbox=bbox_c84,
                crs="CRS:84",
                info_format="application/json",
            )
            results["inundacion_fluvial"][periodo] = await fetch_any(client, [url])

        # INUNDACIONES MARINAS
        results["inundacion_marina"] = {}
        for periodo in ["T100", "T500"]:
            url = build_gfi_url(
                "https://servicios.idee.es/wms-inspire/riesgos-naturales/inundaciones",
                f"NZ.Flood.Marina{periodo}",
                bbox=bbox_c84,
                crs="CRS:84",
                info_format="application/json",
            )
            results["inundacion_marina"][periodo] = await fetch_any(client, [url])

        # SISMO
        url_sismico = build_gfi_url(
            "https://www.ign.es/wms-inspire/geofisica",
            "HazardArea2002.NCSE-02",
            bbox=bbox_c84,
            crs="CRS:84",
            info_format="application/json",
        )
        results["sismico"] = await fetch_any(client, [url_sismico])

        # DESERTIFICACIÓN
        url_des_pot = build_gfi_url(
            "https://wms.mapama.gob.es/sig/Biodiversidad/INESErosionPotencial/wms.aspx",
            "NZ.HazardArea",
            bbox=bbox_c84,
            crs="CRS:84",
            info_format="text/plain",
        )
        url_des_lam = build_gfi_url(
            "https://wms.mapama.gob.es/sig/Biodiversidad/INESErosionLaminarRaster/wms.aspx",
            "NZ.HazardArea",
            bbox=bbox_c84,
            crs="CRS:84",
            info_format="text/plain",
        )
        results["desertificacion_potencial"] = await fetch_any(client, [url_des_pot])
        results["desertificacion_laminar"] = await fetch_any(client, [url_des_lam])

        # DPMT — Deslinde Costero
        url_dpmt = build_gfi_url(
            "https://wms.mapama.gob.es/sig/Costas/DPMT",
            "AM.CoastalZoneManagementArea",
            bbox=bbox_c84,
            crs="CRS:84",  # ← CRÍTICO: NO USAR EPSG:4326
            info_format="application/json",
        )
        results["dpmt_deslinde"] = await fetch_any(client, [url_dpmt])

    return results

# =========================
# Endpoint
# =========================

@router.get("/api/risk_clean")
async def api_risk_clean(
    lat: float = Query(...),
    lon: float = Query(...),
):
    try:
        raw = await fetch_all_risks(lat, lon)

        out = {
            "lat": lat,
            "lon": lon,
            "resumen": {},
        }

        # RESÚMENES
        out["resumen"]["incendios"] = parse_incendios_summary(raw["incendios"])

        out["resumen"]["inundacion_fluvial"] = {
            k: inundable_from_gray(v) for k, v in raw["inundacion_fluvial"].items()
        }

        out["resumen"]["inundacion_marina"] = {
            k: inundable_from_gray(v) for k, v in raw["inundacion_marina"].items()
        }

        out["resumen"]["sismico"] = parse_sismico_summary(raw["sismico"])

        out["resumen"]["desertificacion"] = {
            "potencial": parse_desertificacion_summary(raw["desertificacion_potencial"], "potencial"),
            "laminar": parse_desertificacion_summary(raw["desertificacion_laminar"], "laminar"),
        }

        # DPMT — si hay features, estás dentro del DPMT
        dpmt_fc = raw["dpmt_deslinde"]
        feats = dpmt_fc.get("features", [])
        out["resumen"]["dpmt_deslinde"] = {
            "dentro_dpmt": len(feats) > 0,
            "info": feats[0].get("properties") if feats else None,
            "raw": dpmt_fc,
        }

        # RAW sin geometría
        out["sin_geometria"] = {
            "incendios": remove_geometry_from_geojson(raw["incendios"]),
            "inundacion_fluvial": {
                k: remove_geometry_from_geojson(v) for k, v in raw["inundacion_fluvial"].items()
            },
            "inundacion_marina": {
                k: remove_geometry_from_geojson(v) for k, v in raw["inundacion_marina"].items()
            },
            "sismico": remove_geometry_from_geojson(raw["sismico"]),
            "desertificacion_potencial": raw["desertificacion_potencial"],
            "desertificacion_laminar": raw["desertificacion_laminar"],
            "dpmt_deslinde": dpmt_fc,
        }

        return out

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
