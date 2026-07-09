#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CAZADOR DE EMPLEOS · Data Analyst / Data Scientist (junior y middle)
==================================================================
Sistema personal de búsqueda de ofertas remotas, legítimas y verificadas.

Qué hace en cada ejecución:
  1. Consulta APIs PÚBLICAS y OFICIALES de bolsas de empleo reconocidas
     (nada de scraping agresivo: solo endpoints que ellas mismas publican).
  2. Filtra por rol (analista / científico de datos), nivel (junior y middle),
     modalidad (100% remoto) y compatibilidad con Colombia / LATAM.
  3. Aplica un ÍNDICE DE CONFIANZA anti-fraude: descarta ofertas con
     señales de estafa (pagos por adelantado, contactos sospechosos, etc.).
  4. Almacena todo en data/ofertas.json (deduplicado, con historial).
  5. Genera un panel web en docs/index.html para consultar cuando quieras.

Uso:
  python job_hunter.py            # ejecución normal
  python job_hunter.py --demo     # prueba el sistema con datos de ejemplo
  python job_hunter.py --forzar   # ignora los límites de frecuencia por fuente

Automatización cada 4 horas: ver .github/workflows/actualizar_ofertas.yml
"""

import argparse
import hashlib
import html
import json
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# CONFIGURACIÓN
# ---------------------------------------------------------------------------

RAIZ = Path(__file__).resolve().parent
DIR_DATOS = RAIZ / "data"
DIR_DOCS = RAIZ / "docs"
ARCHIVO_OFERTAS = DIR_DATOS / "ofertas.json"
ARCHIVO_ESTADO = DIR_DATOS / "estado_fuentes.json"
ARCHIVO_HISTORIAL = DIR_DATOS / "historial.json"
ARCHIVO_PANEL = DIR_DOCS / "index.html"

USER_AGENT = "CazadorEmpleosPersonal/1.0 (agregador personal de ofertas; uso no comercial)"
TIMEOUT = 25

# Una oferta se archiva si lleva más de este número de horas sin aparecer
# en su fuente (probablemente fue cerrada o cubierta).
HORAS_PARA_EXPIRAR = 72
# Una oferta se marca "NUEVA" durante sus primeras 24 h en el sistema.
HORAS_COMO_NUEVA = 24
# Solo se aceptan y se muestran ofertas publicadas en los últimos N días.
# Si la fuente no informa fecha de publicación, se usa la fecha de detección.
DIAS_MAX_ANTIGUEDAD = 7

# Frecuencia mínima entre consultas a cada fuente (en horas).
# Remotive pide explícitamente máximo ~4 consultas al día -> cada 6 h.
FUENTES = {
    "getonbrd": {
        "nombre": "Get on Board",
        "activa": True,
        "min_horas": 3.5,
        "nota": "Bolsa líder en LATAM. Modera manualmente cada oferta y verifica empresas.",
    },
    "remotive": {
        "nombre": "Remotive",
        "activa": True,
        "min_horas": 6.0,  # respeta su límite de ~4 consultas/día
        "nota": "Bolsa remota curada. Su API pública exige enlazar de vuelta a la oferta original.",
    },
    "remoteok": {
        "nombre": "Remote OK",
        "activa": True,
        "min_horas": 3.5,
        "nota": "Agregador remoto global. Exige atribución con enlace a la oferta original.",
    },
    "jobicy": {
        "nombre": "Jobicy",
        "activa": True,
        "min_horas": 3.5,
        "nota": "Bolsa remota con API pública gratuita.",
    },
    "arbeitnow": {
        "nombre": "Arbeitnow",
        "activa": False,  # mayormente Europa; actívala si te interesa ese mercado
        "min_horas": 3.5,
        "nota": "Bolsa europea con API abierta. Desactivada por defecto (huso horario).",
    },
}

# --- Filtro de rol -----------------------------------------------------------

PALABRAS_ROL = [
    "data analyst", "analista de datos", "analista datos",
    "data scientist", "cientifico de datos", "científico de datos",
    "data science", "analytics", "analitica", "analítica",
    "business intelligence", "bi analyst", "analista bi", "inteligencia de negocios",
    "machine learning", "analytics engineer",
]

# Niveles: se aceptan junior y middle; se excluye senior/lead y prácticas.
# (PATRON_JUNIOR ya no excluye: solo sirve para etiquetar el nivel en el panel.)
PATRON_SEMI_SENIOR = re.compile(r"\b(semi[\s\-]?senior|semi[\s\-]?sr|ssr|mid[\s\-]?level|middle|mid|intermedio|intermediate)\b", re.I)
PATRON_SENIOR = re.compile(r"\b(sr\.?|senior|lead|líder|lider|principal|staff|head|director[a]?|vp|chief|arquitect[oa]|architect|manager|gerente|jefe|jefa|coordinador[a]?)\b", re.I)
PATRON_JUNIOR = re.compile(r"\b(jr\.?|junior|entry[\s\-]?level|graduate)\b", re.I)
PATRON_PRACTICAS = re.compile(r"\b(intern(ship)?|pasant[ea]|practicante|pr[aá]cticas|trainee|becari[oa]|aprendiz)\b", re.I)

# --- Compatibilidad geográfica (trabajo remoto desde Colombia) ---------------

UBICACIONES_COMPATIBLES = [
    "worldwide", "anywhere", "global", "remote", "international",
    "latam", "latin america", "america latina", "américa latina",
    "south america", "sudamerica", "sudamérica", "americas", "america",
    "colombia", "hispanoamerica", "spanish",
]
UBICACIONES_INCOMPATIBLES = [
    "usa only", "us only", "united states only", "us-only", "usa-only",
    "us citizens", "us citizen", "us resident", "green card", "w2", "w-2",
    "europe only", "eu only", "emea only", "uk only", "canada only",
    "must be located in the us", "must reside in the us", "authorized to work in the us",
    "germany", "poland only", "eu residents", "european union only",
    "australia only", "apac only", "india only",
]

# --- Detección de inglés hablado ---------------------------------------------
# El usuario acepta inglés SOLO ESCRITO. Estas señales sugieren que la vacante
# exige inglés hablado (llamadas, entrevistas, presentaciones) -> se marca
# con advertencia, no se descarta (a veces es negociable).

PATRONES_INGLES_HABLADO = [
    r"fluent\s+(spoken\s+)?english", r"spoken\s+english", r"verbal\s+.{0,20}english",
    r"english\s+.{0,20}(verbal|spoken|conversational)", r"conversational\s+english",
    r"advanced\s+english", r"ingl[eé]s\s+(avanzado|fluido|conversacional|hablado)",
    r"english\s+(is\s+)?(a\s+)?must", r"c1\b", r"c2\b", r"native\s+english",
    r"client[\s\-]facing", r"customer[\s\-]facing", r"daily\s+(stand[\s\-]?ups?|meetings)\s+in\s+english",
    r"presentations?\s+in\s+english", r"interviews?\s+.{0,25}in\s+english",
]

# --- Señales de fraude --------------------------------------------------------
# Cada coincidencia resta puntos al índice de confianza (base 100).
# < 60 puntos = la oferta se DESCARTA y queda registrada con sus motivos.

BANDERAS_ROJAS = [
    # (patrón, penalización, descripción)
    (r"(pago|dep[oó]sito|cuota|tarifa|fee)\s+(inicial|de\s+(inscripci[oó]n|registro|entrenamiento|capacitaci[oó]n|aplicaci[oó]n))", 60, "Pide pago por adelantado"),
    (r"(training|registration|application|processing|starter)\s+fee", 60, "Cobra tarifa (fee) al candidato"),
    (r"(compra(r)?|adquirir|invertir\s+en)\s+(tu\s+)?(equipo|kit|materiales|licencia)", 50, "Pide comprar equipo o materiales"),
    (r"western\s+union|moneygram|money\s+gram|gift\s*cards?|tarjetas?\s+de\s+regalo", 60, "Métodos de pago típicos de estafa"),
    (r"(env[ií]a|adjunta|manda)\s+(foto\s+de\s+)?(tu\s+)?(c[eé]dula|dni|pasaporte|documento\s+de\s+identidad|licencia)", 45, "Pide documentos de identidad antes de un proceso formal"),
    (r"(n[uú]mero|datos|informaci[oó]n)\s+(de\s+)?(tu\s+)?(cuenta\s+bancaria|tarjeta)", 55, "Pide datos bancarios en la oferta"),
    (r"gana\s+(hasta\s+)?\$?\d[\d.,]*\s*(usd|d[oó]lares|pesos)?\s*(al\s+d[ií]a|diarios|por\s+hora)\s*(sin\s+experiencia)?", 35, "Promesa de ganancias fáciles"),
    (r"sin\s+experiencia.{0,30}(altos?\s+ingresos|gana\s+dinero|grandes\s+ganancias)", 40, "Sin experiencia + dinero fácil"),
    (r"(solo|only)\s*(por|via|vía|through)?\s*whatsapp", 30, "Contacto únicamente por WhatsApp"),
    (r"t\.me/|telegram\s*[:@]", 30, "Reclutamiento por Telegram"),
    (r"bit\.ly|tinyurl|goo\.gl|cutt\.ly|rb\.gy", 20, "Usa enlaces acortados"),
    (r"(wallet|billetera)\s+(cripto|crypto|bitcoin|usdt)", 35, "Pagos vía billetera cripto"),
    (r"mystery\s+shopper|reempaque|repack(ing|age)|reship", 55, "Modalidad típica de fraude (reenvíos/mystery shopper)"),
    (r"@(gmail|hotmail|yahoo|outlook|aol)\.com", 15, "Contacto en correo personal, no corporativo"),
    (r"urgente.{0,30}(hoy\s+mismo|inmediat[oa])\s+.{0,20}(dep[oó]sito|pago)", 40, "Urgencia + dinero: patrón de presión"),
]

BONO_FUENTE = {
    # Get on Board modera manualmente y verifica empresas -> bono de confianza.
    "getonbrd": 5,
    "remotive": 3,  # curaduría editorial
    "remoteok": 0,
    "jobicy": 0,
    "arbeitnow": 0,
}

# ---------------------------------------------------------------------------
# UTILIDADES
# ---------------------------------------------------------------------------

def ahora_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(texto: str) -> datetime:
    return datetime.strptime(texto, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def normalizar(texto: str) -> str:
    """minúsculas + sin tildes, para comparaciones robustas."""
    texto = (texto or "").lower()
    texto = unicodedata.normalize("NFD", texto)
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def limpiar_html(texto: str) -> str:
    texto = re.sub(r"<[^>]+>", " ", texto or "")
    texto = html.unescape(texto)
    return re.sub(r"\s+", " ", texto).strip()


def id_oferta(fuente: str, empresa: str, titulo: str, url: str) -> str:
    base = f"{fuente}|{normalizar(empresa)}|{normalizar(titulo)}|{url}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def cargar_json(ruta: Path, defecto):
    if ruta.exists():
        try:
            return json.loads(ruta.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            print(f"  [aviso] {ruta.name} ilegible; se reinicia.")
    return defecto


def guardar_json(ruta: Path, datos):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")


def peticion(url: str, params: dict | None = None):
    resp = requests.get(
        url,
        params=params,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()

# ---------------------------------------------------------------------------
# CONECTORES A FUENTES (solo APIs públicas oficiales)
# ---------------------------------------------------------------------------

def fuente_getonbrd() -> list[dict]:
    """API pública de Get on Board: categoría Data Science / Analytics."""
    crudas = []
    for pagina in (1, 2):
        datos = peticion(
            "https://www.getonbrd.com/api/v0/categories/data-science-analytics/jobs",
            params={"per_page": 100, "page": pagina},
        )
        lote = datos.get("data", []) if isinstance(datos, dict) else []
        crudas.extend(lote)
        if len(lote) < 100:
            break

    ofertas = []
    for item in crudas:
        attrs = item.get("attributes", {}) or {}
        modalidad = normalizar(str(attrs.get("remote_modality") or ""))
        es_remoto = bool(attrs.get("remote")) or "remote" in modalidad
        if not es_remoto:
            continue
        publicada = attrs.get("published_at")
        fecha_pub = (
            iso(datetime.fromtimestamp(publicada, tz=timezone.utc))
            if isinstance(publicada, (int, float)) else None
        )
        salario = None
        if attrs.get("min_salary") or attrs.get("max_salary"):
            salario = f"USD {attrs.get('min_salary') or '?'} – {attrs.get('max_salary') or '?'} /mes"
        seniority = ""
        sen = attrs.get("seniority")
        if isinstance(sen, dict):
            seniority = str(sen.get("data", {}).get("id", "") if isinstance(sen.get("data"), dict) else sen.get("id", ""))
        descripcion = " ".join(
            limpiar_html(str(attrs.get(campo) or ""))
            for campo in ("description", "functions", "desirable", "benefits", "perks")
        )
        empresa = ""
        comp = attrs.get("company")
        if isinstance(comp, dict):
            cdata = comp.get("data") if isinstance(comp.get("data"), dict) else comp
            empresa = str((cdata or {}).get("attributes", {}).get("name") or (cdata or {}).get("id") or "")
        slug = item.get("id", "")
        ofertas.append({
            "fuente": "getonbrd",
            "titulo": str(attrs.get("title") or ""),
            "empresa": empresa or "(ver en Get on Board)",
            "url": f"https://www.getonbrd.com/jobs/{slug}" if slug else "",
            "ubicacion": "Remoto (LATAM)" if "local" in modalidad else "Remoto",
            "salario": salario,
            "fecha_publicada": fecha_pub,
            "descripcion": descripcion,
            "pista_seniority": {"2": "junior", "3": "mid", "4": "senior", "5": "senior"}.get(seniority, ""),
        })
    return ofertas


def fuente_remotive() -> list[dict]:
    """API pública de Remotive (categoría Data). Sus términos exigen enlazar
    a la oferta original y citar a Remotive como fuente: el panel lo cumple."""
    datos = peticion("https://remotive.com/api/remote-jobs", params={"category": "data"})
    ofertas = []
    for j in datos.get("jobs", []):
        fecha = j.get("publication_date")
        if fecha and "T" in str(fecha):
            fecha = str(fecha).split(".")[0].rstrip("Z") + "Z"
            fecha = fecha if fecha.endswith("Z") else fecha + "Z"
        ofertas.append({
            "fuente": "remotive",
            "titulo": str(j.get("title") or ""),
            "empresa": str(j.get("company_name") or ""),
            "url": str(j.get("url") or ""),
            "ubicacion": str(j.get("candidate_required_location") or "No especificada"),
            "salario": str(j.get("salary") or "") or None,
            "fecha_publicada": fecha,
            "descripcion": limpiar_html(j.get("description") or ""),
            "pista_seniority": "",
        })
    return ofertas


def fuente_remoteok() -> list[dict]:
    """API pública de Remote OK. Exige atribución y enlace a la oferta."""
    datos = peticion("https://remoteok.com/api")
    ofertas = []
    for j in datos if isinstance(datos, list) else []:
        if not isinstance(j, dict) or not j.get("position"):
            continue  # el primer elemento es un aviso legal, se omite
        etiquetas = " ".join(str(t) for t in (j.get("tags") or []))
        fecha = str(j.get("date") or "")
        if fecha:
            fecha = fecha.split("+")[0].rstrip("Z") + "Z"
        ofertas.append({
            "fuente": "remoteok",
            "titulo": str(j.get("position") or ""),
            "empresa": str(j.get("company") or ""),
            "url": str(j.get("url") or ""),
            "ubicacion": str(j.get("location") or "Remoto"),
            "salario": (f"USD {j['salary_min']:,} – {j['salary_max']:,} /año"
                        if j.get("salary_min") and j.get("salary_max") else None),
            "fecha_publicada": fecha or None,
            "descripcion": limpiar_html(j.get("description") or "") + " " + etiquetas,
            "pista_seniority": "",
        })
    return ofertas


def fuente_jobicy() -> list[dict]:
    """API pública v2 de Jobicy, industria data-science."""
    datos = peticion(
        "https://jobicy.com/api/v2/remote-jobs",
        params={"count": 50, "industry": "data-science"},
    )
    ofertas = []
    for j in datos.get("jobs", []):
        fecha = str(j.get("pubDate") or "").replace(" ", "T")
        ofertas.append({
            "fuente": "jobicy",
            "titulo": str(j.get("jobTitle") or ""),
            "empresa": str(j.get("companyName") or ""),
            "url": str(j.get("url") or ""),
            "ubicacion": str(j.get("jobGeo") or "Remoto"),
            "salario": (f"{j.get('salaryCurrency','USD')} {j['annualSalaryMin']:,} – {j['annualSalaryMax']:,} /año"
                        if j.get("annualSalaryMin") and j.get("annualSalaryMax") else None),
            "fecha_publicada": (fecha + "Z") if fecha and not fecha.endswith("Z") else (fecha or None),
            "descripcion": limpiar_html(str(j.get("jobExcerpt") or "") + " " + str(j.get("jobDescription") or "")),
            "pista_seniority": normalizar(str(j.get("jobLevel") or "")),
        })
    return ofertas


def fuente_arbeitnow() -> list[dict]:
    """API pública de Arbeitnow (principalmente Europa)."""
    datos = peticion("https://www.arbeitnow.com/api/job-board-api")
    ofertas = []
    for j in datos.get("data", []):
        if not j.get("remote"):
            continue
        fecha = j.get("created_at")
        fecha_pub = (iso(datetime.fromtimestamp(fecha, tz=timezone.utc))
                     if isinstance(fecha, (int, float)) else None)
        ofertas.append({
            "fuente": "arbeitnow",
            "titulo": str(j.get("title") or ""),
            "empresa": str(j.get("company_name") or ""),
            "url": str(j.get("url") or ""),
            "ubicacion": str(j.get("location") or "Remoto"),
            "salario": None,
            "fecha_publicada": fecha_pub,
            "descripcion": limpiar_html(j.get("description") or "") + " " + " ".join(j.get("tags") or []),
            "pista_seniority": "",
        })
    return ofertas


CONECTORES = {
    "getonbrd": fuente_getonbrd,
    "remotive": fuente_remotive,
    "remoteok": fuente_remoteok,
    "jobicy": fuente_jobicy,
    "arbeitnow": fuente_arbeitnow,
}

# ---------------------------------------------------------------------------
# FILTROS Y EVALUACIÓN
# ---------------------------------------------------------------------------

def es_reciente(fecha_publicada: str | None, detectada: str | None = None) -> bool:
    """True si la oferta tiene menos de DIAS_MAX_ANTIGUEDAD días.
    Prioriza la fecha de publicación; sin ella usa la de detección; sin
    ninguna fecha (o con formato ilegible) no se descarta."""
    referencia = fecha_publicada or detectada
    if not referencia:
        return True
    try:
        return ahora_utc() - parse_iso(referencia) <= timedelta(days=DIAS_MAX_ANTIGUEDAD)
    except ValueError:
        return True


def es_rol_objetivo(oferta: dict) -> bool:
    texto = normalizar(oferta["titulo"] + " " + oferta["descripcion"][:600])
    titulo = normalizar(oferta["titulo"])
    # El título manda; la descripción ayuda cuando el título es genérico.
    return any(p in titulo for p in map(normalizar, PALABRAS_ROL)) or (
        ("analyst" in titulo or "analista" in titulo or "scientist" in titulo or "cientifico" in titulo)
        and any(p in texto for p in map(normalizar, PALABRAS_ROL))
    )


def nivel_seniority(oferta: dict) -> str:
    """Devuelve: 'mid', 'junior', 'senior', 'practicas' o 'sin_especificar'."""
    titulo = oferta["titulo"]
    pista = oferta.get("pista_seniority", "")
    if PATRON_PRACTICAS.search(titulo) or pista == "internship":
        return "practicas"
    if PATRON_SEMI_SENIOR.search(titulo) or pista in ("mid", "middle", "semi senior"):
        return "mid"
    if PATRON_SENIOR.search(titulo) or pista == "senior":
        return "senior"
    if PATRON_JUNIOR.search(titulo) or pista in ("junior", "entry"):
        return "junior"
    inicio = normalizar(oferta["descripcion"][:400])
    if PATRON_SEMI_SENIOR.search(inicio):
        return "mid"
    return "sin_especificar"


def ubicacion_compatible(oferta: dict) -> tuple[bool, str | None]:
    """(compatible, advertencia). Excluye ofertas cerradas a otras regiones."""
    u = normalizar(oferta["ubicacion"])
    d = normalizar(oferta["descripcion"][:1500])
    for mala in UBICACIONES_INCOMPATIBLES:
        if normalizar(mala) in u or normalizar(mala) in d:
            return False, None
    if any(normalizar(b) in u for b in UBICACIONES_COMPATIBLES) or u in ("", "no especificada", "remoto"):
        return True, None
    # Ubicación restringida pero ambigua (p. ej. lista de países): incluir con aviso.
    return True, f"Confirma elegibilidad geográfica: la fuente indica «{oferta['ubicacion']}»"


def requiere_ingles_hablado(oferta: dict) -> bool:
    texto = (oferta["titulo"] + " " + oferta["descripcion"]).lower()
    return any(re.search(p, texto, re.I) for p in PATRONES_INGLES_HABLADO)


def detectar_idioma(oferta: dict) -> str:
    texto = normalizar(oferta["titulo"] + " " + oferta["descripcion"][:800])
    marcadores_es = [" que ", " para ", " con ", " los ", " las ", " del ", " una ", " este ", "experiencia", "empresa", "buscamos", "requisitos"]
    aciertos = sum(1 for m in marcadores_es if m in texto)
    return "es" if aciertos >= 3 else "en"


def evaluar_confianza(oferta: dict) -> tuple[int, list[str]]:
    """Índice 0–100. Empieza en 100 y resta por cada bandera roja detectada."""
    puntaje = 100 + BONO_FUENTE.get(oferta["fuente"], 0)
    motivos = []
    texto = oferta["titulo"] + " " + oferta["descripcion"]
    for patron, castigo, descripcion in BANDERAS_ROJAS:
        if re.search(patron, texto, re.I):
            puntaje -= castigo
            motivos.append(descripcion)
    if not oferta["empresa"].strip() or oferta["empresa"].startswith("("):
        if oferta["fuente"] != "getonbrd":  # GOB verifica empresa aunque el API no la expanda
            puntaje -= 25
            motivos.append("No identifica a la empresa")
    if not oferta["url"].startswith("https://"):
        puntaje -= 20
        motivos.append("Enlace sin HTTPS o ausente")
    return max(0, min(100, puntaje)), motivos

# ---------------------------------------------------------------------------
# PIPELINE PRINCIPAL
# ---------------------------------------------------------------------------

def procesar_crudas(crudas: list[dict]) -> tuple[list[dict], list[dict]]:
    """Aplica filtros y devuelve (aceptadas, descartadas_por_fraude)."""
    aceptadas, sospechosas = [], []
    for cruda in crudas:
        if not cruda.get("titulo") or not cruda.get("url"):
            continue
        if not es_reciente(cruda.get("fecha_publicada")):
            continue
        if not es_rol_objetivo(cruda):
            continue
        nivel = nivel_seniority(cruda)
        if nivel in ("senior", "practicas"):
            continue
        compatible, aviso_geo = ubicacion_compatible(cruda)
        if not compatible:
            continue

        confianza, banderas = evaluar_confianza(cruda)
        idioma = detectar_idioma(cruda)
        ingles_hablado = idioma == "en" and requiere_ingles_hablado(cruda)

        registro = {
            "id": id_oferta(cruda["fuente"], cruda["empresa"], cruda["titulo"], cruda["url"]),
            "fuente": cruda["fuente"],
            "fuente_nombre": FUENTES[cruda["fuente"]]["nombre"],
            "titulo": cruda["titulo"].strip(),
            "empresa": cruda["empresa"].strip(),
            "url": cruda["url"],
            "ubicacion": cruda["ubicacion"],
            "salario": cruda.get("salario"),
            "fecha_publicada": cruda.get("fecha_publicada"),
            "seniority": {"mid": "Middle / Semi Senior", "junior": "Junior"}.get(nivel, "No especificado"),
            "idioma": idioma,
            "aviso_ingles_hablado": ingles_hablado,
            "aviso_geografico": aviso_geo,
            "confianza": confianza,
            "banderas": banderas,
            "resumen": cruda["descripcion"][:320],
        }
        if confianza < 60:
            sospechosas.append(registro)
        else:
            aceptadas.append(registro)
    return aceptadas, sospechosas


def actualizar_almacen(nuevas: list[dict]) -> dict:
    """Fusiona lo recién encontrado con lo almacenado, sin duplicados."""
    ahora = ahora_utc()
    almacen = cargar_json(ARCHIVO_OFERTAS, {"ofertas": {}, "actualizado": None})
    ofertas = almacen.get("ofertas", {})

    ids_vistos = set()
    contador_nuevas = 0
    for o in nuevas:
        ids_vistos.add(o["id"])
        if o["id"] in ofertas:
            existente = ofertas[o["id"]]
            existente.update({k: o[k] for k in ("salario", "confianza", "banderas", "ubicacion", "resumen")})
            existente["ultima_vez_vista"] = iso(ahora)
        else:
            o["detectada"] = iso(ahora)
            o["ultima_vez_vista"] = iso(ahora)
            ofertas[o["id"]] = o
            contador_nuevas += 1

    # Archivar ofertas que ya no aparecen en su fuente.
    historial = cargar_json(ARCHIVO_HISTORIAL, {"archivadas": []})
    limite = ahora - timedelta(hours=HORAS_PARA_EXPIRAR)
    expiradas = [oid for oid, o in ofertas.items()
                 if parse_iso(o["ultima_vez_vista"]) < limite]
    for oid in expiradas:
        oferta = ofertas.pop(oid)
        oferta["archivada"] = iso(ahora)
        historial["archivadas"].append(oferta)
    historial["archivadas"] = historial["archivadas"][-500:]  # tope del historial

    almacen["ofertas"] = ofertas
    almacen["actualizado"] = iso(ahora)
    guardar_json(ARCHIVO_OFERTAS, almacen)
    guardar_json(ARCHIVO_HISTORIAL, historial)

    return {
        "total": len(ofertas),
        "nuevas_esta_corrida": contador_nuevas,
        "expiradas": len(expiradas),
    }


def ejecutar(forzar: bool = False, demo: bool = False) -> None:
    ahora = ahora_utc()
    print(f"— Cazador de empleos · {iso(ahora)} —")

    estado = cargar_json(ARCHIVO_ESTADO, {})
    crudas, sospechosas_totales = [], []

    if demo:
        crudas = datos_demo()
        print(f"  [demo] {len(crudas)} ofertas de ejemplo inyectadas.")
    else:
        for clave, cfg in FUENTES.items():
            if not cfg["activa"]:
                continue
            ultimo = estado.get(clave, {}).get("ultima_consulta")
            if ultimo and not forzar:
                transcurrido = (ahora - parse_iso(ultimo)).total_seconds() / 3600
                if transcurrido < cfg["min_horas"]:
                    print(f"  [{cfg['nombre']}] omitida: consultada hace {transcurrido:.1f} h "
                          f"(mínimo {cfg['min_horas']} h para respetar sus términos).")
                    continue
            try:
                lote = CONECTORES[clave]()
                crudas.extend(lote)
                estado[clave] = {"ultima_consulta": iso(ahora), "obtenidas": len(lote), "error": None}
                print(f"  [{cfg['nombre']}] {len(lote)} ofertas obtenidas.")
            except Exception as exc:  # una fuente caída no tumba el sistema
                estado[clave] = {"ultima_consulta": iso(ahora), "obtenidas": 0, "error": str(exc)[:200]}
                print(f"  [{cfg['nombre']}] ERROR: {exc}")

    aceptadas, sospechosas = procesar_crudas(crudas)
    sospechosas_totales.extend(sospechosas)
    print(f"  Filtro de rol/nivel/remoto/fraude: {len(aceptadas)} aceptadas, "
          f"{len(sospechosas)} descartadas por señales de fraude.")

    if sospechosas_totales:
        guardar_json(DIR_DATOS / "descartadas_fraude.json",
                     {"actualizado": iso(ahora), "ofertas": sospechosas_totales})

    resumen = actualizar_almacen(aceptadas)
    guardar_json(ARCHIVO_ESTADO, estado)
    print(f"  Almacén: {resumen['total']} activas · {resumen['nuevas_esta_corrida']} nuevas · "
          f"{resumen['expiradas']} archivadas.")

    generar_panel()
    print(f"  Panel actualizado: {ARCHIVO_PANEL}")


# ---------------------------------------------------------------------------
# DATOS DE PRUEBA (--demo)
# ---------------------------------------------------------------------------

def datos_demo() -> list[dict]:
    hace = lambda h: iso(ahora_utc() - timedelta(hours=h))
    return [
        {"fuente": "getonbrd", "titulo": "Analista de Datos Junior", "empresa": "Logística del Sur",
         "url": "https://www.getonbrd.com/jobs/demo-7", "ubicacion": "Remoto (LATAM)",
         "salario": "USD 900 – 1300 /mes", "fecha_publicada": hace(4),
         "descripcion": "Buscamos analista de datos junior con SQL y Excel avanzado para empresa de logística. Trabajo remoto.",
         "pista_seniority": "junior"},
        {"fuente": "remotive", "titulo": "Data Science Intern", "empresa": "BigLab",
         "url": "https://remotive.com/remote-jobs/data/demo-8", "ubicacion": "Worldwide",
         "salario": None, "fecha_publicada": hace(8),
         "descripcion": "Unpaid internship for students.", "pista_seniority": ""},
        {"fuente": "getonbrd", "titulo": "Data Analyst Semi Senior", "empresa": "Fintech Andina",
         "url": "https://www.getonbrd.com/jobs/demo-1", "ubicacion": "Remoto (LATAM)",
         "salario": "USD 1800 – 2500 /mes", "fecha_publicada": hace(6),
         "descripcion": "Buscamos analista de datos con experiencia en SQL, Python y Power BI para una empresa fintech. Requisitos: 3 años de experiencia. Trabajo 100% remoto para LATAM.",
         "pista_seniority": "mid"},
        {"fuente": "remotive", "titulo": "Data Scientist (Mid-level)", "empresa": "HealthTech Co",
         "url": "https://remotive.com/remote-jobs/data/demo-2", "ubicacion": "Latin America",
         "salario": "$45,000 - $60,000", "fecha_publicada": hace(30),
         "descripcion": "We are hiring a mid-level data scientist. Async-first culture, all communication is written. Python, ML pipelines, dbt.",
         "pista_seniority": ""},
        {"fuente": "remoteok", "titulo": "Senior Data Scientist", "empresa": "BigCorp",
         "url": "https://remoteok.com/remote-jobs/demo-3", "ubicacion": "Worldwide",
         "salario": None, "fecha_publicada": hace(10),
         "descripcion": "Senior role, 8+ years experience.", "pista_seniority": ""},
        {"fuente": "jobicy", "titulo": "Business Intelligence Analyst", "empresa": "RetailData",
         "url": "https://jobicy.com/jobs/demo-4", "ubicacion": "Anywhere",
         "salario": None, "fecha_publicada": hace(50),
         "descripcion": "BI analyst position. Fluent spoken English required for daily standups in English with US clients.",
         "pista_seniority": ""},
        {"fuente": "remoteok", "titulo": "Data Analyst — work from home", "empresa": "",
         "url": "https://remoteok.com/remote-jobs/demo-5", "ubicacion": "Worldwide",
         "salario": None, "fecha_publicada": hace(2),
         "descripcion": "Gana $300 diarios sin experiencia. Envía foto de tu cédula y paga la cuota de inscripción por Western Union. Contacto solo por WhatsApp.",
         "pista_seniority": ""},
        {"fuente": "remotive", "titulo": "Data Analyst", "empresa": "US Insurance Group",
         "url": "https://remotive.com/remote-jobs/data/demo-6", "ubicacion": "USA Only",
         "salario": None, "fecha_publicada": hace(12),
         "descripcion": "Must be located in the US. W2 position.", "pista_seniority": ""},
    ]

# ---------------------------------------------------------------------------
# PANEL WEB (docs/index.html)
# ---------------------------------------------------------------------------

PLANTILLA = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex">
<title>Radar de Ofertas · Datos</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;700;800&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root{
    --papel:#F5F8FA; --tinta:#152430; --tinta-suave:#4A6070;
    --linea:#D8E2E8; --tarjeta:#FFFFFF;
    --verde:#0E7C66; --verde-tenue:#E2F2EE;
    --ambar:#9A6B11; --ambar-tenue:#FBF0DA;
    --azul:#2F5D8A; --azul-tenue:#E7EEF5;
    --mono:'IBM Plex Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
    --sans:'Archivo',system-ui,-apple-system,'Segoe UI',sans-serif;
  }
  *{box-sizing:border-box;margin:0}
  body{background:var(--papel);color:var(--tinta);font-family:var(--sans);line-height:1.5}
  a{color:var(--azul)}
  .contenedor{max-width:960px;margin:0 auto;padding:0 20px 80px}

  header{border-bottom:2px solid var(--tinta);padding:34px 0 22px;margin-bottom:14px}
  .cinta{font-family:var(--mono);font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--verde);margin-bottom:8px}
  h1{font-size:clamp(26px,5vw,40px);font-weight:800;letter-spacing:-.02em;line-height:1.1}
  h1 .fino{color:var(--tinta-suave);font-weight:500}
  .metricas{display:flex;flex-wrap:wrap;gap:26px;margin-top:18px;font-family:var(--mono);font-size:13px}
  .metricas b{font-size:22px;display:block;font-weight:500;color:var(--tinta)}
  .metricas span{color:var(--tinta-suave)}

  .aviso-seguridad{background:var(--verde-tenue);border:1px solid var(--verde);border-radius:8px;
    padding:12px 16px;font-size:13.5px;margin:14px 0 22px;color:#0A5648}
  .aviso-seguridad b{color:var(--verde)}

  .filtros{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:22px;
    position:sticky;top:0;background:var(--papel);padding:12px 0;z-index:5;border-bottom:1px solid var(--linea)}
  .filtros input[type=search],.filtros select{font-family:var(--mono);font-size:13px;padding:8px 12px;
    border:1px solid var(--linea);border-radius:6px;background:var(--tarjeta);color:var(--tinta)}
  .filtros input[type=search]{flex:1;min-width:180px}
  .filtros label{font-family:var(--mono);font-size:12px;display:flex;align-items:center;gap:6px;color:var(--tinta-suave);cursor:pointer}

  .tarjeta{background:var(--tarjeta);border:1px solid var(--linea);border-radius:10px;
    padding:18px 20px;margin-bottom:14px;transition:border-color .15s}
  .tarjeta:hover{border-color:var(--azul)}
  .tarjeta.nueva{border-left:4px solid var(--verde)}
  .fila-superior{display:flex;justify-content:space-between;gap:14px;align-items:flex-start;flex-wrap:wrap}
  .titulo-oferta{font-size:18px;font-weight:700;letter-spacing:-.01em}
  .titulo-oferta a{color:var(--tinta);text-decoration:none}
  .titulo-oferta a:hover{text-decoration:underline}
  .empresa{color:var(--tinta-suave);font-size:14.5px;margin-top:2px}

  .confianza{font-family:var(--mono);font-size:11.5px;text-align:right;min-width:120px}
  .confianza .barra{height:6px;border-radius:3px;background:var(--linea);margin-top:5px;overflow:hidden}
  .confianza .relleno{height:100%;background:var(--verde)}
  .confianza.media .relleno{background:var(--ambar)}
  .confianza .etiqueta{color:var(--verde)}
  .confianza.media .etiqueta{color:var(--ambar)}

  .chips{display:flex;flex-wrap:wrap;gap:7px;margin:12px 0 4px}
  .chip{font-family:var(--mono);font-size:11.5px;padding:3px 9px;border-radius:5px;
    background:var(--azul-tenue);color:var(--azul);border:1px solid transparent}
  .chip.nueva{background:var(--verde);color:#fff}
  .chip.advertencia{background:var(--ambar-tenue);color:var(--ambar);border-color:var(--ambar)}
  .chip.salario{background:var(--verde-tenue);color:var(--verde)}

  .resumen{font-size:13.5px;color:var(--tinta-suave);margin-top:8px}
  .acciones{margin-top:14px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
  .boton{display:inline-block;background:var(--tinta);color:#fff;text-decoration:none;
    font-family:var(--mono);font-size:12.5px;padding:9px 16px;border-radius:6px}
  .boton:hover{background:var(--azul)}
  .fecha{font-family:var(--mono);font-size:11.5px;color:var(--tinta-suave)}

  .vacio{text-align:center;padding:60px 0;color:var(--tinta-suave);font-family:var(--mono);font-size:14px}
  footer{margin-top:44px;padding-top:18px;border-top:1px solid var(--linea);
    font-size:12.5px;color:var(--tinta-suave)}
  footer ul{margin:8px 0 0 18px}
  @media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style>
</head>
<body>
<div class="contenedor">
  <header>
    <div class="cinta">Radar personal · Actualización automática cada 4 h</div>
    <h1>Ofertas de datos <span class="fino">· remotas · junior y middle</span></h1>
    <div class="metricas">
      <div><b id="m-total">0</b><span>activas</span></div>
      <div><b id="m-nuevas">0</b><span>nuevas (24 h)</span></div>
      <div><b id="m-fuentes">0</b><span>fuentes</span></div>
      <div><b id="m-fecha">—</b><span>última actualización</span></div>
    </div>
  </header>

  <div class="aviso-seguridad">
    <b>Reglas de oro:</b> ninguna empresa legítima cobra por contratarte ni pide tu cédula,
    cuenta bancaria o pagos «de inscripción». Postula siempre desde el <b>enlace oficial</b> de cada
    tarjeta, nunca por WhatsApp o Telegram de desconocidos.
  </div>

  <div class="filtros">
    <input type="search" id="buscar" placeholder="Buscar título, empresa, tecnología…">
    <select id="f-fuente"><option value="">Todas las fuentes</option></select>
    <select id="f-idioma">
      <option value="">Idioma: todos</option>
      <option value="es">Español</option>
      <option value="en">Inglés</option>
    </select>
    <select id="f-nivel">
      <option value="">Nivel: todos</option>
      <option value="Junior">Junior</option>
      <option value="Middle / Semi Senior">Middle / Semi Senior</option>
      <option value="No especificado">Sin especificar</option>
    </select>
    <label><input type="checkbox" id="f-nuevas"> Solo nuevas</label>
    <label><input type="checkbox" id="f-sin-oral" checked> Ocultar inglés hablado</label>
  </div>

  <div id="lista"></div>
  <div id="vacio" class="vacio" hidden>Sin resultados con estos filtros.</div>

  <footer>
    Datos obtenidos de las APIs públicas oficiales de Get on Board, Remotive, Remote OK y Jobicy,
    con enlace y atribución a la oferta original en cada fuente. Este panel es de uso personal,
    no almacena datos tuyos y no requiere registro.
    <ul>
      <li>Índice de confianza ≥ 85: sin señales de riesgo detectadas.</li>
      <li>Índice 60–84: revisa las advertencias de la tarjeta antes de postular.</li>
      <li>Menos de 60: la oferta se descarta automáticamente y no se muestra.</li>
    </ul>
  </footer>
</div>

<script>
const OFERTAS = __DATA_JSON__;
const META = __META_JSON__;

const fmtFecha = t => {
  if(!t) return "";
  const d = new Date(t);
  return isNaN(d) ? "" : d.toLocaleDateString("es-CO",{day:"numeric",month:"short"});
};
const esNueva = o => META.ahora - new Date(o.detectada).getTime() < __HORAS_NUEVA__*3600*1000;
const esc = s => (s||"").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

function pintar(){
  const q = document.getElementById("buscar").value.toLowerCase();
  const fuente = document.getElementById("f-fuente").value;
  const idioma = document.getElementById("f-idioma").value;
  const nivel = document.getElementById("f-nivel").value;
  const soloNuevas = document.getElementById("f-nuevas").checked;
  const sinOral = document.getElementById("f-sin-oral").checked;

  const visibles = OFERTAS.filter(o =>
    (!q || (o.titulo+" "+o.empresa+" "+o.resumen).toLowerCase().includes(q)) &&
    (!fuente || o.fuente === fuente) &&
    (!idioma || o.idioma === idioma) &&
    (!nivel || o.seniority === nivel) &&
    (!soloNuevas || esNueva(o)) &&
    (!sinOral || !o.aviso_ingles_hablado)
  ).sort((a,b)=> new Date(b.detectada)-new Date(a.detectada) || b.confianza-a.confianza);

  document.getElementById("lista").innerHTML = visibles.map(o => {
    const nueva = esNueva(o);
    const nivelConf = o.confianza >= 85 ? "" : "media";
    const etiquetaConf = o.confianza >= 85 ? "verificada" : "revisar";
    const chips = [
      nueva ? '<span class="chip nueva">NUEVA</span>' : "",
      `<span class="chip">${esc(o.fuente_nombre)}</span>`,
      `<span class="chip">${esc(o.ubicacion)}</span>`,
      `<span class="chip">${o.idioma === "es" ? "Español" : "Inglés"}</span>`,
      o.seniority !== "No especificado" ? `<span class="chip">${esc(o.seniority)}</span>` : "",
      o.salario ? `<span class="chip salario">${esc(o.salario)}</span>` : "",
      o.aviso_ingles_hablado ? '<span class="chip advertencia">Posible inglés hablado</span>' : "",
      o.aviso_geografico ? `<span class="chip advertencia">${esc(o.aviso_geografico)}</span>` : "",
      ...o.banderas.map(b => `<span class="chip advertencia">⚠ ${esc(b)}</span>`)
    ].join("");
    return `<article class="tarjeta ${nueva?"nueva":""}">
      <div class="fila-superior">
        <div>
          <div class="titulo-oferta"><a href="${esc(o.url)}" target="_blank" rel="noopener noreferrer">${esc(o.titulo)}</a></div>
          <div class="empresa">${esc(o.empresa)}</div>
        </div>
        <div class="confianza ${nivelConf}">
          <span class="etiqueta">confianza ${o.confianza}/100 · ${etiquetaConf}</span>
          <div class="barra"><div class="relleno" style="width:${o.confianza}%"></div></div>
        </div>
      </div>
      <div class="chips">${chips}</div>
      ${o.resumen ? `<div class="resumen">${esc(o.resumen)}…</div>` : ""}
      <div class="acciones">
        <a class="boton" href="${esc(o.url)}" target="_blank" rel="noopener noreferrer">Postular en el sitio oficial →</a>
        <span class="fecha">${o.fecha_publicada ? "publicada "+fmtFecha(o.fecha_publicada)+" · " : ""}detectada ${fmtFecha(o.detectada)}</span>
      </div>
    </article>`;
  }).join("");
  document.getElementById("vacio").hidden = visibles.length > 0;
}

function iniciar(){
  document.getElementById("m-total").textContent = OFERTAS.length;
  document.getElementById("m-nuevas").textContent = OFERTAS.filter(esNueva).length;
  const fuentes = [...new Set(OFERTAS.map(o=>o.fuente))];
  document.getElementById("m-fuentes").textContent = META.fuentes_activas;
  document.getElementById("m-fecha").textContent = META.actualizado
    ? new Date(META.actualizado).toLocaleString("es-CO",{day:"numeric",month:"short",hour:"2-digit",minute:"2-digit"}) : "—";
  const sel = document.getElementById("f-fuente");
  fuentes.forEach(f => {
    const o = OFERTAS.find(x=>x.fuente===f);
    sel.insertAdjacentHTML("beforeend", `<option value="${f}">${esc(o.fuente_nombre)}</option>`);
  });
  ["buscar","f-fuente","f-idioma","f-nivel","f-nuevas","f-sin-oral"].forEach(id =>
    document.getElementById(id).addEventListener("input", pintar));
  pintar();
}
iniciar();
</script>
</body>
</html>
"""


def generar_panel() -> None:
    almacen = cargar_json(ARCHIVO_OFERTAS, {"ofertas": {}, "actualizado": None})
    ofertas = sorted(almacen.get("ofertas", {}).values(),
                     key=lambda o: o.get("detectada", ""), reverse=True)
    # El panel solo muestra ofertas de los últimos DIAS_MAX_ANTIGUEDAD días,
    # incluidas las almacenadas antes de que existiera este filtro.
    ofertas = [o for o in ofertas
               if es_reciente(o.get("fecha_publicada"), o.get("detectada"))]
    meta = {
        "actualizado": almacen.get("actualizado"),
        "ahora": int(ahora_utc().timestamp() * 1000),
        "fuentes_activas": sum(1 for f in FUENTES.values() if f["activa"]),
    }
    pagina = (PLANTILLA
              .replace("__DATA_JSON__", json.dumps(ofertas, ensure_ascii=False).replace("</", "<\\/"))
              .replace("__META_JSON__", json.dumps(meta).replace("</", "<\\/"))
              .replace("__HORAS_NUEVA__", str(HORAS_COMO_NUEVA)))
    ARCHIVO_PANEL.parent.mkdir(parents=True, exist_ok=True)
    ARCHIVO_PANEL.write_text(pagina, encoding="utf-8")


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cazador de empleos de datos (remoto, middle).")
    parser.add_argument("--demo", action="store_true", help="Ejecuta con datos de ejemplo (sin internet).")
    parser.add_argument("--forzar", action="store_true", help="Ignora los límites de frecuencia por fuente.")
    args = parser.parse_args()
    try:
        ejecutar(forzar=args.forzar, demo=args.demo)
    except KeyboardInterrupt:
        sys.exit(130)
