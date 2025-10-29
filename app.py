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

# CSS для анимации земли и стилей заголовка
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
if 'processed' not in st.session_state:
    st.session_state.processed = False
if 'manual_selections' not in st.session_state:
    st.session_state.manual_selections = {}
if 'candidates_cache' not in st.session_state:
    st.session_state.candidates_cache = {}

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
        'ленинградская': 'ленинград', 'московская': 'москов', 'курская': 'курск',
        'кемеровская': 'кемеров', 'свердловская': 'свердлов', 'нижегородская': 'нижегород',
        'новосибирская': 'новосибирск', 'тамбовская': 'тамбов', 'красноярская': 'красноярск',
        'область': '', 'обл': '', 'край': '', 'республика': '', 'респ': '', '  ': ' '
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text.strip()

def extract_city_and_region(text):
    """Извлекает название города и региона из текста"""
    text_lower = text.lower()
    region_keywords = [
        'област', 'край', 'республик', 'округ', 'ленинград', 'москов', 'курск', 'кемеров',
        'свердлов', 'нижегород', 'новосибирск', 'тамбов', 'красноярск'
    ]
    words = text.split()
    if len(words) == 1:
        return text, None
    city_words, region_words = [], []
    region_found = False
    for word in words:
        if not region_found and any(keyword in word.lower() for keyword in region_keywords):
            region_found = True
        if region_found:
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
    return original.strip() != matched.strip()

def get_candidates_by_word(client_city, hh_city_names, limit=20):
    """Получает кандидатов по совпадению начального слова"""
    first_word = client_city.split()[0].lower().strip()
    candidates = []
    for city_name in hh_city_names:
        if first_word in city_name.lower():
            score = fuzz.WRatio(client_city.lower(), city_name.lower())
            candidates.append((city_name, score))
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[:limit]

def smart_match_city(client_city, hh_city_names, hh_areas, threshold=85):
    """Умное сопоставление города с сохранением кандидатов"""
    match_result, candidates = None, []
    word_candidates = get_candidates_by_word(client_city, hh_city_names)
    if word_candidates and word_candidates[0][1] >= threshold:
        match_result = (word_candidates[0][0], word_candidates[0][1], 0)
    return match_result, word_candidates

def match_cities(client_cities, hh_areas, threshold=85):
    """Сопоставляет города с сохранением кандидатов"""
    results = []
    hh_city_names = list(hh_areas.keys())
    seen_original_cities, seen_hh_cities = {}, {}
    duplicate_original_count, duplicate_hh_count = 0, 0
    st.session_state.candidates_cache = {}
    progress_bar = st.progress(0)
    status_text = st.empty()

    for idx, client_city in enumerate(client_cities):
        progress_bar.progress((idx + 1) / len(client_cities))
        status_text.text(f"Обработано {idx + 1} из {len(client_cities)} городов...")
        
        row_data = {'Исходное название': client_city, 'row_id': idx}
        if pd.isna(client_city) or str(client_city).strip() == "":
            results.append({**row_data, 'Итоговое гео': None, 'ID HH': None, 'Регион': None, 'Совпадение %': 0, 'Изменение': 'Нет', 'Статус': '❌ Пустое значение'})
            continue

        client_city_original = str(client_city).strip()
        client_city_normalized = client_city_original.lower().strip()

        if client_city_normalized in seen_original_cities:
            duplicate_original_count += 1
            original_result = seen_original_cities[client_city_normalized]
            results.append({**row_data, **original_result, 'Статус': '🔄 Дубликат (исходное название)'})
            continue

        match_result, candidates = smart_match_city(client_city_original, hh_city_names, hh_areas, threshold)
        st.session_state.candidates_cache[idx] = candidates

        if match_result:
            matched_name, score = match_result[0], match_result[1]
            hh_info = hh_areas[matched_name]
            hh_city_normalized = hh_info['name'].lower().strip()
            is_changed = check_if_changed(client_city_original, hh_info['name'])
            
            city_result = {
                'Итоговое гео': hh_info['name'], 'ID HH': hh_info['id'], 'Регион': hh_info['parent'],
                'Совпадение %': round(score, 1), 'Изменение': 'Да' if is_changed else 'Нет'
            }

            if hh_city_normalized in seen_hh_cities:
                duplicate_hh_count += 1
                results.append({**row_data, **city_result, 'Статус': '🔄 Дубликат (результат HH)'})
            else:
                status = '✅ Точное' if score >= 95 else '⚠️ Похожее'
                results.append({**row_data, **city_result, 'Статус': status})
                seen_hh_cities[hh_city_normalized] = True
            
            seen_original_cities[client_city_normalized] = city_result
        else:
            results.append({**row_data, 'Итоговое гео': None, 'ID HH': None, 'Регион': None, 'Совпадение %': 0, 'Изменение': 'Нет', 'Статус': '❌ Не найдено'})

    progress_bar.empty()
    status_text.empty()
    return pd.DataFrame(results), duplicate_original_count, duplicate_hh_count, duplicate_original_count + duplicate_hh_count

# ============================================
# ИНТЕРФЕЙС
# ============================================
st.markdown(
    '<div class="title-container"><span class="rotating-earth">🌍</span>'
    '<span class="main-title">Синхронизатор гео HH.ru</span></div>',
    unsafe_allow_html=True
)
st.markdown("---")

# ----- SIDEBAR -----
with st.sidebar:
    st.header("⚙️ Настройки")
    threshold = st.slider("Порог совпадения (%)", min_value=50, max_value=100, value=85, help="Минимальный процент совпадения для автоматического сопоставления.")
    st.markdown("---")
    st.markdown("### 📖 Инструкция")
    st.markdown("1. **Загрузите файл** Excel или CSV с городами.\n"
                "2. Города должны быть в **первой колонке**.\n"
                "3. Нажмите **'🚀 Начать сопоставление'**.\n"
                "4. Проверьте результаты и отредактируйте при необходимости.\n"
                "5. Скачайте итоговый файл.")
    st.markdown("---")
    st.markdown("### 📊 Статусы")
    st.markdown("- ✅ **Точное**: совпадение ≥95%\n"
                "- ⚠️ **Похожее**: совпадение ≥порога\n"
                "- 🔄 **Дубликат**: повторы\n"
                "- ❌ **Не найдено**: совпадение <порога")
    with st.expander("📚 Полная справка", expanded=False):
        st.markdown("Сервис автоматически сопоставляет ваш список городов со справочником HeadHunter. Для городов с совпадением ≤ 90% доступно ручное редактирование. Используйте поиск над таблицей (введите текст и нажмите Enter) для быстрой фильтрации.")

# ----- ОСНОВНАЯ ЧАСТЬ -----
col1, col2 = st.columns([1, 1])
with col1:
    st.subheader("📤 Загрузка файла")
    uploaded_file = st.file_uploader("Выберите файл с городами", type=['xlsx', 'csv'], help="Поддерживаются форматы: Excel (.xlsx) и CSV.")
    with st.expander("📋 Показать пример формата файла"):
        st.dataframe(pd.DataFrame({'Названия городов': ['Москва', 'Санкт-Петербург', 'Кировск Ленинградская область']}), use_container_width=True, hide_index=True)
with col2:
    st.subheader("ℹ️ Информация")
    try:
        hh_areas = get_hh_areas()
        st.success(f"✅ Справочник HH загружен: **{len(hh_areas)}** гео-объектов.")
    except Exception as e:
        st.error(f"❌ Ошибка загрузки справочника HH: {str(e)}")
        hh_areas = None

if uploaded_file is not None and hh_areas is not None:
    st.markdown("---")
    df = pd.read_excel(uploaded_file) if uploaded_file.name.endswith('.xlsx') else pd.read_csv(uploaded_file)
    client_cities = df.iloc[:, 0].dropna().unique().tolist()
    st.info(f"📄 Загружено **{len(df)}** строк (**{len(client_cities)}** уникальных городов) из файла.")
    
    if st.button("🚀 Начать сопоставление", type="primary", use_container_width=True):
        with st.spinner("Идет сопоставление... Это может занять некоторое время."):
            result_df, dup_orig, dup_hh, total_dup = match_cities(client_cities, hh_areas, threshold)
            st.session_state.result_df = result_df
            st.session_state.dup_original = dup_orig
            st.session_state.dup_hh = dup_hh
            st.session_state.processed = True
            st.session_state.manual_selections = {}

    if st.session_state.processed and st.session_state.result_df is not None:
        result_df = st.session_state.result_df.copy()
        
        st.markdown("---")
        st.subheader("📊 Результаты")
        total = len(result_df)
        to_export = len(result_df[~result_df['Статус'].str.contains('Дубликат', na=False) & result_df['Итоговое гео'].notna()])
        
        cols = st.columns(6)
        cols[0].metric("Всего", total)
        cols[1].metric("✅ Точных", len(result_df[result_df['Статус'] == '✅ Точное']))
        cols[2].metric("⚠️ Похожих", len(result_df[result_df['Статус'] == '⚠️ Похожее']))
        cols[3].metric("🔄 Дубликатов", len(result_df[result_df['Статус'].str.contains('Дубликат', na=False)]))
        cols[4].metric("❌ Не найдено", len(result_df[result_df['Статус'] == '❌ Не найдено']))
        cols[5].metric("📤 К выгрузке", to_export)

        st.markdown("---")
        st.subheader("📋 Таблица сопоставлений")

        # Простое и стабильное поле поиска (фильтрация по Enter)
        search_query = st.text_input(
            "🔍 Поиск по таблице (нажмите Enter для применения)",
            placeholder="Введите текст и нажмите Enter..."
        )
        
        result_df_sorted = result_df.sort_values(by='Совпадение %').reset_index(drop=True)
        
        # Логика фильтрации
        if search_query:
            search_lower = search_query.lower()
            mask = result_df_sorted.apply(lambda row: any(search_lower in str(val).lower() for val in row), axis=1)
            result_df_filtered = result_df_sorted[mask]
        else:
            result_df_filtered = result_df_sorted
        
        st.dataframe(result_df_filtered.drop(['row_id'], axis=1, errors='ignore'), use_container_width=True, height=400)
        
        editable_rows = result_df[result_df['Совпадение %'] <= 90].copy()
        if not editable_rows.empty:
            st.markdown("---")
            st.subheader("✏️ Редактирование городов (совпадение ≤ 90%)")
            for _, row in editable_rows.iterrows():
                row_id = row['row_id']
                default_val = st.session_state.manual_selections.get(row_id, row['Итоговое гео'])
                candidates = st.session_state.candidates_cache.get(row_id, [])
                options = ["❌ Нет совпадения"] + [c[0] for c in candidates]
                
                try:
                    default_idx = options.index(default_val) if default_val in options else 0
                except ValueError:
                    default_idx = 0
                
                selected = st.selectbox(f"**{row['Исходное название']}** (найдено: *{row['Итоговое гео']}* | {row['Совпадение %']}%)", options, index=default_idx, key=f"select_{row_id}")
                st.session_state.manual_selections[row_id] = selected

        st.markdown("---")
        st.subheader("💾 Скачать результаты")
        
        cols_dl = st.columns(3)
        final_result_df = result_df.copy()
        for row_id, new_value in st.session_state.manual_selections.items():
            mask = final_result_df['row_id'] == row_id
            if new_value == "❌ Нет совпадения":
                final_result_df.loc[mask, ['Итоговое гео', 'ID HH', 'Регион', 'Совпадение %', 'Статус']] = [None, None, None, 0, '❌ Не найдено']
            else:
                final_result_df.loc[mask, 'Итоговое гео'] = new_value
                if new_value in hh_areas:
                    final_result_df.loc[mask, 'ID HH'] = hh_areas[new_value]['id']
                    final_result_df.loc[mask, 'Регион'] = hh_areas[new_value]['parent']
        
        def to_excel(df: pd.DataFrame):
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df.to_excel(writer, index=False, header=False, sheet_name='Гео')
            return output.getvalue()

        if st.session_state.manual_selections:
            publisher_manual_df = final_result_df[~final_result_df['Статус'].str.contains('Дубликат', na=False) & final_result_df['Итоговое гео'].notna()][['Итоговое гео']]
            manual_count = len(publisher_manual_df)
            percentage = (manual_count / total * 100) if total > 0 else 0
            cols_dl[0].download_button(
                label=f"✏️ С ручными изменениями ({manual_count} | {percentage:.0f}%)",
                data=to_excel(publisher_manual_df),
                file_name=f"geo_manual_{uploaded_file.name.split('.')[0]}.xlsx",
                mime="application/vnd.ms-excel",
                use_container_width=True, type="primary"
            )
        
        full_report_df = final_result_df.drop(['row_id'], axis=1, errors='ignore')
        cols_dl[1].download_button(
            label="📥 Скачать полный отчет",
            data=full_report_df.to_csv(index=False).encode('utf-8'),
            file_name=f"result_{uploaded_file.name.split('.')[0]}.csv",
            mime="text/csv",
            use_container_width=True
        )

        publisher_df = final_result_df[~final_result_df['Статус'].str.contains('Дубликат', na=False) & final_result_df['Итоговое гео'].notna()][['Итоговое гео']]
        cols_dl[2].download_button(
            label=f"📤 Файл для публикатора ({len(publisher_df)})",
            data=to_excel(publisher_df),
            file_name=f"geo_for_publisher_{uploaded_file.name.split('.')[0]}.xlsx",
            mime="application/vnd.ms-excel",
            use_container_width=True
        )

st.markdown("---")
st.markdown("Сделано с ❤️ | Данные из API HH.ru")
