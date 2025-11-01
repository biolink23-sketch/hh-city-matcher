import streamlit as st  
import requests  
import pandas as pd  
from rapidfuzz import fuzz, process  
import io  

# Настройка страницы  
st.set_page_config(  
    page_title="Синхронизатор гео HH.ru",  
    page_icon="🌍",  
    layout="wide"  
)  

# CSS для анимации земли и стилей  
st.markdown("""  
<style>  
@keyframes rotate {  
    from { transform: rotate(0deg); }  
    to { transform: rotate(360deg); }  
}  

.rotating-earth {  
    display: inline-block;  
    animation: rotate 3s linear infinite;  
    font-size: 3em;  
    vertical-align: middle;  
    margin-right: 15px;  
}  

.main-title {  
    display: inline-block;  
    font-size: 3em;  
    font-weight: bold;  
    vertical-align: middle;  
    margin: 0;  
}  

.title-container {  
    display: flex;  
    align-items: center;  
    margin-bottom: 20px;  
}  
</style>  
""", unsafe_allow_html=True)  

# Инициализация session_state  
if 'result_df' not in st.session_state:  
    st.session_state.result_df = None  
if 'duplicate_count' not in st.session_state:  
    st.session_state.duplicate_count = 0  
if 'processed' not in st.session_state:  
    st.session_state.processed = False  
if 'manual_selections' not in st.session_state:  
    st.session_state.manual_selections = {}  
if 'candidates_cache' not in st.session_state:  
    st.session_state.candidates_cache = {}  
if 'search_query' not in st.session_state:  
    st.session_state.search_query = ""  

# ============================================  
# СПРАВОЧНИК ФЕДЕРАЛЬНЫХ ОКРУГОВ И РЕГИОНОВ  
# ============================================  
FEDERAL_DISTRICTS = {
    "Центральный федеральный округ": [
        "Белгородская область", "Брянская область", "Владимирская область",
        "Воронежская область", "Ивановская область", "Калужская область",
        "Костромская область", "Курская область", "Липецкая область",
        "Московская область", "Орловская область", "Рязанская область",
        "Смоленская область", "Тамбовская область", "Тверская область",
        "Тульская область", "Ярославская область", "Москва"
    ],
    "Южный федеральный округ": [
        "Республика Адыгея", "Республика Калмыкия", "Краснодарский край",
        "Астраханская область", "Волгоградская область", "Ростовская область"
    ],
    "Северо-Западный федеральный округ": [
        "Республика Карелия", "Республика Коми", "Архангельская область",
        "Вологодская область", "Калининградская область", "Ленинградская область",
        "Мурманская область", "Новгородская область", "Псковская область",
        "Санкт-Петербург", "Ненецкий автономный округ"
    ],
    "Дальневосточный федеральный округ": [
        "Республика Саха (Якутия)", "Камчатский край", "Приморский край",
        "Хабаровский край", "Амурская область", "Магаданская область",
        "Сахалинская область", "Еврейская автономная область", "Чукотский автономный округ"
    ],
    "Сибирский федеральный округ": [
        "Республика Алтай", "Республика Бурятия", "Республика Тыва",
        "Республика Хакасия", "Алтайский край", "Забайкальский край",
        "Красноярский край", "Иркутская область", "Кемеровская область",
        "Новосибирская область", "Омская область", "Томская область"
    ],
    "Уральский федеральный округ": [
        "Курганская область", "Свердловская область", "Тюменская область",
        "Челябинская область", "Ханты-Мансийский автономный округ — Югра",
        "Ямало-Ненецкий автономный округ"
    ],
    "Приволжский федеральный округ": [
        "Республика Башкортостан", "Республика Марий Эл", "Республика Мордовия",
        "Республика Татарстан", "Удмуртская Республика", "Чувашская Республика",
        "Кировская область", "Нижегородская область", "Оренбургская область",
        "Пензенская область", "Пермский край", "Самарская область",
        "Саратовская область", "Ульяновская область"
    ],
    "Северо-Кавказский федеральный округ": [
        "Республика Дагестан", "Республика Ингушетия", "Кабардино-Балкарская Республика",
        "Карачаево-Черкесская Республика", "Республика Северная Осетия — Алания",
        "Чеченская Республика", "Ставропольский край"
    ],
    "Крымский федеральный округ": [
        "Республика Крым", "Севастополь"
    ]
}

# ============================================  
# ФУНКЦИИ  
# ============================================  
@st.cache_data(ttl=3600)  
def get_hh_areas():  
    """Получает справочник HH.ru"""  
    response = requests.get('https://api.hh.ru/areas')  
    data = response.json()  
      
    areas_dict = {}  
      
    def parse_areas(areas, parent_name="", parent_id="", root_parent_id=""):  
        for area in areas:  
            area_id = area['id']  
            area_name = area['name']  
            
            # Определяем корневой parent_id (страну)
            current_root_id = root_parent_id if root_parent_id else parent_id if parent_id else area_id
              
            areas_dict[area_name] = {  
                'id': area_id,  
                'name': area_name,  
                'parent': parent_name,
                'parent_id': parent_id,
                'root_parent_id': current_root_id  # ID страны верхнего уровня
            }  
              
            if area.get('areas'):  
                parse_areas(area['areas'], area_name, area_id, current_root_id)  
      
    parse_areas(data)  
    return areas_dict  
 
def get_cities_by_regions(hh_areas, selected_regions):
    """Получает все города из выбранных регионов (только Россия)"""
    cities = []
    
    # Список исключений - что не выгружать
    excluded_names = ['Россия', 'Другие регионы', 'Другие страны']
    
    # ID России
    russia_id = '113'
    
    for city_name, city_info in hh_areas.items():
        parent = city_info['parent']
        root_parent_id = city_info.get('root_parent_id', '')
        
        # Пропускаем всё, что не относится к России
        if root_parent_id != russia_id:
            continue
        
        # Пропускаем исключенные названия
        if city_name in excluded_names:
            continue
        
        # Проверяем, входит ли город в выбранные регионы
        for region in selected_regions:
            # Нормализуем названия для сравнения
            region_normalized = region.lower().strip()
            parent_normalized = parent.lower().strip() if parent else ""
            city_normalized = city_name.lower().strip()
            
            # Проверяем различные варианты совпадений
            if (region_normalized in parent_normalized or 
                parent_normalized in region_normalized or
                region_normalized == parent_normalized or
                region_normalized == city_normalized):
                cities.append({
                    'Город': city_name,
                    'ID HH': city_info['id'],
                    'Регион': parent if parent else 'Россия'
                })
                break
    
    return pd.DataFrame(cities)
 
def get_all_cities(hh_areas):
    """Получает все города из справочника HH (только Россия)"""
    cities = []
    
    # Список исключений - что не выгружать
    excluded_names = ['Россия', 'Другие регионы', 'Другие страны']
    
    # ID России
    russia_id = '113'
    
    for city_name, city_info in hh_areas.items():
        parent = city_info['parent']
        root_parent_id = city_info.get('root_parent_id', '')
        
        # Пропускаем всё, что не относится к России
        if root_parent_id != russia_id:
            continue
        
        # Пропускаем исключенные названия
        if city_name in excluded_names:
            continue
        
        cities.append({
            'Город': city_name,
            'ID HH': city_info['id'],
            'Регион': parent if parent else 'Россия'
        })
    
    return pd.DataFrame(cities)

def normalize_region_name(text):  
    """Нормализует название региона для сравнения"""  
    text = text.lower()  
    replacements = {  
        'ленинградская': 'ленинград',  
        'московская': 'москов',  
        'курская': 'курск',  
        'кемеровская': 'кемеров',  
        'свердловская': 'свердлов',  
        'нижегородская': 'нижегород',  
        'новосибирская': 'новосибирск',  
        'тамбовская': 'тамбов',  
        'красноярская': 'красноярск',  
        'область': '',  
        'обл': '',  
        'край': '',  
        'республика': '',  
        'респ': '',  
        '  ': ' '  
    }  
    for old, new in replacements.items():  
        text = text.replace(old, new)  
    return text.strip()  

def extract_city_and_region(text):  
    """Извлекает название города и региона из текста"""  
    text_lower = text.lower()  
      
    region_keywords = [  
        'област', 'край', 'республик', 'округ',  
        'ленинград', 'москов', 'курск', 'кемеров',  
        'свердлов', 'нижегород', 'новосибирск', 'тамбов',  
        'красноярск'  
    ]  
      
    words = text.split()  
      
    if len(words) == 1:  
        return text, None  
      
    city_words = []  
    region_words = []  
    region_found = False  
      
    for word in words:  
        word_lower = word.lower()  
        if not region_found and any(keyword in word_lower for keyword in region_keywords):  
            region_found = True  
            region_words.append(word)  
        elif region_found:  
            region_words.append(word)  
        else:  
            city_words.append(word)  
      
    city = ' '.join(city_words) if city_words else text  
    region = ' '.join(region_words) if region_words else None  
      
    return city, region  

def check_if_changed(original, matched):  
    """Проверяет, изменилось ли название города"""  
    if matched is None or matched == "❌ Нет совпадения":  
        return False  
      
    original_clean = original.strip()  
    matched_clean = matched.strip()  
      
    return original_clean != matched_clean  

def get_candidates_by_word(client_city, hh_city_names, limit=20):  
    """Получает кандидатов по совпадению начального слова"""  
    first_word = client_city.split()[0].lower().strip()  
      
    candidates = []  
    for city_name in hh_city_names:  
        city_lower = city_name.lower()  
        if first_word in city_lower:  
            score = fuzz.WRatio(client_city.lower(), city_lower)  
            candidates.append((city_name, score))  
      
    candidates.sort(key=lambda x: x[1], reverse=True)  
      
    return candidates[:limit]  

def smart_match_city(client_city, hh_city_names, hh_areas, threshold=85):  
    """Умное сопоставление города с сохранением кандидатов"""  
      
    city_part, region_part = extract_city_and_region(client_city)  
    city_part_lower = city_part.lower().strip()  
      
    word_candidates = get_candidates_by_word(client_city, hh_city_names)  
      
    if word_candidates and len(word_candidates) > 0 and word_candidates[0][1] >= threshold:  
        best_candidate = word_candidates[0]  
        return (best_candidate[0], best_candidate[1], 0), word_candidates  
      
    if not word_candidates or (word_candidates and word_candidates[0][1] < threshold):  
        return None, word_candidates  
      
    exact_matches = []  
    exact_matches_with_region = []  
      
    for hh_city_name in hh_city_names:  
        hh_city_base = hh_city_name.split('(')[0].strip().lower()  
          
        if city_part_lower == hh_city_base:  
            if region_part:  
                region_normalized = normalize_region_name(region_part)  
                hh_normalized = normalize_region_name(hh_city_name)  
                  
                if region_normalized in hh_normalized:  
                    exact_matches_with_region.append(hh_city_name)  
                else:  
                    exact_matches.append(hh_city_name)  
            else:  
                exact_matches.append(hh_city_name)  
      
    if exact_matches_with_region:  
        best_match = exact_matches_with_region[0]  
        score = fuzz.WRatio(client_city.lower(), best_match.lower())  
        return (best_match, score, 0), word_candidates  
    elif exact_matches:  
        best_match = exact_matches[0]  
        score = fuzz.WRatio(client_city.lower(), best_match.lower())  
        return (best_match, score, 0), word_candidates  
      
    candidates = process.extract(  
        client_city,  
        hh_city_names,  
        scorer=fuzz.WRatio,  
        limit=10  
    )  
      
    if not candidates:  
        return None, word_candidates  
      
    candidates = [c for c in candidates if c[1] >= threshold]  
      
    if not candidates:  
        return None, word_candidates  
      
    if len(candidates) == 1:  
        return candidates[0], word_candidates  
      
    best_match = None  
    best_score = 0  
      
    client_city_lower = client_city.lower()  
      
    for candidate_name, score, _ in candidates:  
        candidate_lower = candidate_name.lower()  
        adjusted_score = score  
          
        candidate_city = candidate_name.split('(')[0].strip().lower()  
          
        if city_part_lower == candidate_city:  
            adjusted_score += 50  
        elif city_part_lower in candidate_city:  
            adjusted_score += 30  
        elif candidate_city in city_part_lower:  
            adjusted_score += 20  
        else:  
            adjusted_score -= 30  
          
        if region_part:  
            region_normalized = normalize_region_name(region_part)  
            candidate_normalized = normalize_region_name(candidate_name)  
              
            if region_normalized in candidate_normalized:  
                adjusted_score += 40  
            elif '(' in candidate_name:  
                adjusted_score -= 25  
          
        len_diff = abs(len(candidate_city) - len(city_part_lower))  
        if len_diff > 3:  
            adjusted_score -= 20  
          
        if len(candidate_city) > len(city_part_lower) + 4:  
            adjusted_score -= 25  
          
        if len(candidate_name) > 15 and len(client_city) > 15:  
            adjusted_score += 5  
          
        region_keywords = ['област', 'край', 'республик', 'округ']  
        client_has_region = any(keyword in client_city_lower for keyword in region_keywords)  
        candidate_has_region = any(keyword in candidate_lower for keyword in region_keywords)  
          
        if client_has_region and candidate_has_region:  
            adjusted_score += 15  
        elif client_has_region and not candidate_has_region:  
            adjusted_score -= 15  
          
        if adjusted_score > best_score:  
            best_score = adjusted_score  
            best_match = (candidate_name, score, _)  
      
    return (best_match if best_match else candidates[0]), word_candidates  

def match_cities(client_cities, hh_areas, threshold=85):  
    """Сопоставляет города с сохранением кандидатов"""  
    results = []  
    hh_city_names = list(hh_areas.keys())  
      
    seen_original_cities = {}  
    seen_hh_cities = {}  
      
    duplicate_original_count = 0  
    duplicate_hh_count = 0  
      
    st.session_state.candidates_cache = {}  
      
    progress_bar = st.progress(0)  
    status_text = st.empty()  
      
    for idx, client_city in enumerate(client_cities):  
        progress = (idx + 1) / len(client_cities)  
        progress_bar.progress(progress)  
        status_text.text(f"Обработано {idx + 1} из {len(client_cities)} городов...")  
          
        if pd.isna(client_city) or str(client_city).strip() == "":  
            results.append({  
                'Исходное название': client_city,  
                'Итоговое гео': None,  
                'ID HH': None,  
                'Регион': None,  
                'Совпадение %': 0,  
                'Изменение': 'Нет',  
                'Статус': '❌ Пустое значение',  
                'row_id': idx  
            })  
            continue  
          
        client_city_original = str(client_city).strip()  
        client_city_normalized = client_city_original.lower().strip()  
          
        if client_city_normalized in seen_original_cities:  
            duplicate_original_count += 1  
            original_result = seen_original_cities[client_city_normalized]  
            results.append({  
                'Исходное название': client_city_original,  
                'Итоговое гео': original_result['Итоговое гео'],  
                'ID HH': original_result['ID HH'],  
                'Регион': original_result['Регион'],  
                'Совпадение %': original_result['Совпадение %'],  
                'Изменение': original_result['Изменение'],  
                'Статус': '🔄 Дубликат (исходное название)',  
                'row_id': idx  
            })  
            continue  
          
        match_result, candidates = smart_match_city(client_city_original, hh_city_names, hh_areas, threshold)  
          
        st.session_state.candidates_cache[idx] = candidates  
          
        if match_result:  
            matched_name = match_result[0]  
            score = match_result[1]  
            hh_info = hh_areas[matched_name]  
            hh_city_normalized = hh_info['name'].lower().strip()  
              
            is_changed = check_if_changed(client_city_original, hh_info['name'])  
            change_status = 'Да' if is_changed else 'Нет'  
              
            if hh_city_normalized in seen_hh_cities:  
                duplicate_hh_count += 1  
                city_result = {  
                    'Исходное название': client_city_original,  
                    'Итоговое гео': hh_info['name'],  
                    'ID HH': hh_info['id'],  
                    'Регион': hh_info['parent'],  
                    'Совпадение %': round(score, 1),  
                    'Изменение': change_status,  
                    'Статус': '🔄 Дубликат (результат HH)',  
                    'row_id': idx  
                }  
                results.append(city_result)  
                seen_original_cities[client_city_normalized] = city_result  
            else:  
                status = '✅ Точное' if score >= 95 else '⚠️ Похожее'  
                  
                city_result = {  
                    'Исходное название': client_city_original,  
                    'Итоговое гео': hh_info['name'],  
                    'ID HH': hh_info['id'],  
                    'Регион': hh_info['parent'],  
                    'Совпадение %': round(score, 1),  
                    'Изменение': change_status,  
                    'Статус': status,  
                    'row_id': idx  
                }  
                  
                results.append(city_result)  
                seen_original_cities[client_city_normalized] = city_result  
                seen_hh_cities[hh_city_normalized] = True  
        else:  
            city_result = {  
                'Исходное название': client_city_original,  
                'Итоговое гео': None,  
                'ID HH': None,  
                'Регион': None,  
                'Совпадение %': 0,  
                'Изменение': 'Нет',  
                'Статус': '❌ Не найдено',  
                'row_id': idx  
            }  
              
            results.append(city_result)  
            seen_original_cities[client_city_normalized] = city_result  
      
    progress_bar.empty()  
    status_text.empty()  
      
    total_duplicates = duplicate_original_count + duplicate_hh_count  
      
    return pd.DataFrame(results), duplicate_original_count, duplicate_hh_count, total_duplicates  

# ============================================  
# ИНТЕРФЕЙС  
# ============================================  
# Заголовок с анимированной землей  
st.markdown(  
    '<div class="title-container">'  
    '<span class="rotating-earth">🌍</span>'  
    '<span class="main-title">Синхронизатор гео HH.ru</span>'  
    '</div>',  
    unsafe_allow_html=True  
)  
st.markdown("---")  

# Загрузка справочника HH
try:  
    hh_areas = get_hh_areas()  
except Exception as e:  
    st.error(f"❌ Ошибка загрузки справочника: {str(e)}")  
    hh_areas = None  

# ============================================
# БЛОК: СИНХРОНИЗАТОР ГОРОДОВ
# ============================================
st.header("📤 Синхронизатор городов")

with st.sidebar:  
    st.header("⚙️ Настройки")  
    threshold = st.slider(  
        "Порог совпадения (%)",  
        min_value=50,  
        max_value=100,  
        value=85,  
        help="Минимальный процент совпадения"  
    )  
      
    st.markdown("---")  
      
    st.markdown("### 📖 Инструкция")  
    st.markdown("""  
    **Как использовать:**  
      
    1. **Загрузите файл** Excel или CSV с городами  
    2. Города должны быть в **первой колонке**  
    3. Нажмите **"🚀 Начать сопоставление"**  
    4. Проверьте результаты в таблице  
    5. Отредактируйте города с совпадением ≤ 90%  
    6. Скачайте итоговый файл  
      
    **Формат файла:**  
    - Без заголовков  
    - Один город на строку  
    - Можно указывать область/регион  
    """)  
      
    st.markdown("---")  
    st.markdown("### 📊 Статусы")  
    st.markdown("""  
    - ✅ **Точное** - совпадение ≥95%  
    - ⚠️ **Похожее** - совпадение ≥порога  
    - 🔄 **Дубликат** - повторы  
    - ❌ **Не найдено** - совпадение <порога  
    """)  

col1, col2 = st.columns([1, 1])  

with col1:  
    st.subheader("📤 Загрузка файла")  
    uploaded_file = st.file_uploader(  
        "Выберите файл с городами",  
        type=['xlsx', 'csv'],  
        help="Поддерживаются форматы: Excel (.xlsx) и CSV"  
    )  
      
    with st.expander("📋 Показать пример формата файла"):  
        example_df = pd.DataFrame({  
            '': ['Москва', 'Санкт-Петербург', 'Екатеринбург', 'Новосибирск']  
        })  
        st.dataframe(example_df, use_container_width=True, hide_index=True)  

with col2:  
    st.subheader("ℹ️ Информация")  
    if hh_areas:
        st.success(f"✅ Справочник HH загружен: **{len(hh_areas)}** городов")  

if uploaded_file is not None and hh_areas is not None:  
    st.markdown("---")  
      
    try:  
        if uploaded_file.name.endswith('.csv'):  
            df = pd.read_csv(uploaded_file, header=None)  
        else:  
            df = pd.read_excel(uploaded_file, header=None)  
          
        client_cities = df.iloc[:, 0].tolist()  
        st.info(f"📄 Загружено **{len(client_cities)}** городов из файла")  
          
        if st.button("🚀 Начать сопоставление", type="primary", use_container_width=True):  
            with st.spinner("Обрабатываю..."):  
                result_df, dup_original, dup_hh, total_dup = match_cities(client_cities, hh_areas, threshold)  
                st.session_state.result_df = result_df  
                st.session_state.dup_original = dup_original  
                st.session_state.dup_hh = dup_hh  
                st.session_state.total_dup = total_dup  
                st.session_state.processed = True  
                st.session_state.manual_selections = {}  
                st.session_state.search_query = ""  
          
        if st.session_state.processed and st.session_state.result_df is not None:  
            result_df = st.session_state.result_df.copy()  
            dup_original = st.session_state.dup_original  
            dup_hh = st.session_state.dup_hh  
            total_dup = st.session_state.total_dup  
              
            st.markdown("---")  
            st.subheader("📊 Результаты")  
              
            col1, col2, col3, col4, col5, col6 = st.columns(6)  
              
            total = len(result_df)  
            exact = len(result_df[result_df['Статус'] == '✅ Точное'])  
            similar = len(result_df[result_df['Статус'] == '⚠️ Похожее'])  
            duplicates = len(result_df[result_df['Статус'].str.contains('Дубликат', na=False)])  
            not_found = len(result_df[result_df['Статус'] == '❌ Не найдено'])  
              
            to_export = len(result_df[  
                (~result_df['Статус'].str.contains('Дубликат', na=False)) &   
                (result_df['Итоговое гео'].notna())  
            ])  
              
            col1.metric("Всего", total)  
            col2.metric("✅ Точных", exact)  
            col3.metric("⚠️ Похожих", similar)  
            col4.metric("🔄 Дубликатов", duplicates)  
            col5.metric("❌ Не найдено", not_found)  
            col6.metric("📤 К выгрузке", to_export)  
              
            if duplicates > 0:  
                st.warning(f"""  
                ⚠️ **Найдено {duplicates} дубликатов:**  
                - 🔄 По исходному названию: **{dup_original}**  
                - 🔄 По результату HH: **{dup_hh}**  
                """)  
              
            st.markdown("---")  
            st.subheader("📋 Таблица сопоставлений")  
              
            st.text_input(  
                "🔍 Поиск по таблице",  
                key="search_query",
                placeholder="Начните вводить название города...",  
                label_visibility="visible"  
            )  
              
            result_df['sort_priority'] = result_df.apply(  
                lambda row: 0 if row['Совпадение %'] == 0 else (1 if row['Изменение'] == 'Да' else 2),  
                axis=1  
            )  
              
            result_df_sorted = result_df.sort_values(  
                by=['sort_priority', 'Совпадение %'],  
                ascending=[True, True]  
            ).reset_index(drop=True)  
              
            if st.session_state.search_query and st.session_state.search_query.strip():  
                search_lower = st.session_state.search_query.lower().strip()  
                mask = result_df_sorted.apply(  
                    lambda row: (  
                        search_lower in str(row['Исходное название']).lower() or  
                        search_lower in str(row['Итоговое гео']).lower() or  
                        search_lower in str(row['Регион']).lower() or  
                        search_lower in str(row['Статус']).lower()  
                    ),  
                    axis=1  
                )  
                result_df_filtered = result_df_sorted[mask]  
                  
                if len(result_df_filtered) == 0:  
                    st.warning(f"По запросу **'{st.session_state.search_query}'** ничего не найдено")  
                else:  
                    st.info(f"Найдено совпадений: **{len(result_df_filtered)}** из {len(result_df_sorted)}")  
            else:  
                result_df_filtered = result_df_sorted  
              
            display_df = result_df_filtered.copy()  
            display_df = display_df.drop(['row_id', 'sort_priority'], axis=1, errors='ignore')  
              
            st.dataframe(display_df, use_container_width=True, height=400)  
              
            editable_rows = result_df_sorted[result_df_sorted['Совпадение %'] <= 90].copy()  
              
            if len(editable_rows) > 0:  
                st.markdown("---")  
                st.subheader("✏️ Редактирование городов с совпадением ≤ 90%")  
                st.info(f"Найдено **{len(editable_rows)}** городов, доступных для редактирования")  
                  
                for idx, row in editable_rows.iterrows():  
                    with st.container():  
                        col1, col2, col3, col4 = st.columns([2, 3, 1, 1])  
                          
                        with col1:  
                            st.markdown(f"**{row['Исходное название']}**")  
                          
                        with col2:  
                            row_id = row['row_id']  
                            candidates = st.session_state.candidates_cache.get(row_id, [])  
                              
                            if candidates:  
                                options = ["❌ Нет совпадения"] + [f"{c[0]} ({c[1]:.1f}%)" for c in candidates]  
                                  
                                current_value = row['Итоговое гео']  
                                  
                                if row_id in st.session_state.manual_selections:  
                                    selected_value = st.session_state.manual_selections[row_id]  
                                    if selected_value == "❌ Нет совпадения":  
                                        default_idx = 0  
                                    else:  
                                        default_idx = 0  
                                        for i, c in enumerate(candidates):  
                                            if c[0] == selected_value:  
                                                default_idx = i + 1  
                                                break  
                                else:  
                                    default_idx = 0  
                                    if current_value:  
                                        for i, c in enumerate(candidates):  
                                            if c[0] == current_value:  
                                                default_idx = i + 1  
                                                break  
                                  
                                selected = st.selectbox(  
                                    "Выберите город:",  
                                    options=options,  
                                    index=default_idx,  
                                    key=f"select_{row_id}",  
                                    label_visibility="collapsed"  
                                )  
                                  
                                if selected == "❌ Нет совпадения":  
                                    st.session_state.manual_selections[row_id] = "❌ Нет совпадения"  
                                else:  
                                    selected_city = selected.rsplit(' (', 1)[0]  
                                    st.session_state.manual_selections[row_id] = selected_city  
                            else:  
                                st.selectbox(  
                                    "Нет кандидатов",  
                                    options=["❌ Нет совпадения"],  
                                    index=0,  
                                    key=f"select_{row_id}",  
                                    label_visibility="collapsed",  
                                    disabled=True  
                                )  
                                st.session_state.manual_selections[row_id] = "❌ Нет совпадения"  
                          
                        with col3:  
                            st.text(f"{row['Совпадение %']}%")  
                          
                        with col4:  
                            st.text(row['Статус'])  
                          
                        st.markdown("<hr style='margin-top: 5px; margin-bottom: 5px;'>", unsafe_allow_html=True)  
                  
                if st.session_state.manual_selections:  
                    no_match_count = sum(1 for v in st.session_state.manual_selections.values() if v == "❌ Нет совпадения")  
                    changed_count = len(st.session_state.manual_selections) - no_match_count  
                      
                    st.success(f"✅ Внесено изменений: {changed_count} | ❌ Отмечено как 'Нет совпадения': {no_match_count}")  
              
            st.markdown("---")  
            st.subheader("💾 Скачать результаты")  
              
            col1, col2, col3 = st.columns(3)  
              
            final_result_df = result_df.copy()  
            if st.session_state.manual_selections:  
                for row_id, new_value in st.session_state.manual_selections.items():  
                    mask = final_result_df['row_id'] == row_id  
                      
                    if new_value == "❌ Нет совпадения":  
                        final_result_df.loc[mask, 'Итоговое гео'] = None  
                        final_result_df.loc[mask, 'ID HH'] = None  
                        final_result_df.loc[mask, 'Регион'] = None  
                        final_result_df.loc[mask, 'Совпадение %'] = 0  
                        final_result_df.loc[mask, 'Изменение'] = 'Нет'  
                        final_result_df.loc[mask, 'Статус'] = '❌ Не найдено'  
                    else:  
                        final_result_df.loc[mask, 'Итоговое гео'] = new_value  
                          
                        if new_value in hh_areas:  
                            final_result_df.loc[mask, 'ID HH'] = hh_areas[new_value]['id']  
                            final_result_df.loc[mask, 'Регион'] = hh_areas[new_value]['parent']  
                          
                        original = final_result_df.loc[mask, 'Исходное название'].values[0]  
                        final_result_df.loc[mask, 'Изменение'] = 'Да' if check_if_changed(original, new_value) else 'Нет'  
              
            with col1:  
                if st.session_state.manual_selections:  
                    unique_manual_df = final_result_df[~final_result_df['Статус'].str.contains('Дубликат', na=False)]  
                    publisher_manual_df = pd.DataFrame({'Итоговое гео': unique_manual_df['Итоговое гео']})  
                    publisher_manual_df = publisher_manual_df.dropna()  
                      
                    output_manual = io.BytesIO()  
                    with pd.ExcelWriter(output_manual, engine='openpyxl') as writer:  
                        publisher_manual_df.to_excel(writer, index=False, header=False, sheet_name='Гео')  
                    output_manual.seek(0)  
                      
                    manual_count = len(publisher_manual_df)  
                    total_cities = len(result_df)  
                    percentage = (manual_count / total_cities * 100) if total_cities > 0 else 0  
                      
                    st.download_button(  
                        label=f"✏️ С ручными изменениями\n{manual_count} ({percentage:.0f}%) из {total_cities}",  
                        data=output_manual,  
                        file_name=f"geo_manual_{uploaded_file.name.rsplit('.', 1)[0]}.xlsx",  
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  
                        use_container_width=True,  
                        type="primary",  
                        key='download_manual'  
                    )  
                else:  
                    st.button(  
                        "✏️ С ручными изменениями",   
                        use_container_width=True,   
                        disabled=True,   
                        help="Внесите изменения в разделе 'Редактирование', чтобы скачать этот файл"  
                    )  
              
            with col2:  
                output = io.BytesIO()  
                export_df = final_result_df.drop(['row_id', 'sort_priority'], axis=1, errors='ignore')  
                with pd.ExcelWriter(output, engine='openpyxl') as writer:  
                    export_df.to_excel(writer, index=False, sheet_name='Результат')  
                output.seek(0)  
                  
                st.download_button(  
                    label="📥 Скачать полный отчет",  
                    data=output,  
                    file_name=f"result_{uploaded_file.name.rsplit('.', 1)[0]}.xlsx",  
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  
                    use_container_width=True,  
                    key='download_full'  
                )  
              
            with col3:  
                unique_df = final_result_df[~final_result_df['Статус'].str.contains('Дубликат', na=False)]  
                publisher_df = pd.DataFrame({'Итоговое гео': unique_df['Итоговое гео']})  
                publisher_df = publisher_df.dropna()  
                  
                output_publisher = io.BytesIO()  
                with pd.ExcelWriter(output_publisher, engine='openpyxl') as writer:  
                    publisher_df.to_excel(writer, index=False, header=False, sheet_name='Гео')  
                output_publisher.seek(0)  
                  
                unique_count = len(publisher_df)  
                  
                st.download_button(  
                    label=f"📤 Файл для публикатора ({unique_count})",  
                    data=output_publisher,  
                    file_name=f"geo_for_publisher_{uploaded_file.name.rsplit('.', 1)[0]}.xlsx",  
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  
                    use_container_width=True,  
                    key='download_publisher'  
                )  
      
    except Exception as e:  
        st.error(f"❌ Ошибка обработки файла: {str(e)}")  
        import traceback  
        st.code(traceback.format_exc())  

st.markdown("---")

# ============================================
# БЛОК: ВЫБОР РЕГИОНОВ
# ============================================
st.header("🗺️ Выбор регионов")
st.markdown("Выберите федеральные округа и области для получения списка всех городов")

if hh_areas is not None:
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("Федеральные округа")
        selected_districts = st.multiselect(
            "Выберите федеральные округа:",
            options=list(FEDERAL_DISTRICTS.keys()),
            help="Можно выбрать несколько округов",
            key="districts_select"
        )
    
    # Формируем список доступных регионов на основе выбранных округов
    available_regions = []
    if selected_districts:
        for district in selected_districts:
            available_regions.extend(FEDERAL_DISTRICTS[district])
    else:
        # Если округа не выбраны, показываем все регионы
        for regions in FEDERAL_DISTRICTS.values():
            available_regions.extend(regions)
    
    with col2:
        st.subheader("Области/Регионы")
        selected_regions = st.multiselect(
            "Выберите области/регионы:",
            options=sorted(available_regions),
            help="Можно выбрать несколько регионов",
            key="regions_select"
        )
    
    # Определяем, какие регионы использовать для поиска
    regions_to_search = []
    
    # Если выбраны конкретные регионы, используем их
    if selected_regions:
        regions_to_search = selected_regions
    # Если выбраны только округа (без конкретных регионов), берем все регионы из этих округов
    elif selected_districts:
        for district in selected_districts:
            regions_to_search.extend(FEDERAL_DISTRICTS[district])
    
    # Кнопки действий
    col_btn1, col_btn2 = st.columns(2)
    
    with col_btn1:
        # Показываем кнопку только если что-то выбрано
        if regions_to_search:
            # Информация о выборе
            if selected_regions:
                st.info(f"📍 Выбрано регионов: **{len(selected_regions)}**")
            elif selected_districts:
                st.info(f"📍 Выбрано округов: **{len(selected_districts)}** (включает {len(regions_to_search)} регионов)")
            
            if st.button("🔍 Получить список городов по регионам", type="primary", use_container_width=True):
                with st.spinner("Формирую список городов..."):
                    cities_df = get_cities_by_regions(hh_areas, regions_to_search)
                    
                    if not cities_df.empty:
                        st.success(f"✅ Найдено **{len(cities_df)}** городов в выбранных регионах")
                        
                        # Показываем таблицу
                        st.dataframe(cities_df, use_container_width=True, height=400)
                        
                        # Кнопки для скачивания
                        col1, col2 = st.columns(2)
                        
                        with col1:
                            # Полный отчет
                            output_full = io.BytesIO()
                            with pd.ExcelWriter(output_full, engine='openpyxl') as writer:
                                cities_df.to_excel(writer, index=False, sheet_name='Города')
                            output_full.seek(0)
                            
                            st.download_button(
                                label=f"📥 Скачать полный отчет ({len(cities_df)} городов)",
                                data=output_full,
                                file_name="cities_full_report.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                use_container_width=True,
                                key="download_regions_full"
                            )
                        
                        with col2:
                            # Только названия городов для публикатора
                            publisher_df = pd.DataFrame({'Город': cities_df['Город']})
                            output_publisher = io.BytesIO()
                            with pd.ExcelWriter(output_publisher, engine='openpyxl') as writer:
                                publisher_df.to_excel(writer, index=False, header=False, sheet_name='Гео')
                            output_publisher.seek(0)
                            
                            st.download_button(
                                label=f"📤 Для публикатора ({len(cities_df)} городов)",
                                data=output_publisher,
                                file_name="cities_for_publisher.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                use_container_width=True,
                                key="download_regions_publisher"
                            )
                    else:
                        st.warning("⚠️ Города не найдены в выбранных регионах")
        else:
            st.info("👆 Выберите федеральные округа или конкретные регионы для получения списка городов")
    
    with col_btn2:
        # Кнопка для выгрузки всех городов
        if st.button("🌍 Выгрузить ВСЕ города из справочника", type="secondary", use_container_width=True):
            with st.spinner("Формирую полный список городов..."):
                all_cities_df = get_all_cities(hh_areas)
                
                if not all_cities_df.empty:
                    st.success(f"✅ Найдено **{len(all_cities_df)}** городов в справочнике HH.ru")
                    
                    # Показываем таблицу
                    st.dataframe(all_cities_df, use_container_width=True, height=400)
                    
                    # Кнопки для скачивания
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        # Полный отчет
                        output_all_full = io.BytesIO()
                        with pd.ExcelWriter(output_all_full, engine='openpyxl') as writer:
                            all_cities_df.to_excel(writer, index=False, sheet_name='Города')
                        output_all_full.seek(0)
                        
                        st.download_button(
                            label=f"📥 Скачать полный отчет ({len(all_cities_df)} городов)",
                            data=output_all_full,
                            file_name="all_cities_full_report.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            use_container_width=True,
                            key="download_all_full"
                        )
                    
                    with col2:
                        # Только названия городов для публикатора
                        publisher_all_df = pd.DataFrame({'Город': all_cities_df['Город']})
                        output_all_publisher = io.BytesIO()
                        with pd.ExcelWriter(output_all_publisher, engine='openpyxl') as writer:
                            publisher_all_df.to_excel(writer, index=False, header=False, sheet_name='Гео')
                        output_all_publisher.seek(0)
                        
                        st.download_button(
                            label=f"📤 Для публикатора ({len(all_cities_df)} городов)",
                            data=output_all_publisher,
                            file_name="all_cities_for_publisher.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            use_container_width=True,
                            key="download_all_publisher"
                        )
                else:
                    st.warning("⚠️ Не удалось получить список городов")

st.markdown("---")  
st.markdown(  
    "Сделано с ❤️ | Данные из API HH.ru",  
    unsafe_allow_html=True  
)

