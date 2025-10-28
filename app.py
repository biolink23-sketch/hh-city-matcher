import streamlit as st
import requests
import pandas as pd
from rapidfuzz import fuzz, process
import io

# Настройка страницы
st.set_page_config(
    page_title="Сопоставление городов с HH.ru",
    page_icon="🌍",
    layout="wide"
)

# ============================================
# ФУНКЦИИ
# ============================================
@st.cache_data(ttl=3600)
def get_hh_areas():
    """Получает справочник HH.ru"""
    response = requests.get('https://api.hh.ru/areas')
    data = response.json()
    
    areas_dict = {}
    
    def parse_areas(areas, parent_name=""):
        for area in areas:
            area_id = area['id']
            area_name = area['name']
            
            areas_dict[area_name] = {
                'id': area_id,
                'name': area_name,
                'parent': parent_name
            }
            
            if area.get('areas'):
                parse_areas(area['areas'], area_name)
    
    parse_areas(data)
    return areas_dict

def match_cities(client_cities, hh_areas, threshold=80):
    """Сопоставляет города"""
    results = []
    hh_city_names = list(hh_areas.keys())
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    for idx, client_city in enumerate(client_cities):
        progress = (idx + 1) / len(client_cities)
        progress_bar.progress(progress)
        status_text.text(f"Обработано {idx + 1} из {len(client_cities)} городов...")
        
        if pd.isna(client_city) or str(client_city).strip() == "":
            results.append({
                'Исходное название': client_city,
                'Название HH': None,
                'ID HH': None,
                'Регион': None,
                'Совпадение %': 0,
                'Статус': '❌ Пустое значение'
            })
            continue
        
        client_city = str(client_city).strip()
        
        match = process.extractOne(
            client_city,
            hh_city_names,
            scorer=fuzz.WRatio,
            score_cutoff=threshold
        )
        
        if match:
            matched_name = match[0]
            score = match[1]
            hh_info = hh_areas[matched_
