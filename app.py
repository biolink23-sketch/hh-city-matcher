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

def match_cities(client_cities, hh_areas, threshold=80):
    """Сопоставляет города с обработкой дубликатов"""
    results = []
    hh_city_names = list(hh_areas.keys())
    
    # Отслеживаем уже обработанные города (нормализованные)
    seen_cities = {}
    duplicate_count = 0
    
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
        
        # Нормализуем: убираем пробелы и приводим к нижнему регистру для сравнения
        client_city_original = str(client_city).strip()
        client_city_normalized = client_city_original.lower().strip()
        
        # Проверка на дубликат (сравниваем нормализованные версии)
        if client_city_normalized in seen_cities:
            duplicate_count += 1
            results.append({
                'Исходное название': client_city_original,
                'Название HH': seen_cities[client_city_normalized]['Название HH'],
                'ID HH': seen_cities[client_city_normalized]['ID HH'],
                'Регион': seen_cities[client_city_normalized]['Регион'],
                'Совпадение %': seen_cities[client_city_normalized]['Совпадение %'],
                'Статус': '🔄 Дубликат'
            })
            continue
        
        # Нечеткое сопоставление
        match = process.extractOne(
            client_city_original,
            hh_city_names,
            scorer=fuzz.WRatio,
            score_cutoff=threshold
        )
        
        if match:
            matched_name = match[0]
            score = match[1]
            hh_info = hh_areas[matched_name]
            
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
            seen_cities[client_city_normalized] = city_result
        else:
            city_result = {
                'Исходное название': client_city_original,
                'Название HH': None,
                'ID HH': None,
                'Регион': None,
                'Совпадение %': 0,
                'Статус': '❌ Не найдено'
            }
            
            results.append(city_result)
            seen_cities[client_city_normalized] = city_result
    
    progress_bar.empty()
    status_text.empty()
    
    return pd.DataFrame(results), duplicate_count

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
    - 🔄 **Дубликат** - повтор города
    - ❌ **Не найдено** - совпадение <порога
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
                result_df, duplicate_count = match_cities(client_cities, hh_areas, threshold)
                # Сохраняем в session_state
                st.session_state.result_df = result_df
                st.session_state.duplicate_count = duplicate_count
                st.session_state.processed = True
        
        # Показываем результаты, если они есть в session_state
        if st.session_state.processed and st.session_state.result_df is not None:
            result_df = st.session_state.result_df
            duplicate_count = st.session_state.duplicate_count
            
            # Статистика
            st.markdown("---")
            st.subheader("📊 Результаты")
            
            col1, col2, col3, col4, col5 = st.columns(5)
            
            total = len(result_df)
            exact = len(result_df[result_df['Статус'] == '✅ Точное'])
            similar = len(result_df[result_df['Статус'] == '⚠️ Похожее'])
            duplicates = len(result_df[result_df['Статус'] == '🔄 Дубликат'])
            not_found = len(result_df[result_df['Статус'] == '❌ Не найдено'])
            
            col1.metric("Всего", total)
            col2.metric("✅ Точных", exact, f"{exact/total*100:.1f}%")
            col3.metric("⚠️ Похожих", similar, f"{similar/total*100:.1f}%")
            col4.metric("🔄 Дубликатов", duplicates, f"{duplicates/total*100:.1f}%")
            col5.metric("❌ Не найдено", not_found, f"{not_found/total*100:.1f}%")
            
            # Информация о дубликатах
            if duplicates > 0:
                st.info(f"ℹ️ Найдено и помечено **{duplicates}** дубликатов. Они будут исключены из файла для публикатора.")
            
            # Таблица результатов
            st.markdown("---")
            st.subheader("📋 Таблица сопоставлений")
            
            # Фильтры
            filter_col1, filter_col2 = st.columns(2)
            
            with filter_col1:
                status_filter = st.multiselect(
                    "Фильтр по статусу",
                    options=['✅ Точное', '⚠️ Похожее', '🔄 Дубликат', '❌ Не найдено'],
                    default=['✅ Точное', '⚠️ Похожее', '🔄 Дубликат', '❌ Не найдено'],
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
                # Исключаем дубликаты - берем только НЕ дубликаты
                unique_df = result_df[result_df['Статус'] != '🔄 Дубликат']
                
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
