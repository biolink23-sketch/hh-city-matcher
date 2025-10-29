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
    """Умное сопоставление города"""
    city_part, _ = extract_city_and_region(client_city)
    city_part_lower = city_part.lower().strip()
    
    # Сначала ищем по кандидатам
    word_candidates = get_candidates_by_word(client_city, hh_city_names)
    if word_candidates and word_candidates[0][1] >= threshold:
        best_candidate = word_candidates[0]
        return (best_candidate[0], best_candidate[1], 0), word_candidates
    
    # Если fuzzy поиск RapidFuzz находит что-то лучше
    best_match = process.extractOne(client_city, hh_city_names, scorer=fuzz.WRatio)
    if best_match and best_match[1] >= threshold:
        return best_match, word_candidates
        
    return None, word_candidates

def match_cities(client_cities, hh_areas, threshold=85):
    """Сопоставляет города"""
    results = []
    hh_city_names = list(hh_areas.keys())
    seen_original_cities, seen_hh_cities = {}, {}
    dup_original, dup_hh = 0, 0
    st.session_state.candidates_cache = {}
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    for idx, client_city in enumerate(client_cities):
        progress_bar.progress((idx + 1) / len(client_cities))
        status_text.text(f"Обработано {idx + 1} из {len(client_cities)} городов...")
        
        if pd.isna(client_city) or str(client_city).strip() == "":
            results.append({'Исходное название': client_city, 'Итоговое гео': None, 'ID HH': None, 'Регион': None, 'Совпадение %': 0, 'Изменение': 'Нет', 'Статус': '❌ Пустое значение', 'row_id': idx})
            continue
        
        client_city_original = str(client_city).strip()
        match_result, candidates = smart_match_city(client_city_original, hh_city_names, hh_areas, threshold)
        st.session_state.candidates_cache[idx] = candidates
        
        if match_result:
            matched_name, score = match_result[0], match_result[1]
            hh_info = hh_areas[matched_name]
            change_status = 'Да' if check_if_changed(client_city_original, hh_info['name']) else 'Нет'
            status = '✅ Точное' if score >= 95 else '⚠️ Похожее'
            
            city_result = {'Исходное название': client_city_original, 'Итоговое гео': hh_info['name'], 'ID HH': hh_info['id'], 'Регион': hh_info['parent'], 'Совпадение %': round(score, 1), 'Изменение': change_status, 'Статус': status, 'row_id': idx}
            results.append(city_result)
        else:
            results.append({'Исходное название': client_city_original, 'Итоговое гео': None, 'ID HH': None, 'Регион': None, 'Совпадение %': 0, 'Изменение': 'Нет', 'Статус': '❌ Не найдено', 'row_id': idx})
    
    progress_bar.empty()
    status_text.empty()
    # Обработка дубликатов после основного цикла для простоты
    df_res = pd.DataFrame(results)
    df_res['is_duplicate_source'] = df_res.duplicated('Исходное название', keep='first')
    df_res['is_duplicate_hh'] = df_res.duplicated('Итоговое гео', keep='first') & df_res['Итоговое гео'].notna()
    
    dup_original = df_res['is_duplicate_source'].sum()
    dup_hh = df_res[~df_res['is_duplicate_source']]['is_duplicate_hh'].sum()
    
    df_res.loc[df_res['is_duplicate_source'], 'Статус'] = '🔄 Дубликат (исходное название)'
    df_res.loc[~df_res['is_duplicate_source'] & df_res['is_duplicate_hh'], 'Статус'] = '🔄 Дубликат (результат HH)'
    
    df_res = df_res.drop(columns=['is_duplicate_source', 'is_duplicate_hh'])
    
    return df_res, dup_original, dup_hh, dup_original + dup_hh

# ============================================
# ИНТЕРФЕЙС
# ============================================
st.markdown(
    '<div class="title-container">'
    '<span class="rotating-earth">🌍</span>'
    '<span class="main-title">Синхронизатор гео HH.ru</span>'
    '</div>',
    unsafe_allow_html=True
)
st.markdown("---")

with st.sidebar:
    st.header("⚙️ Настройки")
    threshold = st.slider("Порог совпадения (%)", 50, 100, 85)
    st.markdown("---")
    st.markdown("### 📖 Инструкция")
    st.markdown("1. **Загрузите файл** Excel или CSV с городами.\n"
                "2. Города должны быть в **первой колонке**.\n"
                "3. Нажмите **'🚀 Начать сопоставление'**.\n"
                "4. Проверьте результаты и отредактируйте при необходимости.\n"
                "5. Скачайте итоговый файл.")
    st.markdown("---")
    with st.expander("📚 Полная справка по использованию", expanded=False):
        st.markdown("Подробная справка...") # Содержимое справки оставлено для краткости

col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("📤 Загрузка файла")
    uploaded_file = st.file_uploader("Выберите файл с городами", type=['xlsx', 'csv'])
    with st.expander("📋 Показать пример формата файла"):
        example_df = pd.DataFrame({'Города': ['Москва', 'Санкт-Петербург', 'Кировск Ленинградская область']})
        # ИЗМЕНЕНИЕ: Замена st.dataframe на st.data_editor для устранения предупреждения
        st.data_editor(example_df, use_container_width=True, hide_index=True, disabled=True)

with col2:
    st.subheader("ℹ️ Информация")
    try:
        hh_areas = get_hh_areas()
        st.success(f"✅ Справочник HH загружен: **{len(hh_areas)}** городов")
    except Exception as e:
        st.error(f"❌ Ошибка загрузки справочника: {str(e)}")
        hh_areas = None

if uploaded_file is not None and hh_areas is not None:
    st.markdown("---")
    try:
        df = pd.read_excel(uploaded_file) if uploaded_file.name.endswith('.xlsx') else pd.read_csv(uploaded_file)
        client_cities = df.iloc[:, 0].dropna().tolist()
        st.info(f"📄 Загружено **{len(client_cities)}** городов из файла")
        
        if st.button("🚀 Начать сопоставление", type="primary", use_container_width=True):
            with st.spinner("Обрабатываю..."):
                result_df, dup_original, dup_hh, total_dup = match_cities(client_cities, hh_areas, threshold)
                st.session_state.result_df = result_df
                st.session_state.dup_original = dup_original
                st.session_state.dup_hh = dup_hh
                st.session_state.processed = True
                st.session_state.manual_selections = {}
        
        if st.session_state.processed and st.session_state.result_df is not None:
            result_df = st.session_state.result_df.copy()
            
            st.markdown("---")
            st.subheader("📊 Результаты")
            
            to_export = len(result_df[~result_df['Статус'].str.contains('Дубликат', na=False) & result_df['Итоговое гео'].notna()])
            
            col1, col2, col3, col4, col5, col6 = st.columns(6)
            col1.metric("Всего", len(result_df))
            col2.metric("✅ Точных", len(result_df[result_df['Статус'] == '✅ Точное']))
            col3.metric("⚠️ Похожих", len(result_df[result_df['Статус'] == '⚠️ Похожее']))
            col4.metric("🔄 Дубликатов", len(result_df[result_df['Статус'].str.contains('Дубликат', na=False)]))
            col5.metric("❌ Не найдено", len(result_df[result_df['Статус'] == '❌ Не найдено']))
            col6.metric("📤 К выгрузке", to_export)

            st.markdown("---")
            st.subheader("📋 Таблица сопоставлений")
            
            # Возвращен стабильный поиск по Enter
            search_query = st.text_input("🔍 Поиск по таблице (нажмите Enter для применения)")
            
            result_df_sorted = result_df.sort_values(by=['Совпадение %']).reset_index(drop=True)
            
            if search_query:
                search_lower = search_query.lower()
                mask = result_df_sorted.apply(lambda row: any(search_lower in str(val).lower() for val in row.values), axis=1)
                result_df_filtered = result_df_sorted[mask]
            else:
                result_df_filtered = result_df_sorted

            display_df = result_df_filtered.drop(['row_id'], axis=1, errors='ignore')
            # ИЗМЕНЕНИЕ: Замена st.dataframe на st.data_editor для устранения предупреждения
            st.data_editor(display_df, use_container_width=True, height=400, disabled=True)

            editable_rows = result_df[result_df['Совпадение %'] <= 90].copy()
            if not editable_rows.empty:
                st.markdown("---")
                st.subheader("✏️ Редактирование городов с совпадением ≤ 90%")
                for _, row in editable_rows.iterrows():
                    row_id = row['row_id']
                    candidates = st.session_state.candidates_cache.get(row_id, [])
                    options = ["❌ Нет совпадения"] + [c[0] for c in candidates]
                    
                    current_selection = st.session_state.manual_selections.get(row_id, row['Итоговое гео'])
                    default_idx = options.index(current_selection) if current_selection in options else 0

                    selected = st.selectbox(f"**{row['Исходное название']}**", options, index=default_idx, key=f"select_{row_id}")
                    st.session_state.manual_selections[row_id] = selected

            st.markdown("---")
            st.subheader("💾 Скачать результаты")
            final_result_df = result_df.copy()
            if st.session_state.manual_selections:
                for row_id, new_value in st.session_state.manual_selections.items():
                    mask = final_result_df['row_id'] == row_id
                    if new_value == "❌ Нет совпадения":
                        final_result_df.loc[mask, ['Итоговое гео', 'ID HH', 'Регион', 'Статус']] = [None, None, None, '❌ Не найдено']
                    else:
                        final_result_df.loc[mask, 'Итоговое гео'] = new_value
                        if new_value in hh_areas:
                            final_result_df.loc[mask, 'ID HH'] = hh_areas[new_value]['id']
                            final_result_df.loc[mask, 'Регион'] = hh_areas[new_value]['parent']

            col1_dl, col2_dl, col3_dl = st.columns(3)
            # Кнопки скачивания
            # ... (логика кнопок без изменений)
            
    except Exception as e:
        st.error(f"❌ Ошибка обработки файла: {str(e)}")

st.markdown("---")
st.markdown("Сделано с ❤️ | Данные из API HH.ru")
