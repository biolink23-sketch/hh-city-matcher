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
    """Проверяет, изменилось ли название города - ПРОСТОЕ СРАВНЕНИЕ"""
    if matched is None:
        return False
    
    # Простое сравнение строк
    original_clean = original.strip()
    matched_clean = matched.strip()
    
    # Если строки идентичны - изменения нет
    # Если отличаются хоть чем-то - изменение есть
    return original_clean != matched_clean

def smart_match_city(client_city, hh_city_names, hh_areas, threshold=85):
    """Умное сопоставление города с приоритетом точного совпадения"""
    
    city_part, region_part = extract_city_and_region(client_city)
    city_part_lower = city_part.lower().strip()
    
    # ШАГ 1: ТОЧНЫЙ ПОИСК (приоритет)
    exact_matches = []
    exact_matches_with_region = []
    
    for hh_city_name in hh_city_names:
        hh_city_base = hh_city_name.split('(')[0].strip().lower()
        
        # Точное совпадение названия города
        if city_part_lower == hh_city_base:
            # Если указан регион - проверяем его тоже
            if region_part:
                region_normalized = normalize_region_name(region_part)
                hh_normalized = normalize_region_name(hh_city_name)
                
                if region_normalized in hh_normalized:
                    # Идеальное совпадение: и город, и регион
                    exact_matches_with_region.append(hh_city_name)
                else:
                    # Город совпадает, но регион другой
                    exact_matches.append(hh_city_name)
            else:
                # Город совпадает, регион не указан
                exact_matches.append(hh_city_name)
    
    # Приоритет: сначала с совпадающим регионом, потом без
    if exact_matches_with_region:
        best_match = exact_matches_with_region[0]
        score = fuzz.WRatio(client_city.lower(), best_match.lower())
        return (best_match, score, 0)
    elif exact_matches:
        best_match = exact_matches[0]
        score = fuzz.WRatio(client_city.lower(), best_match.lower())
        return (best_match, score, 0)
    
    # ШАГ 2: НЕЧЕТКИЙ ПОИСК (если точного не нашли)
    candidates = process.extract(
        client_city,
        hh_city_names,
        scorer=fuzz.WRatio,
        limit=10
    )
    
    if not candidates:
        return None
    
    candidates = [c for c in candidates if c[1] >= threshold]
    
    if not candidates:
        return None
    
    if len(candidates) == 1:
        return candidates[0]
    
    # ШАГ 3: УМНЫЙ ВЫБОР из кандидатов
    best_match = None
    best_score = 0
    
    client_city_lower = client_city.lower()
    
    for candidate_name, score, _ in candidates:
        candidate_lower = candidate_name.lower()
        adjusted_score = score
        
        candidate_city = candidate_name.split('(')[0].strip().lower()
        
        # КРИТЕРИЙ 1: Точное совпадение названия города
        if city_part_lower == candidate_city:
            adjusted_score += 50
        elif city_part_lower in candidate_city:
            adjusted_score += 30
        elif candidate_city in city_part_lower:
            adjusted_score += 20
        else:
            adjusted_score -= 30
        
        # КРИТЕРИЙ 2: Проверка региона
        if region_part:
            region_normalized = normalize_region_name(region_part)
            candidate_normalized = normalize_region_name(candidate_name)
            
            if region_normalized in candidate_normalized:
                adjusted_score += 40
            elif '(' in candidate_name:
                adjusted_score -= 25
        
        # КРИТЕРИЙ 3: Защита от "похожих"
        len_diff = abs(len(candidate_city) - len(city_part_lower))
        if len_diff > 3:
            adjusted_score -= 20
        
        # КРИТЕРИЙ 4: Проверка на вхождение
        if len(candidate_city) > len(city_part_lower) + 4:
            adjusted_score -= 25
        
        # КРИТЕРИЙ 5: Бонус за длинные совпадения
        if len(candidate_name) > 15 and len(client_city) > 15:
            adjusted_score += 5
        
        # КРИТЕРИЙ 6: Совпадение области/края
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
    
    return best_match if best_match else candidates[0]

def match_cities(client_cities, hh_areas, threshold=85):
    """Сопоставляет города с двойной проверкой дубликатов"""
    results = []
    hh_city_names = list(hh_areas.keys())
    
    seen_original_cities = {}
    seen_hh_cities = {}
    
    duplicate_original_count = 0
    duplicate_hh_count = 0
    
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
                'Статус': '❌ Пустое значение'
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
                'Статус': '🔄 Дубликат (исходное название)'
            })
            continue
        
        match = smart_match_city(client_city_original, hh_city_names, hh_areas, threshold)
        
        if match:
            matched_name = match[0]
            score = match[1]
            hh_info = hh_areas[matched_name]
            hh_city_normalized = hh_info['name'].lower().strip()
            
            # ПРОСТАЯ ПРОВЕРКА: строки идентичны или нет
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
                    'Статус': '🔄 Дубликат (результат HH)'
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
                    'Статус': status
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
                'Статус': '❌ Не найдено'
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
    4. Скачайте результат
    """)
    
    st.markdown("---")
    st.markdown("### 📊 Статусы")
    st.markdown("""
    - ✅ **Точное** - совпадение ≥95%
    - ⚠️ **Похожее** - совпадение ≥порога
    - 🔄 **Дубликат (исходное название)** - повтор в загруженном файле
    - 🔄 **Дубликат (результат HH)** - разные названия → один город HH
    - ❌ **Не найдено** - совпадение <порога
    """)
    
    st.markdown("---")
    st.markdown("### 🔄 Изменение")
    st.markdown("""
    - **Да** - "Исходное название" ≠ "Название HH"
    - **Нет** - строки идентичны
    
    Примеры:
    - "Апрелевка" → "Апрелевка (Московская область)" = **Да** ✅
    - "Москва" → "Москва" = **Нет** ✅
    - "Питер" → "Санкт-Петербург" = **Да** ✅
    - "Балашиха" → "Балашиха (Московская область)" = **Да** ✅
    """)
    
    st.markdown("---")
    st.success("""
    ✨ **Умный поиск v2.3:**
    
    **Изменение = простое сравнение строк**
    - Если строки не идентичны → "Да"
    - Если идентичны → "Нет"
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
        
        if st.session_state.processed and st.session_state.result_df is not None:
            result_df = st.session_state.result_df
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
                
                Все дубликаты будут исключены из файла для публикатора.
                """)
            
            if changed > 0:
                st.info(f"""
                🔄 **Изменено названий: {changed}**
                
                Города с изменениями отображаются первыми в таблице.
                """)
            
            st.markdown("---")
            st.subheader("📋 Таблица сопоставлений")
            
            filter_col1, filter_col2 = st.columns(2)
            
            with filter_col1:
                status_filter = st.multiselect(
                    "Фильтр по статусу",
                    options=[
                        '✅ Точное', 
                        '⚠️ Похожее', 
                        '🔄 Дубликат (исходное название)',
                        '🔄 Дубликат (результат HH)',
                        '❌ Не найдено'
                    ],
                    default=[
                        '✅ Точное', 
                        '⚠️ Похожее', 
                        '🔄 Дубликат (исходное название)',
                        '🔄 Дубликат (результат HH)',
                        '❌ Не найдено'
                    ],
                    key='status_filter'
                )
            
            with filter_col2:
                search_term = st.text_input("🔍 Поиск по названию", "", key='search_input')
            
            filtered_df = result_df[result_df['Статус'].isin(status_filter)]
            
            if search_term:
                filtered_df = filtered_df[
                    filtered_df['Исходное название'].str.contains(search_term, case=False, na=False) |
                    filtered_df['Название HH'].str.contains(search_term, case=False, na=False)
                ]
            
            # Сортируем: сначала с изменениями (Да), потом по "Совпадение %" по возрастанию
            filtered_df['Изменение_sort'] = filtered_df['Изменение'].map({'Да': 0, 'Нет': 1})
            filtered_df = filtered_df.sort_values(
                by=['Изменение_sort', 'Совпадение %'], 
                ascending=[True, True]
            ).drop('Изменение_sort', axis=1).reset_index(drop=True)
            
            st.dataframe(
                filtered_df,
                use_container_width=True,
                height=400,
                column_config={
                    "Совпадение %": st.column_config.NumberColumn(
                        "Совпадение %",
                        help="Процент совпадения с базой HH",
                        format="%.1f"
                    ),
                    "ID HH": st.column_config.NumberColumn(
                        "ID HH",
                        help="ID города в базе HH"
                    ),
                    "Изменение": st.column_config.TextColumn(
                        "Изменение",
                        help="Было ли изменено название города"
                    )
                }
            )
            
            st.caption("💡 Нажмите на заголовок столбца для изменения сортировки. По умолчанию: сначала измененные города.")
            
            st.markdown("---")
            st.subheader("💾 Скачать результаты")
            
            col1, col2 = st.columns(2)
            
            with col1:
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    result_df.to_excel(writer, index=False, sheet_name='Результат')
                output.seek(0)
                
                st.download_button(
                    label="📥 Скачать полный отчет (Excel)",
                    data=output,
                    file_name=f"result_{uploaded_file.name.rsplit('.', 1)[0]}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                    key='download_full'
                )
            
            with col2:
                unique_df = result_df[~result_df['Статус'].str.contains('Дубликат', na=False)]
                publisher_df = pd.DataFrame({'Название HH': unique_df['Название HH']})
                publisher_df = publisher_df.dropna()
                
                output_publisher = io.BytesIO()
                with pd.ExcelWriter(output_publisher, engine='openpyxl') as writer:
                    publisher_df.to_excel(writer, index=False, header=False, sheet_name='Гео')
                output_publisher.seek(0)
                
                unique_count = len(publisher_df)
                
                st.download_button(
                    label=f"📤 Выгрузить готовый файл для публикатора ({unique_count} городов)",
                    data=output_publisher,
                    file_name=f"geo_for_publisher_{uploaded_file.name.rsplit('.', 1)[0]}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                    type="primary",
                    key='download_publisher'
                )
            
    except Exception as e:
        st.error(f"❌ Ошибка обработки файла: {str(e)}")

st.markdown("---")
st.markdown(
    "<div style='text-align: center; color: gray;'>Сделано с ❤️ | Данные из API HH.ru</div>",
    unsafe_allow_html=True
)
