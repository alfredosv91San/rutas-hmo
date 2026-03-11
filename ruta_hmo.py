import streamlit as st
import folium
from streamlit_folium import st_folium
import requests
import json
import os
import math
import time
import random
from datetime import datetime
import urllib.parse

st.set_page_config(
    page_title="HMO Rutas",
    page_icon="🚚",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ── CSS móvil-first dark ───────────────────────────────────────────────────
st.markdown("""
<style>
  #MainMenu, footer, header {visibility: hidden;}
  .block-container {padding: 0.5rem 0.7rem 4rem; max-width: 600px; margin: auto;}

  /* Barra progreso */
  .prog-wrap {background:#2d2d3d;border-radius:8px;height:10px;margin:4px 0 10px}
  .prog-fill  {background:linear-gradient(90deg,#22c55e,#16a34a);border-radius:8px;height:10px;transition:width .4s}

  /* Tarjeta parada */
  .scard {
    background:#1e1e2e; border-radius:12px; padding:12px 14px;
    margin-bottom:8px; border-left:4px solid #6366f1; color:#e2e8f0;
    box-shadow:0 2px 8px #0004;
  }
  .scard.ent  {border-color:#22c55e}
  .scard.fall {border-color:#ef4444}
  .scard.enc  {border-color:#f59e0b; background:#1e1c12}

  /* Badge */
  .badge {display:inline-block;padding:2px 10px;border-radius:20px;font-size:.7rem;font-weight:700}
  .bp {background:#312e81;color:#a5b4fc}
  .be {background:#14532d;color:#86efac}
  .bf {background:#450a0a;color:#fca5a5}
  .bc {background:#451a03;color:#fcd34d}

  /* Sugerencias autocompletado */
  .sug-item {
    padding:10px 12px; background:#1e1e2e; border-bottom:1px solid #2d2d3d;
    cursor:pointer; font-size:.9rem; color:#e2e8f0;
  }
  .sug-item:hover {background:#2d2d3d}
  .sug-box {border:1px solid #3d3d5d;border-radius:8px;overflow:hidden;margin-top:2px}

  /* Botones */
  .stButton>button {border-radius:10px!important;font-weight:700!important;min-height:44px!important}

  /* Nav buttons */
  .nav-btn {
    display:block;text-align:center;padding:12px;border-radius:10px;
    font-weight:700;font-size:.95rem;text-decoration:none;margin-bottom:4px;
  }
  .nav-gmaps {background:#4285F4;color:white!important}
  .nav-waze  {background:#33CCFF;color:#000!important}
</style>
""", unsafe_allow_html=True)

DB_FILE = "datos_ruta.json"
HDRS    = {"User-Agent": "HMO-Rutas/2.0 repartidor-hermosillo"}

# ══════════════════════════════════════════════════════════════════
# PERSISTENCIA
# ══════════════════════════════════════════════════════════════════
def guardar():
    try:
        with open(DB_FILE, "w") as f:
            json.dump({
                "puntos":    st.session_state.puntos,
                "resultado": st.session_state.resultado,
                "estados":   {str(k): v for k, v in st.session_state.estados.items()},
                "activo":    st.session_state.activo,
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        st.toast(f"Error al guardar: {e}", icon="⚠️")

def cargar():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE) as f:
                d = json.load(f)
            st.session_state.puntos    = d.get("puntos", [])
            st.session_state.resultado = d.get("resultado", None)
            st.session_state.estados   = {int(k): v for k, v in d.get("estados", {}).items()}
            st.session_state.activo    = d.get("activo", None)
            return
        except Exception:
            pass
    st.session_state.puntos    = []
    st.session_state.resultado = None
    st.session_state.estados   = {}
    st.session_state.activo    = None

for k in ["puntos","resultado","estados","activo"]:
    if k not in st.session_state:
        cargar(); break

# ══════════════════════════════════════════════════════════════════
# GEOCODING + AUTOCOMPLETADO (Photon — OSM, gratis, sin key)
# ══════════════════════════════════════════════════════════════════
def autocompletar(texto: str, limite=6):
    """Photon API — devuelve lista de {nombre, lat, lon}"""
    if len(texto) < 3:
        return []
    try:
        url = "https://photon.komoot.io/api/"
        params = {
            "q":       texto,
            "limit":   limite,
            "lang":    "es",
            "lat":     29.0729,   # centro Hermosillo
            "lon":    -110.9559,
            "zoom":    12,
            # bbox Hermosillo para priorizar resultados locales
            "bbox":   "-111.2,28.8,-110.5,29.4",
        }
        r = requests.get(url, params=params, headers=HDRS, timeout=5)
        feats = r.json().get("features", [])
        resultados = []
        for f in feats:
            props = f["properties"]
            coords = f["geometry"]["coordinates"]  # [lon, lat]
            # Construir etiqueta legible
            partes = []
            for campo in ["name","street","housenumber","district","city","state"]:
                v = props.get(campo)
                if v and v not in partes:
                    partes.append(v)
            label = ", ".join(partes[:4]) if partes else texto
            resultados.append({
                "nombre": label,
                "lat":    coords[1],
                "lon":    coords[0],
            })
        return resultados
    except Exception:
        return []

def geocodificar_nominatim(texto: str):
    """Fallback geocoding con Nominatim"""
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {"q": f"{texto}, Hermosillo, Sonora, México",
                  "format": "json", "limit": 1}
        r = requests.get(url, params=params, headers=HDRS, timeout=8)
        data = r.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"]), data[0].get("display_name", texto)
    except Exception:
        pass
    return None, None, None

# ══════════════════════════════════════════════════════════════════
# MATRIZ OSRM — bloques para soportar hasta 250 paradas
# ══════════════════════════════════════════════════════════════════
def haversine_seg(p1, p2, vel_kmh=40):
    R = 6371000
    lat1, lat2 = math.radians(p1["lat"]), math.radians(p2["lat"])
    dlat = math.radians(p2["lat"] - p1["lat"])
    dlon = math.radians(p2["lon"] - p1["lon"])
    a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    dist_m = 2 * R * math.asin(math.sqrt(a))
    return dist_m / (vel_kmh * 1000 / 3600)

def matriz_haversine(puntos):
    n = len(puntos)
    return [[haversine_seg(puntos[i], puntos[j]) for j in range(n)] for i in range(n)]

def matriz_osrm_bloque(puntos, orig_indices, dest_indices):
    """Llama OSRM Table API para un bloque de orígenes × destinos."""
    # OSRM admite hasta ~100×100 en el servidor público
    src_coords = ";".join(f"{puntos[i]['lon']},{puntos[i]['lat']}" for i in orig_indices)
    dst_coords = ";".join(f"{puntos[j]['lon']},{puntos[j]['lat']}" for j in dest_indices)
    all_coords = src_coords + ";" + dst_coords
    src_ids = ";".join(str(k) for k in range(len(orig_indices)))
    dst_ids = ";".join(str(k + len(orig_indices)) for k in range(len(dest_indices)))
    url = f"http://router.project-osrm.org/table/v1/driving/{all_coords}"
    params = {"sources": src_ids, "destinations": dst_ids, "annotations": "duration"}
    try:
        r = requests.get(url, params=params, headers=HDRS, timeout=25)
        data = r.json()
        if data.get("code") == "Ok":
            return data["durations"]
    except Exception:
        pass
    return None

def matriz_distancias(puntos, progress_cb=None):
    """
    Construye la matriz completa NxN usando OSRM en bloques de 50.
    Fallback a Haversine si OSRM falla.
    """
    n = len(puntos)
    mat = [[0.0] * n for _ in range(n)]
    BLOQUE = 50  # máximo seguro para OSRM público

    total_bloques = math.ceil(n / BLOQUE) ** 2
    bloque_actual = 0

    for i_start in range(0, n, BLOQUE):
        orig_idx = list(range(i_start, min(i_start + BLOQUE, n)))
        for j_start in range(0, n, BLOQUE):
            dest_idx = list(range(j_start, min(j_start + BLOQUE, n)))

            bloque_actual += 1
            if progress_cb:
                progress_cb(bloque_actual / total_bloques,
                            f"Calculando rutas... bloque {bloque_actual}/{total_bloques}")

            result = matriz_osrm_bloque(puntos, orig_idx, dest_idx)

            if result:
                for ri, oi in enumerate(orig_idx):
                    for rj, dj in enumerate(dest_idx):
                        val = result[ri][rj]
                        mat[oi][dj] = val if val is not None else haversine_seg(puntos[oi], puntos[dj])
            else:
                # Fallback haversine para este bloque
                for oi in orig_idx:
                    for dj in dest_idx:
                        mat[oi][dj] = haversine_seg(puntos[oi], puntos[dj])

            time.sleep(0.1)  # respetar rate limit OSRM público

    return mat

# ══════════════════════════════════════════════════════════════════
# OPTIMIZACIÓN TSP — Nearest Neighbor + 2-opt
# (funciona bien hasta ~250 paradas en pocos segundos)
# ══════════════════════════════════════════════════════════════════
def tsp_nearest_neighbor(mat, inicio=0):
    n = len(mat)
    visitado = [False] * n
    ruta = [inicio]
    visitado[inicio] = True
    for _ in range(n - 1):
        actual = ruta[-1]
        mejor_j, mejor_t = -1, float("inf")
        for j in range(n):
            if not visitado[j] and mat[actual][j] < mejor_t:
                mejor_j, mejor_t = j, mat[actual][j]
        ruta.append(mejor_j)
        visitado[mejor_j] = True
    return ruta

def costo_ruta(ruta, mat):
    return sum(mat[ruta[i]][ruta[i+1]] for i in range(len(ruta)-1))

def dos_opt(ruta, mat, max_iter=500):
    """2-opt con límite de iteraciones para no tardar demasiado con 250 paradas."""
    mejor = ruta[:]
    mejor_costo = costo_ruta(mejor, mat)
    for _ in range(max_iter):
        mejorado = False
        for i in range(1, len(mejor) - 1):
            for j in range(i + 1, len(mejor)):
                nueva = mejor[:i] + mejor[i:j+1][::-1] + mejor[j+1:]
                c = costo_ruta(nueva, mat)
                if c < mejor_costo - 0.001:
                    mejor, mejor_costo = nueva, c
                    mejorado = True
        if not mejorado:
            break
    return mejor

def optimizar(progress_bar, status_txt):
    pts = st.session_state.puntos
    n   = len(pts)
    if n < 2:
        return False, "Necesitas al menos 2 paradas."

    def cb(pct, msg):
        progress_bar.progress(pct * 0.85)  # 85% para la matriz
        status_txt.text(msg)

    status_txt.text("📡 Conectando con OSRM para calcular rutas reales…")
    mat = matriz_distancias(pts, progress_cb=cb)

    status_txt.text("🧠 Calculando orden óptimo…")
    progress_bar.progress(0.88)

    # Intentar varios puntos de inicio y quedarse con el mejor
    mejor_ruta, mejor_costo = None, float("inf")
    inicios = [0] + random.sample(range(1, n), min(4, n-1))
    for ini in inicios:
        r = tsp_nearest_neighbor(mat, inicio=ini)
        c = costo_ruta(r, mat)
        if c < mejor_costo:
            mejor_ruta, mejor_costo = r, c

    progress_bar.progress(0.93)
    status_txt.text("✨ Refinando ruta con 2-opt…")
    mejor_ruta = dos_opt(mejor_ruta, mat)

    progress_bar.progress(1.0)
    status_txt.text("✅ ¡Ruta optimizada!")

    st.session_state.resultado = mejor_ruta
    st.session_state.estados   = {}
    st.session_state.activo    = mejor_ruta[0]
    guardar()
    return True, f"Ruta de {n} paradas optimizada."

# ══════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════
def progreso():
    ruta = st.session_state.resultado or []
    total = len(ruta)
    done  = sum(1 for i in ruta
                if st.session_state.estados.get(i, {}).get("status") in ("Entregado","Fallido"))
    return done, total

def url_gmaps(p):
    return (f"https://www.google.com/maps/dir/?api=1"
            f"&destination={p['lat']},{p['lon']}&travelmode=driving")

def url_waze(p):
    return f"https://waze.com/ul?ll={p['lat']},{p['lon']}&navigate=yes"

def marcar(orig_idx, status, nota):
    ruta = st.session_state.resultado
    st.session_state.estados[orig_idx] = {
        "status": status,
        "hora":   datetime.now().strftime("%H:%M"),
        "nota":   nota,
    }
    # Avanzar activo al siguiente pendiente
    pendientes = [i for i in ruta
                  if st.session_state.estados.get(i,{}).get("status")
                  not in ("Entregado","Fallido","En curso") and i != orig_idx]
    st.session_state.activo = pendientes[0] if pendientes else None
    guardar()

# ══════════════════════════════════════════════════════════════════
# UI — 3 TABS
# ══════════════════════════════════════════════════════════════════
t_ruta, t_agregar, t_resumen = st.tabs(["🗺️ Ruta", "➕ Paradas", "📊 Resumen"])

# ─────────────────────────────────────────────────────────────────
# TAB 1 — RUTA
# ─────────────────────────────────────────────────────────────────
with t_ruta:
    n_pts = len(st.session_state.puntos)

    if n_pts == 0:
        st.info("👉 Ve a **Paradas** para agregar direcciones.")
    else:
        # Progreso
        done, total = progreso()
        pct = int(done/total*100) if total else 0
        st.markdown(f"""
        <div style='display:flex;justify-content:space-between;font-size:.78rem;color:#94a3b8;margin-bottom:2px'>
          <span>Progreso del día</span><span>{done}/{total} · {pct}%</span>
        </div>
        <div class='prog-wrap'><div class='prog-fill' style='width:{pct}%'></div></div>
        """, unsafe_allow_html=True)

        # Botón optimizar
        col_opt, col_del = st.columns([4, 1])
        with col_opt:
            opt_btn = st.button(
                f"⚡ OPTIMIZAR {n_pts} PARADAS",
                use_container_width=True, type="primary",
                disabled=(n_pts < 2)
            )
        with col_del:
            del_btn = st.button("🗑️", use_container_width=True, help="Borrar todo")

        if del_btn:
            if os.path.exists(DB_FILE): os.remove(DB_FILE)
            st.session_state.clear(); st.rerun()

        if opt_btn:
            prog = st.progress(0)
            txt  = st.empty()
            ok, msg = optimizar(prog, txt)
            time.sleep(0.6)
            prog.empty(); txt.empty()
            st.toast(msg, icon="✅" if ok else "❌")
            st.rerun()

        # ── MAPA ──────────────────────────────────────────────
        if st.session_state.resultado:
            ruta = st.session_state.resultado
            pts  = st.session_state.puntos
            pts_ord = [pts[i] for i in ruta]

            # Centro en parada activa o primera
            activo_idx = st.session_state.activo
            centro = next(
                (pts[i] for i in ruta if i == activo_idx),
                pts_ord[0]
            )
            m = folium.Map(location=[centro["lat"], centro["lon"]], zoom_start=13,
                           tiles="OpenStreetMap")

            # Línea de ruta
            folium.PolyLine(
                [[p["lat"], p["lon"]] for p in pts_ord],
                color="#6366f1", weight=3, opacity=0.65, dash_array="6"
            ).add_to(m)

            # Marcadores
            for orden, orig_i in enumerate(ruta):
                p   = pts[orig_i]
                est = st.session_state.estados.get(orig_i, {}).get("status","Pendiente")
                color = {"Entregado":"#22c55e","Fallido":"#ef4444",
                         "En curso":"#f59e0b"}.get(est, "#6366f1")
                border = "3px solid #fff" if orig_i == activo_idx else "2px solid #fff8"
                size   = "34px" if orig_i == activo_idx else "28px"
                folium.Marker(
                    [p["lat"], p["lon"]],
                    tooltip=f"#{orden+1} {p['nombre'][:30]}",
                    icon=folium.DivIcon(html=f"""
                        <div style='background:{color};color:white;border-radius:50%;
                             width:{size};height:{size};display:flex;align-items:center;
                             justify-content:center;font-weight:900;font-size:12px;
                             border:{border};box-shadow:0 2px 8px #0006;'>
                          {orden+1}
                        </div>""", icon_size=(38,38), icon_anchor=(19,19))
                ).add_to(m)

            st_folium(m, width="100%", height=270,
                      key="mapa_ruta", returned_objects=[])
            st.markdown("---")

            # ── LISTA PARADAS ──────────────────────────────────
            for orden, orig_i in enumerate(ruta):
                p   = pts[orig_i]
                est = st.session_state.estados.get(orig_i, {})
                status = est.get("status","Pendiente")
                hora   = est.get("hora","")
                nota_g = est.get("nota","")
                is_act = (orig_i == activo_idx and status == "Pendiente")

                ico = {"Entregado":"✅","Fallido":"❌","En curso":"▶️"}.get(status,"🔶" if is_act else "⚪")
                badge_cls = {"Entregado":"be","Fallido":"bf","En curso":"bc"}.get(status,"bp")
                card_cls  = {"Entregado":"ent","Fallido":"fall","En curso":"enc"}.get(status,"")
                if is_act: card_cls = "enc"

                with st.expander(
                    f"{ico} #{orden+1}  {p['nombre'][:38]}{'…' if len(p['nombre'])>38 else ''}",
                    expanded=is_act
                ):
                    st.markdown(
                        f'<span class="badge {badge_cls}">{status}</span>'
                        + (f' &nbsp;🕐 {hora}' if hora else ""),
                        unsafe_allow_html=True
                    )

                    nota = st.text_input("📝 Nota:", key=f"nota_{orig_i}",
                                         value=nota_g, placeholder="Opcional…",
                                         label_visibility="collapsed")

                    # Botones de navegación
                    c1, c2 = st.columns(2)
                    with c1:
                        st.markdown(
                            f'<a class="nav-btn nav-gmaps" href="{url_gmaps(p)}" target="_blank">'
                            f'🗺️ Google Maps</a>', unsafe_allow_html=True)
                    with c2:
                        st.markdown(
                            f'<a class="nav-btn nav-waze" href="{url_waze(p)}" target="_blank">'
                            f'🔵 Waze</a>', unsafe_allow_html=True)

                    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)

                    # Acciones
                    ca, cb, cc = st.columns(3)
                    if ca.button("▶️ En curso",   key=f"enc_{orig_i}", use_container_width=True):
                        st.session_state.estados[orig_i] = {"status":"En curso","hora":datetime.now().strftime("%H:%M"),"nota":nota}
                        st.session_state.activo = orig_i
                        guardar(); st.rerun()
                    if cb.button("✅ Entregado", key=f"ent_{orig_i}", use_container_width=True):
                        marcar(orig_i, "Entregado", nota); st.rerun()
                    if cc.button("❌ Fallido",   key=f"fal_{orig_i}", use_container_width=True):
                        marcar(orig_i, "Fallido",   nota); st.rerun()

        else:
            # Sin optimizar aún
            m = folium.Map(location=[29.0729,-110.9559], zoom_start=12)
            for p in st.session_state.puntos:
                folium.CircleMarker([p["lat"],p["lon"]], radius=7,
                                    color="#6366f1", fill=True, fill_opacity=0.8,
                                    tooltip=p["nombre"]).add_to(m)
            st_folium(m, width="100%", height=270, key="mapa_base", returned_objects=[])
            st.caption("Toca ⚡ OPTIMIZAR para calcular el mejor orden.")

# ─────────────────────────────────────────────────────────────────
# TAB 2 — AGREGAR PARADAS
# ─────────────────────────────────────────────────────────────────
with t_agregar:
    st.subheader(f"Paradas ({len(st.session_state.puntos)}/250)")

    # ── Buscador con autocompletado ──
    st.markdown("**🔍 Buscar dirección en Hermosillo:**")
    busq = st.text_input(
        "Escribe la calle o colonia:",
        key="busq_input",
        placeholder="Ej: Blvd Morelos 123, Col Pitic…",
        label_visibility="collapsed"
    )

    if busq and len(busq) >= 3:
        sugs = autocompletar(busq)
        if sugs:
            st.markdown('<div class="sug-box">', unsafe_allow_html=True)
            for sug in sugs:
                col_s, col_b = st.columns([5, 1])
                col_s.markdown(
                    f'<div class="sug-item">📍 {sug["nombre"]}</div>',
                    unsafe_allow_html=True
                )
                if col_b.button("＋", key=f"sug_{sug['lat']}_{sug['lon']}", help="Agregar"):
                    if len(st.session_state.puntos) >= 250:
                        st.warning("Límite de 250 paradas alcanzado.")
                    else:
                        st.session_state.puntos.append(sug)
                        st.session_state.resultado = None
                        st.session_state.estados   = {}
                        guardar()
                        st.toast(f"✅ Agregado: {sug['nombre'][:40]}", icon="📍")
                        st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)
        else:
            st.caption("Sin resultados. Intenta con más detalle o diferente nombre.")

    st.markdown("---")

    # ── Pegar lista masiva ──
    with st.expander("📋 Cargar lista completa (hasta 250)"):
        txt_lista = st.text_area(
            "Una dirección por línea:",
            height=180,
            placeholder="Blvd Morelos 100\nCalle Yáñez 45\nPaseo Río Sonora Sur…"
        )
        if st.button("📥 Geocodificar y agregar lista", use_container_width=True):
            lineas = [l.strip() for l in txt_lista.strip().split("\n") if l.strip()]
            if not lineas:
                st.warning("Escribe al menos una dirección.")
            else:
                ok_list, fail_list = [], []
                prog_geo = st.progress(0)
                for idx_l, linea in enumerate(lineas):
                    if len(st.session_state.puntos) >= 250:
                        st.warning("Límite 250 alcanzado."); break
                    prog_geo.progress((idx_l+1)/len(lineas))
                    # Intentar con Photon primero
                    sugs = autocompletar(linea, limite=1)
                    if sugs:
                        st.session_state.puntos.append(sugs[0])
                        ok_list.append(linea)
                    else:
                        # Fallback Nominatim
                        lat, lon, name = geocodificar_nominatim(linea)
                        if lat:
                            st.session_state.puntos.append({"nombre": name or linea, "lat": lat, "lon": lon})
                            ok_list.append(linea)
                        else:
                            fail_list.append(linea)
                    time.sleep(0.12)  # respetar rate limit
                prog_geo.empty()
                st.session_state.resultado = None
                st.session_state.estados   = {}
                guardar()
                if ok_list:   st.success(f"✅ {len(ok_list)} paradas agregadas.")
                if fail_list: st.warning(f"⚠️ No encontradas: {', '.join(fail_list[:5])}")
                if ok_list: st.rerun()

    st.markdown("---")
    st.markdown(f"**Lista actual — {len(st.session_state.puntos)} paradas**")

    if not st.session_state.puntos:
        st.caption("Sin paradas aún.")
    else:
        for idx_p, p in enumerate(st.session_state.puntos):
            c_n, c_b = st.columns([6, 1])
            c_n.markdown(f"**{idx_p+1}.** {p['nombre'][:50]}")
            if c_b.button("✕", key=f"rm_{idx_p}"):
                st.session_state.puntos.pop(idx_p)
                st.session_state.resultado = None
                st.session_state.estados   = {}
                guardar(); st.rerun()

        if st.button("🗑️ Limpiar todas las paradas", use_container_width=True):
            st.session_state.puntos    = []
            st.session_state.resultado = None
            st.session_state.estados   = {}
            guardar(); st.rerun()

# ─────────────────────────────────────────────────────────────────
# TAB 3 — RESUMEN
# ─────────────────────────────────────────────────────────────────
with t_resumen:
    st.subheader("📊 Resumen del día")

    if not st.session_state.resultado:
        st.info("Optimiza la ruta primero.")
    else:
        ruta  = st.session_state.resultado
        pts   = st.session_state.puntos
        total = len(ruta)
        ent   = sum(1 for i in ruta if st.session_state.estados.get(i,{}).get("status")=="Entregado")
        fall  = sum(1 for i in ruta if st.session_state.estados.get(i,{}).get("status")=="Fallido")
        enc   = sum(1 for i in ruta if st.session_state.estados.get(i,{}).get("status")=="En curso")
        pend  = total - ent - fall - enc

        c1,c2,c3,c4 = st.columns(4)
        c1.metric("📦 Total",       total)
        c2.metric("✅ Entregados",  ent)
        c3.metric("❌ Fallidos",    fall)
        c4.metric("⏳ Pendientes",  pend)

        pct = int(ent/total*100) if total else 0
        st.markdown(f"""
        <div class='prog-wrap'>
          <div class='prog-fill' style='width:{pct}%'></div>
        </div>
        <div style='font-size:.78rem;color:#94a3b8;margin-bottom:10px'>
          {ent} de {total} entregados ({pct}%)
        </div>
        """, unsafe_allow_html=True)

        # Lista resumen
        for orden, orig_i in enumerate(ruta):
            p      = pts[orig_i]
            est    = st.session_state.estados.get(orig_i, {})
            status = est.get("status","Pendiente")
            hora   = est.get("hora","—")
            nota   = est.get("nota","")
            color  = {"Entregado":"#22c55e","Fallido":"#ef4444",
                      "En curso":"#f59e0b"}.get(status,"#475569")
            ico    = {"Entregado":"✅","Fallido":"❌","En curso":"▶️"}.get(status,"⏳")
            st.markdown(f"""
            <div style='background:#1e1e2e;border-radius:8px;padding:9px 13px;
                        margin-bottom:5px;border-left:3px solid {color}'>
              <div style='display:flex;justify-content:space-between'>
                <span style='font-weight:700;color:#e2e8f0'>#{orden+1} {p['nombre'][:42]}</span>
                <span style='font-size:.78rem;color:#94a3b8'>{hora}</span>
              </div>
              <div style='font-size:.78rem;color:#94a3b8;margin-top:2px'>
                {ico} {status}{' · '+nota if nota else ''}
              </div>
            </div>""", unsafe_allow_html=True)

        st.markdown("---")

        # Exportar WhatsApp
        if st.button("📱 Enviar resumen por WhatsApp", use_container_width=True):
            hoy = datetime.now().strftime("%d/%m/%Y %H:%M")
            msg = f"🚚 *Reporte {hoy}*\n"
            msg += f"✅ {ent}  ❌ {fall}  ⏳ {pend}  📦 {total}\n\n"
            for orden, orig_i in enumerate(ruta):
                p   = pts[orig_i]
                est = st.session_state.estados.get(orig_i,{})
                s   = est.get("status","Pendiente")
                h   = est.get("hora","")
                n   = est.get("nota","")
                ico = "✅" if s=="Entregado" else "❌" if s=="Fallido" else "⏳"
                msg += f"{ico} #{orden+1} {p['nombre']}"
                if h: msg += f" ({h})"
                if n: msg += f" — {n}"
                msg += "\n"
            st.markdown(
                f'<a href="https://wa.me/?text={urllib.parse.quote(msg)}" target="_blank">'
                f'<button style="width:100%;background:#25D366;color:white;border:none;'
                f'padding:13px;border-radius:10px;font-weight:700;font-size:1rem">'
                f'📱 Abrir WhatsApp</button></a>',
                unsafe_allow_html=True
            )

        st.markdown("---")
        if st.button("🔄 Nuevo día (mantener paradas)", use_container_width=True):
            st.session_state.estados = {}
            st.session_state.activo  = (st.session_state.resultado[0]
                                         if st.session_state.resultado else None)
            guardar(); st.rerun()
