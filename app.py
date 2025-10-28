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
    # Извлекаем первое слово из города
    first_word = client_city.split()[0].lower().strip()
    
    # Ищем все города, содержащие это слово
    candidates = []
    for city_name in hh_city_names:
        city_lower = city_name.lower()
        if first_word in city_lower:
            # Вычисляем процент совпадения для сортировки
            score = fuzz.WRatio(client_city.lower(), city_lower)
            candidates.append((city_name, score))
    
    # Сортируем по проценту совпадения
    candidates.sort(key=lambda x: x[1], reverse=True)
    
    # Ограничиваем количество
    return candidates[:limit]

def smart_match_city(client_city, hh_city_names, hh_areas, threshold=85):
    """Умное сопоставление города с сохранением кандидатов"""
    
    city_part, region_part = extract_city_and_region(client_city)
    city_part_lower = city_part.lower().strip()
    
    # Получаем кандидатов по совпадению слова
    word_candidates = get_candidates_by_word(client_city, hh_city_names)
    
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
                'Название HH': None,
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
                'Название HH': original_result['Название HH'],
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
                    'Название HH': hh_info['name'],
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
                    'Название HH': hh_info['name'],
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
                'Название HH': None,
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
st.title("🌍 Сопоставление городов с HH.ru")
st.markdown("---")

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
    1. Загрузите Excel или CSV
    2. Города в первой колонке
    3. Нажмите "Начать"
    4. Редактируйте города с совпадением < 86%
    5. Выберите "❌ Нет совпадения" если город не подходит
    6. Скачайте результат
    """)
    
    st.markdown("---")
    st.markdown("### 📊 Статусы")
    st.markdown("""
    - ✅ **Точное** - совпадение ≥95%
    - ⚠️ **Похожее** - совпадение ≥порога
    - 🔄 **Дубликат** - повторы
    - ❌ **Не найдено** - совпадение <порога
    """)
    
    st.markdown("---")
    st.success("""
    ✨ **Новое v4.1:**
    
    **Улучшенное редактирование:**
    - Опция "❌ Нет совпадения" - город не попадет в выгрузку
    - Поиск по начальному слову (все города с этим словом)
    - Сортировка кандидатов по релевантности
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
            'Город': ['Москва', 'Питер', 'Екатеринбург', 'Новосиб']
        })
        st.dataframe(example_df, use_container_width=True)

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
        if uploaded_file.name.endswith('.csv'):
            df = pd.read_csv(uploaded_file)
        else:
            df = pd.read_excel(uploaded_file)
        
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
            changed = len(result_df[result_df['Изменение'] == 'Да'])
            
            col1.metric("Всего", total)
            col2.metric("✅ Точных", exact, f"{exact/total*100:.1f}%")
            col3.metric("⚠️ Похожих", similar, f"{similar/total*100:.1f}%")
            col4.metric("🔄 Дубликатов", duplicates, f"{duplicates/total*100:.1f}%")
            col5.metric("❌ Не найдено", not_found, f"{not_found/total*100:.1f}%")
            col6.metric("🔄 Изменено", changed, f"{changed/total*100:.1f}%")
            
            if duplicates > 0:
                st.warning(f"""
                ⚠️ **Найдено {duplicates} дубликатов:**
                - 🔄 По исходному названию: **{dup_original}**
                - 🔄 По результату HH: **{dup_hh}**
                """)
            
            st.markdown("---")
            st.subheader("📋 Таблица сопоставлений")
            
            # Сортировка
            result_df['sort_priority'] = result_df.apply(
                lambda row: 0 if row['Совпадение %'] == 0 else (1 if row['Изменение'] == 'Да' else 2),
                axis=1
            )
            
            result_df_sorted = result_df.sort_values(
                by=['sort_priority', 'Совпадение %'],
                ascending=[True, True]
            ).reset_index(drop=True)
            
            # Показываем основную таблицу
            display_df = result_df_sorted.copy()
            display_df = display_df.drop(['row_id', 'sort_priority'], axis=1, errors='ignore')
            
            st.dataframe(display_df, use_container_width=True, height=400)
            
            # Раздел для редактирования городов с совпадением < 86%
            editable_rows = result_df_sorted[result_df_sorted['Совпадение %'] < 86].copy()
            
            if len(editable_rows) > 0:
                st.markdown("---")
                st.subheader("✏️ Редактирование городов с совпадением < 86%")
                st.info(f"Найдено **{len(editable_rows)}** городов, доступных для редактирования")
                
                # Создаем форму для редактирования
                for idx, row in editable_rows.iterrows():
                    with st.container():
                        col1, col2, col3, col4 = st.columns([2, 3, 1, 1])
                        
                        with col1:
                            st.markdown(f"**{row['Исходное название']}**")
                        
                        with col2:
                            # Получаем кандидатов
                            row_id = row['row_id']
                            candidates = st.session_state.candidates_cache.get(row_id, [])
                            
                            if candidates:
                                # Формируем список опций с опцией "Нет совпадения"
                                options = ["❌ Нет совпадения"] + [f"{c[0]} ({c[1]:.1f}%)" for c in candidates]
                                
                                # Текущее значение
                                current_value = row['Название HH']
                                
                                # Если есть ручной выбор, используем его
                                if row_id in st.session_state.manual_selections:
                                    selected_value = st.session_state.manual_selections[row_id]
                                    if selected_value == "❌ Нет совпадения":
                                        default_idx = 0
                                    else:
                                        # Ищем в списке кандидатов
                                        default_idx = 0
                                        for i, c in enumerate(candidates):
                                            if c[0] == selected_value:
                                                default_idx = i + 1  # +1 потому что первая опция "Нет совпадения"
                                                break
                                else:
                                    # Находим индекс текущего значения
                                    default_idx = 0
                                    if current_value:
                                        for i, c in enumerate(candidates):
                                            if c[0] == current_value:
                                                default_idx = i + 1
                                                break
                                
                                # Selectbox для выбора
                                selected = st.selectbox(
                                    "Выберите город:",
                                    options=options,
                                    index=default_idx,
                                    key=f"select_{row_id}",
                                    label_visibility="collapsed"
                                )
                                
                                # Сохраняем выбор
                                if selected == "❌ Нет совпадения":
                                    st.session_state.manual_selections[row_id] = "❌ Нет совпадения"
                                else:
                                    # Извлекаем название города без процента
                                    selected_city = selected.rsplit(' (', 1)[0]
                                    st.session_state.manual_selections[row_id] = selected_city
                            else:
                                # Если нет кандидатов, показываем только опцию "Нет совпадения"
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
                        
                        st.markdown("---")
                
                if st.session_state.manual_selections:
                    # Подсчитываем сколько городов отмечено как "Нет совпадения"
                    no_match_count = sum(1 for v in st.session_state.manual_selections.values() if v == "❌ Нет совпадения")
                    changed_count = len(st.session_state.manual_selections) - no_match_count
                    
                    st.success(f"✅ Внесено изменений: {changed_count} | ❌ Отмечено как 'Нет совпадения': {no_match_count}")
            
            st.markdown("---")
            st.subheader("💾 Скачать результаты")
            
            col1, col2, col3 = st.columns(3)
            
            # Применяем ручные изменения
            final_result_df = result_df.copy()
            if st.session_state.manual_selections:
                for row_id, new_value in st.session_state.manual_selections.items():
                    mask = final_result_df['row_id'] == row_id
                    
                    if new_value == "❌ Нет совпадения":
                        # Помечаем как не найденное
                        final_result_df.loc[mask, 'Название HH'] = None
                        final_result_df.loc[mask, 'ID HH'] = None
                        final_result_df.loc[mask, 'Регион'] = None
                        final_result_df.loc[mask, 'Совпадение %'] = 0
                        final_result_df.loc[mask, 'Изменение'] = 'Нет'
                        final_result_df.loc[mask, 'Статус'] = '❌ Не найдено'
                    else:
                        # Применяем выбранный город
                        final_result_df.loc[mask, 'Название HH'] = new_value
                        
                        if new_value in hh_areas:
                            final_result_df.loc[mask, 'ID HH'] = hh_areas[new_value]['id']
                            final_result_df.loc[mask, 'Регион'] = hh_areas[new_value]['parent']
                        
                        original = final_result_df.loc[mask, 'Исходное название'].values[0]
                        final_result_df.loc[mask, 'Изменение'] = 'Да' if check_if_changed(original, new_value) else 'Нет'
            
            # Полный отчет
            with col1:
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
            
            # Файл для публикатора (обычный)
            with col2:
                unique_df = final_result_df[~final_result_df['Статус'].str.contains('Дубликат', na=False)]
                # Убираем города с "Нет совпадения"
                publisher_df = pd.DataFrame({'Название HH': unique_df['Название HH']})
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
            
            # Файл с ручными изменениями
            with col3:
                if st.session_state.manual_selections:
                    unique_manual_df = final_result_df[~final_result_df['Статус'].str.contains('Дубликат', na=False)]
                    publisher_manual_df = pd.DataFrame({'Название HH': unique_manual_df['Название HH']})
                    publisher_manual_df = publisher_manual_df.dropna()
                    
                    output_manual = io.BytesIO()
                    with pd.ExcelWriter(output_manual, engine='openpyxl') as writer:
                        publisher_manual_df.to_excel(writer, index=False, header=False, sheet_name='Гео')
                    output_manual.seek(0)
                    
                    manual_count = len(publisher_manual_df)
                    changes_count = len(st.session_state.manual_selections)
                    
                    st.download_button(
                        label=f"✏️ С ручными изменениями ({changes_count} изм.)",
                        data=output_manual,
                        file_name=f"geo_manual_{uploaded_file.name.rsplit('.', 1)[0]}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True,
                        type="primary",
                        key='download_manual'
                    )
                else:
                    st.info("Нет ручных изменений")
    
    except Exception as e:
        st.error(f"❌ Ошибка обработки файла: {str(e)}")
        import traceback
        st.code(traceback.format_exc())

st.markdown("---")
st.markdown(
    "Сделано с ❤️ | Данные из API HH.ru | v4.1",
    unsafe_allow_html=True
)
