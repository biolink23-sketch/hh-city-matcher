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

def smart_match_city(client_city, hh_city_names, hh_areas, threshold=80):
    """
    Умное сопоставление города с учетом длины и контекста
    """
    # Получаем топ-5 кандидатов
    candidates = process.extract(
        client_city,
        hh_city_names,
        scorer=fuzz.WRatio,
        limit=5
    )
    
    if not candidates:
        return None
    
    # Фильтруем по порогу
    candidates = [c for c in candidates if c[1] >= threshold]
    
    if not candidates:
        return None
    
    # Если только один кандидат - возвращаем его
    if len(candidates) == 1:
        return candidates[0]
    
    # УМНАЯ ЛОГИКА: выбираем лучший вариант
    best_match = None
    best_score = 0
    
    client_city_lower = client_city.lower()
    
    for candidate_name, score, _ in candidates:
        candidate_lower = candidate_name.lower()
        
        # Бонусные баллы за:
        adjusted_score = score
        
        # 1. Более длинное совпадение (например, "Железногорск (Курская область)" лучше чем "Курск")
        if len(candidate_name) > 10 and len(client_city) > 10:
            adjusted_score += 5
        
        # 2. Точное вхождение основного слова
        client_words = set(client_city_lower.split())
        candidate_words = set(candidate_lower.replace('(', ' ').replace(')', ' ').split())
        
        # Если первое слово клиента есть в кандидате - это хорошо
        if client_words and candidate_words:
            first_word_client = list(client_words)[0] if len(list(client_words)[0]) > 3 else None
            if first_word_client and first_word_client in candidate_lower:
                adjusted_score += 10
        
        # 3. Проверка на "короткое совпадение" - штраф
        # Например, "Курск" не должен побеждать "Железногорск (Курская область)"
        if len(candidate_name) < len(client_city) * 0.6:
            adjusted_score -= 15
        
        # 4. Если в клиенте есть область/край, а в кандидате тоже - бонус
        region_keywords = ['област', 'край', 'республик', 'округ']
        client_has_region = any(keyword in client_city_lower for keyword in region_keywords)
        candidate_has_region = any(keyword in candidate_lower for keyword in region_keywords)
        
        if client_has_region and candidate_has_region:
            adjusted_score += 15
        
        if adjusted_score > best_score:
            best_score = adjusted_score
            best_match = (candidate_name, score, _)
    
    return best_match if best_match else candidates[0]

def match_cities(client_cities, hh_areas, threshold=80):
    """Сопоставляет города с двойной проверкой дубликатов"""
    results = []
    hh_city_names = list(hh_areas.keys())
    
    # Отслеживаем дубликаты по исходному названию
    seen_original_cities = {}
    # Отслеживаем дубликаты по результату HH
    seen_hh_cities = {}
    
    duplicate_original_count = 0
    duplicate_hh_count = 0
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    for idx, client_city in enumerate(client_cities):
        progress = (idx + 1) / len(client_cities)
        progress_bar.progress(progress)
        status_text.text(f"Обработано {idx + 1} из {len(client_cities)} городов...")
        
        # Обработка пустых значений
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
        
        client_city_original = str(client_city).strip()
        client_city_normalized = client_city_original.lower().strip()
        
        # ПРОВЕРКА 1: Дубликат по исходному названию
        if client_city_normalized in seen_original_cities:
            duplicate_original_count += 1
            original_result = seen_original_cities[client_city_normalized]
            results.append({
                'Исходное название': client_city_original,
                'Название HH': original_result['Название HH'],
                'ID HH': original_result['ID HH'],
                'Регион': original_result['Регион'],
                'Совпадение %': original_result['Совпадение %'],
                'Статус': '🔄 Дубликат (исходное название)'
            })
            continue
        
        # Умное сопоставление
        match = smart_match_city(client_city_original, hh_city_names, hh_areas, threshold)
        
        if match:
            matched_name = match[0]
            score = match[1]
            hh_info = hh_areas[matched_name]
            hh_city_normalized = hh_info['name'].lower().strip()
            
            # ПРОВЕРКА 2: Дубликат по результату HH
            if hh_city_normalized in seen_hh_cities:
                duplicate_hh_count += 1
                city_result = {
                    'Исходное название': client_city_original,
                    'Название HH': hh_info['name'],
                    'ID HH': hh_info['id'],
                    'Регион': hh_info['parent'],
                    'Совпадение %': round(score, 1),
                    'Статус': '🔄 Дубликат (результат HH)'
                }
                results.append(city_result)
                # Сохраняем в seen_original_cities для будущих проверок
                seen_original_cities[client_city_normalized] = city_result
            else:
                # Уникальный город
                status = '✅ Точное' if score >= 95 else '⚠️ Похожее'
                
                city_result = {
                    'Исходное название': client_city_original,
                    'Название HH': hh_info['name'],
                    'ID HH': hh_info['id'],
                    'Регион': hh_info['parent'],
                    'Совпадение %': round(score, 1),
                    'Статус': status
                }
                
                results.append(city_result)
                seen_original_cities[client_city_normalized] = city_result
                seen_hh_cities[hh_city_normalized] = True
        else:
            # Не найдено
            city_result = {
                'Исходное название': client_city_original,
                'Название HH': None,
                'ID HH': None,
                'Регион': None,
                'Совпадение %': 0,
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

# Боковая панель
with st.sidebar:
    st.header("⚙️ Настройки")
    threshold = st.slider(
        "Порог совпадения (%)",
        min_value=50,
        max_value=100,
        value=80,
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
    st.info("""
    💡 **Умный поиск:**
    
    Система учитывает:
    - Длину названия
    - Наличие области/края
    - Точность совпадения слов
    
    Пример: "Железногорск Курской области" → "Железногорск (Курская область)" ✅
    """)

# Основная область
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

# Обработка файла
if uploaded_file is not None and hh_areas is not None:
    st.markdown("---")
    
    try:
        # Чтение файла
        if uploaded_file.name.endswith('.csv'):
            df = pd.read_csv(uploaded_file)
        else:
            df = pd.read_excel(uploaded_file)
        
        client_cities = df.iloc[:, 0].tolist()
        st.info(f"📄 Загружено **{len(client_cities)}** городов из файла")
        
        # Кнопка обработки
        if st.button("🚀 Начать сопоставление", type="primary", use_container_width=True):
            with st.spinner("Обрабатываю..."):
                result_df, dup_original, dup_hh, total_dup = match_cities(client_cities, hh_areas, threshold)
                # Сохраняем в session_state
                st.session_state.result_df = result_df
                st.session_state.dup_original = dup_original
                st.session_state.dup_hh = dup_hh
                st.session_state.total_dup = total_dup
                st.session_state.processed = True
        
        # Показываем результаты, если они есть в session_state
        if st.session_state.processed and st.session_state.result_df is not None:
            result_df = st.session_state.result_df
            dup_original = st.session_state.dup_original
            dup_hh = st.session_state.dup_hh
            total_dup = st.session_state.total_dup
            
            # Статистика
            st.markdown("---")
            st.subheader("📊 Результаты")
            
            col1, col2, col3, col4, col5 = st.columns(5)
            
            total = len(result_df)
            exact = len(result_df[result_df['Статус'] == '✅ Точное'])
            similar = len(result_df[result_df['Статус'] == '⚠️ Похожее'])
            duplicates = len(result_df[result_df['Статус'].str.contains('Дубликат', na=False)])
            not_found = len(result_df[result_df['Статус'] == '❌ Не найдено'])
            
            col1.metric("Всего", total)
            col2.metric("✅ Точных", exact, f"{exact/total*100:.1f}%")
            col3.metric("⚠️ Похожих", similar, f"{similar/total*100:.1f}%")
            col4.metric("🔄 Дубликатов", duplicates, f"{duplicates/total*100:.1f}%")
            col5.metric("❌ Не найдено", not_found, f"{not_found/total*100:.1f}%")
            
            # Детальная информация о дубликатах
            if duplicates > 0:
                st.warning(f"""
                ⚠️ **Найдено {duplicates} дубликатов:**
                - 🔄 По исходному названию: **{dup_original}**
                - 🔄 По результату HH: **{dup_hh}**
                
                Все дубликаты будут исключены из файла для публикатора.
                """)
            
            # Таблица результатов
            st.markdown("---")
            st.subheader("📋 Таблица сопоставлений")
            
            # Фильтры
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
            
            # Применяем фильтры
            filtered_df = result_df[result_df['Статус'].isin(status_filter)]
            
            if search_term:
                filtered_df = filtered_df[
                    filtered_df['Исходное название'].str.contains(search_term, case=False, na=False) |
                    filtered_df['Название HH'].str.contains(search_term, case=False, na=False)
                ]
            
            # Показываем таблицу
            st.dataframe(
                filtered_df,
                use_container_width=True,
                height=400
            )
            
            # Скачивание результата
            st.markdown("---")
            st.subheader("💾 Скачать результаты")
            
            col1, col2 = st.columns(2)
            
            # Полный отчет Excel
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
            
            # Файл для публикатора (только уникальные гео БЕЗ заголовка)
            with col2:
                # Исключаем ВСЕ дубликаты (оба типа)
                unique_df = result_df[~result_df['Статус'].str.contains('Дубликат', na=False)]
                
                # Создаем DataFrame только с колонкой "Название HH"
                publisher_df = pd.DataFrame({
                    'Название HH': unique_df['Название HH']
                })
                
                # Удаляем строки с None (города, которые не найдены)
                publisher_df = publisher_df.dropna()
                
                output_publisher = io.BytesIO()
                with pd.ExcelWriter(output_publisher, engine='openpyxl') as writer:
                    # header=False убирает заголовок
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

# Футер
st.markdown("---")
st.markdown(
    "<div style='text-align: center; color: gray;'>Сделано с ❤️ | Данные из API HH.ru</div>",
    unsafe_allow_html=True
)
