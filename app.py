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
# Добавляем отдельный ключ для самого виджета, чтобы не было конфликтов
if 'search_input_widget' not in st.session_state:
    st.session_state.search_input_widget = ""

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
    
    # Если есть кандидаты по слову с хорошим совпадением (>= 85%), используем первый
    if word_candidates and len(word_candidates) > 0 and word_candidates[0][1] >= threshold:
        best_candidate = word_candidates[0]
        return (best_candidate[0], best_candidate[1], 0), word_candidates
    
    # Если нет хороших кандидатов по слову, возвращаем None (не найдено)
    if not word_candidates or (word_candidates and word_candidates[0][1] < threshold):
        return None, word_candidates
    
    # Старая логика для точных совпадений
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
    
    # БАЗОВЫЕ ИНСТРУКЦИИ
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
    
    st.markdown("---")
    
    # ПОЛНАЯ СПРАВКА
    with st.expander("📚 Полная справка по использованию", expanded=False):
        st.markdown("""
        ### Что делает сервис?
        
        **Синхронизатор гео HH.ru** автоматически сопоставляет ваш список городов 
        со справочником HeadHunter и подготавливает файл для загрузки в публикатор вакансий.
        
        ---
        
        ### Как работает алгоритм?
        
        #### 1️⃣ Автоматическое сопоставление
        - Анализирует каждый город из вашего файла
        - Ищет совпадения по **первому слову** в названии города
        - Учитывает название города, область/регион, похожесть написания
        - **Порог совпадения 85%** - если ниже, город помечается как "Не найдено"
        
        **Пример:** для города "Кировск" найдет все города, содержащие "кировск":
        - Кировск (Ленинградская область)
        - Кировск (Мурманская область)
        - И т.д.
        
        #### 2️⃣ Типы совпадений
        - **✅ Точное** (≥95%) - город найден с высокой точностью
        - **⚠️ Похожее** (≥85%) - найдено похожее совпадение
        - **❌ Не найдено** (<85%) - требуется ручной выбор
        - **🔄 Дубликат** - город повторяется в списке
        
        #### 3️⃣ Ручное редактирование
        Для городов с совпадением **≤ 90%** доступно редактирование:
        - Выберите правильный вариант из списка
        - Список формируется по первому слову города
        - Если не подходит - выберите **"❌ Нет совпадения"**
        - Города с "Нет совпадения" **не попадут** в итоговый файл
        
        ---
        
        ### Подробная инструкция
        
        #### Шаг 1: Подготовка файла
        Создайте Excel (.xlsx) или CSV файл:
        - Города в **первой колонке**
        - **Без заголовков** (или заголовок будет обработан как город)
        - Один город на строку
        
        **Пример:**
        ```
        Москва
        Санкт-Петербург
        Екатеринбург
        Кировск Ленинградская область
        ```
        
        #### Шаг 2: Загрузка
        1. Нажмите **"Выберите файл с городами"**
        2. Загрузите Excel или CSV
        3. Нажмите **"🚀 Начать сопоставление"**
        4. Дождитесь завершения обработки
        
        #### Шаг 3: Проверка результатов
        Изучите таблицу:
        - **Исходное название** - ваш город из файла
        - **Итоговое гео** - найденный город в справочнике HH
        - **ID HH** - идентификатор города
        - **Регион** - область/край/республика
        - **Совпадение %** - процент совпадения
        - **Статус** - результат сопоставления
        
        Используйте **поиск** над таблицей для быстрого поиска городов.
        
        #### Шаг 4: Редактирование (если нужно)
        Если есть города с совпадением ≤ 90%:
        1. Прокрутите до раздела **"✏️ Редактирование городов"**
        2. Для каждого города выберите правильный вариант
        3. Если город не найден - выберите **"❌ Нет совпадения"**
        
        #### Шаг 5: Скачивание результата
        
        **✏️ С ручными изменениями** (рекомендуется)
        - Содержит все ваши правки
        - Готов для загрузки в публикатор
        - Формат: одна колонка без заголовка
        - **Используйте этот файл, если вносили изменения**
        
        **📥 Полный отчет**
        - Подробная таблица со всеми данными
        - Для анализа и архива
        
        **📤 Для публикатора**
        - Автоматический результат без ручных правок
        - Готов для загрузки в публикатор
        
        ---
        
        ### Особенности работы
        
        #### ✅ Что сервис делает хорошо:
        - Распознает разные написания (Питер → Санкт-Петербург)
        - Учитывает область/регион
        - Удаляет дубликаты автоматически
        - Находит города даже с опечатками
        - **Ищет по первому слову** - для "Кировск" покажет все варианты
        
        #### ⚠️ Когда нужна ручная проверка:
        - Город написан с сокращениями (СПб, Мск)
        - Есть несколько городов с похожими названиями
        - Совпадение ≤ 90%
        
        #### ❌ Что делать, если город не найден:
        1. Проверьте правильность написания
        2. Попробуйте добавить область/регион
        3. Выберите похожий вариант из списка
        4. Если города нет в справочнике HH - выберите "Нет совпадения"
        
        ---
        
        ### Частые вопросы
        
        **Q: Почему некоторые города не найдены?**  
        A: Возможно, город написан с ошибкой, сокращением, или его нет в справочнике HH.ru. 
        Используйте ручной выбор из списка кандидатов.
        
        **Q: Что означает "Дубликат"?**  
        A: Этот город уже встречался в вашем списке. Дубликаты автоматически 
        исключаются из итогового файла.
        
        **Q: Нужно ли редактировать города с высоким процентом?**  
        A: Города с совпадением >90% обычно определены правильно, но рекомендуется 
        просмотреть результаты в таблице.
        
        **Q: Какой файл скачивать для публикатора?**  
        A: Если вносили ручные изменения - скачивайте **"С ручными изменениями"**. 
        Если нет - используйте **"Для публикатора"**.
        
        **Q: Можно ли загрузить файл с заголовками?**  
        A: Да, но первая строка будет обработана как город. Лучше загружать 
        файл без заголовков.
        
        **Q: Почему в таблице один город, а в редактировании другой?**  
        A: Если процент совпадения < 85%, город помечается как "Не найдено". 
        Вы можете выбрать подходящий вариант из списка кандидатов.
        
        **Q: Как работает поиск в таблице?**  
        A: Начните вводить название города - таблица автоматически отфильтруется. 
        Поиск работает по всем колонкам (название, регион, статус).
        
        ---
        
        ### Технические детали
        
        - **Справочник**: Актуальные данные из API HH.ru
        - **Алгоритм**: Поиск по первому слову + нечеткое сравнение
        - **Порог совпадения**: По умолчанию 85% (настраивается)
        - **Формат выгрузки**: Excel (.xlsx), одна колонка без заголовка
        - **Обновление**: Справочник обновляется каждый час
        - **Поиск**: Работает в реальном времени по мере ввода
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
                st.session_state.search_input_widget = "" # Сбрасываем и виджет
        
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
            
            # Подсчет городов к выгрузке (все кроме не найденных и дубликатов)
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
            
            # ==========================================================
            # ИЗМЕНЕННЫЙ БЛОК ПОИСКА
            # ==========================================================
            # Используем callback on_change для надежного обновления состояния.
            # Это решает проблему, когда очистка поля не обновляла таблицу.
            def on_search_change():
                st.session_state.search_query = st.session_state.search_input_widget
            
            st.text_input(
                "🔍 Поиск по таблице",
                key="search_input_widget",
                on_change=on_search_change,
                placeholder="Начните вводить название города...",
                label_visibility="visible"
            )
            # ==========================================================
            
            # Сортировка
            result_df['sort_priority'] = result_df.apply(
                lambda row: 0 if row['Совпадение %'] == 0 else (1 if row['Изменение'] == 'Да' else 2),
                axis=1
            )
            
            result_df_sorted = result_df.sort_values(
                by=['sort_priority', 'Совпадение %'],
                ascending=[True, True]
            ).reset_index(drop=True)
            
            # Фильтрация по поисковому запросу
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
            
            # Показываем таблицу
            display_df = result_df_filtered.copy()
            display_df = display_df.drop(['row_id', 'sort_priority'], axis=1, errors='ignore')
            
            st.dataframe(display_df, use_container_width=True, height=400)
            
            # Раздел для редактирования городов с совпадением <= 90%
            editable_rows = result_df_sorted[result_df_sorted['Совпадение %'] <= 90].copy()
            
            if len(editable_rows) > 0:
                st.markdown("---")
                st.subheader("✏️ Редактирование городов с совпадением ≤ 90%")
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
                                current_value = row['Итоговое гео']
                                
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
                        
                        st.markdown("<hr style='margin-top: 5px; margin-bottom: 5px;'>", unsafe_allow_html=True)
                
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
                        final_result_df.loc[mask, 'Итоговое гео'] = None
                        final_result_df.loc[mask, 'ID HH'] = None
                        final_result_df.loc[mask, 'Регион'] = None
                        final_result_df.loc[mask, 'Совпадение %'] = 0
                        final_result_df.loc[mask, 'Изменение'] = 'Нет'
                        final_result_df.loc[mask, 'Статус'] = '❌ Не найдено'
                    else:
                        # Применяем выбранный город
                        final_result_df.loc[mask, 'Итоговое гео'] = new_value
                        
                        if new_value in hh_areas:
                            final_result_df.loc[mask, 'ID HH'] = hh_areas[new_value]['id']
                            final_result_df.loc[mask, 'Регион'] = hh_areas[new_value]['parent']
                        
                        original = final_result_df.loc[mask, 'Исходное название'].values[0]
                        final_result_df.loc[mask, 'Изменение'] = 'Да' if check_if_changed(original, new_value) else 'Нет'
            
            # Файл с ручными изменениями (первая кнопка)
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
            
            # Полный отчет
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
            
            # Файл для публикатора (обычный)
            with col3:
                unique_df = final_result_df[~final_result_df['Статус'].str.contains('Дубликат', na=False)]
                # Убираем города с "Нет совпадения"
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
st.markdown(
    "Сделано с ❤️ | Данные из API HH.ru",
    unsafe_allow_html=True
)
