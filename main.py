import streamlit as st
import folium
from streamlit_folium import st_folium
import requests
from streamlit_js_eval import get_geolocation
from ortools.constraint_solver import pywrapcp, routing_enums_pb2
from datetime import datetime
import time
import json
import os

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="HMO Logística Permanente", layout="wide")
HMO_CENTRO = [29.0730, -110.9559]
DB_FILE = "datos_ruta.json"

# --- FUNCIONES DE PERSISTENCIA ---
def guardar_datos():
    datos = {
        "puntos": st.session_state.puntos,
        "resultado": st.session_state.resultado,
        "estados": st.session_state.estados
    }
    with open(DB_FILE, "w") as f:
        json.dump(datos, f)

def cargar_datos():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f:
            datos = json.load(f)
            st.session_state.puntos = datos.get("puntos", [])
            st.session_state.resultado = datos.get("resultado", None)
            st.session_state.estados = {int(k): v for k, v in datos.get("estados", {}).items()}

# --- INICIALIZACIÓN ---
if 'puntos' not in st.session_state:
    cargar_datos() # Intenta cargar al iniciar
    if 'puntos' not in st.session_state: # Si sigue vacío
        st.session_state.puntos = []
        st.session_state.resultado = None
        st.session_state.estados = {}

st.title("🚚 Mi Ruta Permanente (HMO)")

# --- INTERFAZ DE CARGA ---
with st.sidebar:
    st.header("📥 Carga Masiva")
    texto_libre = st.text_area("Pega tus paradas (una por línea):")
    if st.button("Procesar y Guardar"):
        lineas = [l.strip() for l in texto_libre.split('\n') if l.strip()]
        for linea in lineas:
            params = {'q': f"{linea}, Hermosillo, Sonora", 'format': 'json', 'limit': 1}
            try:
                r = requests.get("https://nominatim.openstreetmap.org/search", params=params, headers={'User-Agent':'HMO_Pro'}).json()
                lat, lon = (float(r[0]['lat']), float(r[0]['lon'])) if r else HMO_CENTRO
                st.session_state.puntos.append({"nombre": linea, "lat": lat, "lon": lon})
                time.sleep(0.4)
            except: pass
        guardar_datos()
        st.rerun()

    if st.button("🗑️ BORRAR TODO"):
        if os.path.exists(DB_FILE): os.remove(DB_FILE)
        st.session_state.clear()
        st.rerun()

# --- MAPA CON NUMERACIÓN ---
st.subheader(f"📍 Mapa de Trabajo ({len(st.session_state.puntos)} paradas)")
m = folium.Map(location=HMO_CENTRO, zoom_start=12)

for i, p in enumerate(st.session_state.puntos):
    color = '#1a73e8'
    if i in st.session_state.estados:
        color = '#28a745' if st.session_state.estados[i]['status'] == "Entregado" else '#dc3545'
    
    folium.Marker(
        [p['lat'], p['lon']],
        icon=folium.DivIcon(html=f"""
            <div style="background:{color}; color:white; border-radius:50%; width:24px; height:24px; 
            display:flex; align-items:center; justify-content:center; font-weight:bold; border:2px solid white;">
                {i}
            </div>""")
    ).add_to(m)

st_folium(m, width="100%", height=400, key="mapa_pers")

# --- OPTIMIZACIÓN ---
if len(st.session_state.puntos) >= 2 and not st.session_state.resultado:
    if st.button("🏁 OPTIMIZAR"):
        pts = st.session_state.puntos
        manager = pywrapcp.RoutingIndexManager(len(pts), 1, 0)
        routing = pywrapcp.RoutingModel(manager)
        def dist_e(f, t):
            p1, p2 = pts[manager.IndexToNode(f)], pts[manager.IndexToNode(t)]
            return int(((p1['lat']-p2['lat'])**2 + (p1['lon']-p2['lon'])**2)**0.5 * 100000)
        routing.SetArcCostEvaluatorOfAllVehicles(routing.RegisterTransitCallback(dist_e))
        sol = routing.SolveWithParameters(pywrapcp.DefaultRoutingSearchParameters())
        if sol:
            orden = []
            idx = routing.Start(0)
            while not routing.IsEnd(idx):
                orden.append(manager.IndexToNode(idx))
                idx = sol.Value(routing.NextVar(idx))
            orden.append(manager.IndexToNode(idx))
            st.session_state.resultado = orden
            guardar_datos()
            st.rerun()

# --- LISTA DE NAVEGACIÓN ---
if st.session_state.resultado:
    orden = st.session_state.resultado
    pts = st.session_state.puntos
    
    # Botón de "Iniciar Parada 1"
    if len(orden) > 1:
        p1 = pts[orden[1]]
        url_g = f"https://www.google.com/maps/search/?api=1&query={p1['nombre'].replace(' ','+')}+Hermosillo+Sonora"
        st.markdown(f'<a href="{url_g}" target="_blank"><button style="width:100%; background:#1a73e8; color:white; padding:15px; border-radius:10px; font-weight:bold;">🚀 INICIAR RUTA (Maps)</button></a>', unsafe_allow_html=True)

    for i, idx in enumerate(orden[1:-1], 1):
        p = pts[idx]
        est = st.session_state.estados.get(idx, {'status': 'Pendiente', 'nota': ''})
        
        with st.expander(f"#{i} - {p['nombre']}"):
            col_t, col_b = st.columns([3, 1])
            with col_b:
                url = f"https://www.google.com/maps/search/?api=1&query={p['nombre'].replace(' ','+')}+Hermosillo+Sonora"
                st.markdown(f'<a href="{url}" target="_blank"><button style="width:100%; background:#4285F4; color:white; border:none; padding:8px; border-radius:5px;">Maps</button></a>', unsafe_allow_html=True)
            
            nota = st.text_input("Nota:", value=est['nota'], key=f"n_{idx}")
            c1, c2 = st.columns(2)
            if c1.button("✅ Entregado", key=f"e_{idx}"):
                st.session_state.estados[idx] = {'status': 'Entregado', 'hora': datetime.now().strftime("%H:%M"), 'nota': nota}
                guardar_datos()
                st.rerun()
            if c2.button("❌ Fallido", key=f"f_{idx}"):
                st.session_state.estados[idx] = {'status': 'Fallido', 'hora': datetime.now().strftime("%H:%M"), 'nota': nota}
                guardar_datos()
                st.rerun()