import streamlit as st
import folium
from streamlit_folium import st_folium
import requests
from streamlit_js_eval import get_geolocation
from ortools.constraint_solver import pywrapcp, routing_enums_pb2
from datetime import datetime
import json
import os

st.set_page_config(page_title="HMO Logística Pro", layout="wide")
DB_FILE = "datos_ruta.json"

# --- PERSISTENCIA ---
def guardar():
    with open(DB_FILE, "w") as f:
        json.dump({"puntos": st.session_state.puntos, "resultado": st.session_state.resultado, "estados": st.session_state.estados}, f)

def cargar():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f:
            d = json.load(f)
            st.session_state.puntos = d.get("puntos", [])
            st.session_state.resultado = d.get("resultado", None)
            st.session_state.estados = {int(k): v for k, v in d.get("estados", {}).items()}

if 'puntos' not in st.session_state:
    cargar()
    if 'puntos' not in st.session_state:
        st.session_state.puntos, st.session_state.resultado, st.session_state.estados = [], None, {}

st.title("🚚 Optimización Sincronizada HMO")

# --- BUSCADOR Y CARGA ---
with st.sidebar:
    st.header("Entrada de Datos")
    txt = st.text_area("Pega direcciones (una por línea):")
    if st.button("Cargar Lista"):
        for l in txt.split('\n'):
            if l.strip():
                r = requests.get(f"https://nominatim.openstreetmap.org/search?q={l.strip()}, Hermosillo&format=json&limit=1").json()
                lat, lon = (float(r[0]['lat']), float(r[0]['lon'])) if r else (29.07, -110.95)
                st.session_state.puntos.append({"nombre": l.strip(), "lat": lat, "lon": lon})
        guardar()
        st.rerun()
    
    if st.button("🗑️ Borrar Todo"):
        if os.path.exists(DB_FILE): os.remove(DB_FILE)
        st.session_state.clear()
        st.rerun()

# --- LÓGICA DE OPTIMIZACIÓN ---
def optimizar_ruta():
    pts = st.session_state.puntos
    if len(pts) < 2: return
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
        st.session_state.resultado = orden
        guardar()

if len(st.session_state.puntos) >= 2:
    if st.button("🔄 OPTIMIZAR / RE-CALCULAR RUTA", use_container_width=True, type="primary"):
        optimizar_ruta()
        st.rerun()

# --- MAPA Y LISTA (Sincronizados) ---
st.subheader("📍 Mapa y Orden de Visita")
m = folium.Map(location=[29.07, -110.95], zoom_start=12)

if st.session_state.resultado:
    # Si ya está optimizado, usamos el orden de 'resultado'
    puntos_ordenados = [st.session_state.puntos[i] for i in st.session_state.resultado]
    
    for i, p in enumerate(puntos_ordenados):
        # i es el orden de visita real (0, 1, 2...)
        color = '#28a745' if st.session_state.resultado[i] in st.session_state.estados else '#1a73e8'
        folium.Marker(
            [p['lat'], p['lon']],
            icon=folium.DivIcon(html=f"""
                <div style="background:{color}; color:white; border-radius:50%; width:28px; height:28px; 
                display:flex; align-items:center; justify-content:center; font-weight:bold; border:2px solid white;">
                    {i}
                </div>""")
        ).add_to(m)
    
    st_folium(m, width="100%", height=400, key="mapa_sync")

    st.divider()
    # Mostramos la lista exactamente en el mismo orden que el mapa
    for i, original_idx in enumerate(st.session_state.resultado):
        p = st.session_state.puntos[original_idx]
        est = st.session_state.estados.get(original_idx, {'status': 'Pendiente'})
        
        with st.expander(f"ORDEN #{i}: {p['nombre']}"):
            col1, col2 = st.columns([3, 1])
            with col1:
                st.write(f"Estado: **{est['status']}**")
                nota = st.text_input("Nota:", key=f"n_{original_idx}", value=est.get('nota',''))
            with col2:
                url = f"https://www.google.com/maps/search/?api=1&query={p['nombre'].replace(' ','+')}+Hermosillo"
                st.markdown(f'<a href="{url}" target="_blank"><button style="width:100%; background:#4285F4; color:white; border:none; padding:10px; border-radius:5px;">Maps</button></a>', unsafe_allow_html=True)
            
            c1, c2 = st.columns(2)
            if c1.button("✅ Entregado", key=f"e_{original_idx}"):
                st.session_state.estados[original_idx] = {'status': 'Entregado', 'hora': datetime.now().strftime("%H:%M"), 'nota': nota}
                guardar(); st.rerun()
            if c2.button("❌ Fallido", key=f"f_{original_idx}"):
                st.session_state.estados[original_idx] = {'status': 'Fallido', 'hora': datetime.now().strftime("%H:%M"), 'nota': nota}
                guardar(); st.rerun()
else:
    # Si no se ha optimizado, mostrar puntos normales
    for i, p in enumerate(st.session_state.puntos):
        folium.Marker([p['lat'], p['lon']], tooltip=p['nombre']).add_to(m)
    st_folium(m, width="100%", height=400)
