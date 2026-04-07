# Referencia: Forecasting de Demanda

## Tabla de contenidos
1. [Clasificación ADI-CV² extendida](#1-clasificación-adi-cv²-extendida)
2. [Técnicas por tipo de demanda](#2-técnicas-por-tipo-de-demanda)
3. [Modelos avanzados y ML](#3-modelos-avanzados-y-ml)
4. [Métricas de evaluación](#4-métricas-de-evaluación)
5. [Segmentación para forecasting](#5-segmentación-para-forecasting)
6. [Outliers y limpieza de datos](#6-outliers-y-limpieza-de-datos)
7. [Operacionalización a escala](#7-operacionalización-a-escala)
8. [Aspectos críticos frecuentemente subestimados](#8-aspectos-críticos-frecuentemente-subestimados)
9. [Framework de decisión integrado](#9-framework-de-decisión-integrado)

---

## 1. Clasificación ADI-CV² extendida

### Esquema base (Syntetos-Boylan 2005)

**ADI** (Average Demand Interval): número total de periodos / periodos con demanda > 0. Un ADI cercano a 1 indica demanda en casi todos los periodos.

**CV²** (Squared Coefficient of Variation): varianza de demanda no-nula / (media de demanda no-nula)². Mide variabilidad del tamaño de la demanda cuando ocurre.

**Umbrales estándar**: ADI = 1.32, CV² = 0.49. Son ajustables por dominio — algunos autores proponen calibrarlos por validación cruzada sobre el catálogo propio.

### Extensiones recomendadas

**Estacionalidad**: test de autocorrelación en lag estacional (lag 12 mensual, lag 52 semanal) con umbral ACF > 0.3. O descomposición STL + test de Kruskal-Wallis sobre residuos agrupados por estación. Si hay estacionalidad significativa, los candidatos deben incluir modelos estacionales.

**Tendencia**: test de Mann-Kendall (p-value < 0.05). Productos con tendencia sostenida requieren modelos que la capturen (Holt, Holt-Winters, ARIMA con diferenciación).

**Ciclo de vida**:
- Nuevo (< 6 meses historia): analogía con productos similares o bayesiano
- Crecimiento: modelos que capturan tendencia
- Maduro: candidatos estándar según ADI-CV²
- Declive: monitorear tasa de caída; flag de posible descontinuación
- Inactivo (sin demanda en N periodos): excluir del forecasting activo; umbral de reactivación

**Demanda censurada** (`censored_pct`): ajusta el `quality_score`. Un SKU con 40% de periodos censurados tiene un quality_score degradado — sus métricas de forecast son menos confiables.

### Clasificación compuesta recomendada (guía técnica)

Un sistema robusto debe combinar: (1) ADI-CV² base, (2) flag de estacionalidad, (3) flag de tendencia, (4) etapa del ciclo de vida, (5) flag de producto inactivo. Esta clasificación compuesta permite reglas de decisión automatizadas y multidimensionales.

---

## 2. Técnicas por tipo de demanda

### Demanda smooth

La mayoría de modelos clásicos funcionan bien. Para sistemas automatizados:

- **SES** (Suavizado Exponencial Simple): base para demanda estacionaria sin tendencia ni estacionalidad. Parámetro α ∈ (0,1) — cerca de 1 da peso a lo reciente.
- **Holt** (doble suavizado): agrega componente de tendencia lineal (parámetros α y β).
- **Holt-Winters**: agrega estacionalidad (parámetro γ). Aditivo (amplitud constante) o multiplicativo (amplitud crece con nivel). Referencia para productos con estacionalidad clara.
- **ETS** (Error, Trend, Seasonality): framework que engloba todas las combinaciones de error/tendencia/estacionalidad. Selección automática por AIC. Equivalente a la familia de suavizado exponencial con marco estadístico formal.
- **ARIMA/AutoARIMA**: mayor flexibilidad para series con autocorrelación compleja. Más costoso computacionalmente. Selección automática de órdenes (p,d,q) por AIC/BIC.

### Demanda erratic

Demanda frecuente pero volúmenes muy variables. Modelos de smooth + limpieza de outliers robusta:
- Separar demanda base de picos promocionales
- Incorporar variables exógenas si están disponibles (precio, promoción, día de semana)
- Prophet de Meta: maneja bien estacionalidad compleja y efectos de feriados
- LightGBM/XGBoost: capturan no-linealidades con features temporales

### Demanda intermittent

Muchos periodos con demanda cero, tamaño relativamente constante cuando ocurre:

- **CrostonSBA** (Syntetos-Boylan Approximation): corrige el sesgo positivo de Croston original multiplicando por (1 - α/2). Punto de partida recomendado.
- **ADIDA** (Aggregate-Disaggregate Intermittent Demand Approach): agrega temporalmente, aplica modelo suavizado, desagrega.
- **TSB** (Teunter-Syntetos-Babai): estima directamente la probabilidad de demanda en cada periodo — decae gradualmente si pasan muchos periodos sin demanda. Útil para detectar obsolescencia gradual.
- **Modelos de conteo** (Poisson, Negative Binomial): modelan la probabilidad de observar k unidades. Con zero-inflation (ZIP, ZINB) manejan el exceso de ceros. Más ricos probabilísticamente.

### Demanda lumpy

Lo más difícil — esporádica Y con volúmenes muy variables:
- **SBA** como baseline razonable (intervalos de confianza amplios)
- **Willemain bootstrap**: genera distribución empírica de DDLT a partir de bootstrapping histórico. Muy útil para inventario.
- **Agregación temporal**: pasar de semanal a mensual transforma lumpy en erratic o intermittent.
- **Bayesiano con prior informativo**: combinar datos históricos escasos con conocimiento del dominio.

---

## 3. Modelos avanzados y ML

### Prophet (Meta)
Descomposición aditiva/multiplicativa con manejo automático de feriados, changepoints y estacionalidades múltiples. Fácil de usar, robusto a valores faltantes. Limitación: no captura dependencias autorregresivas — débil en series con fuerte autocorrelación de corto plazo.

### Gradient Boosting (XGBoost, LightGBM, CatBoost)
Reformula el forecast como regresión tabular. Features: lags de demanda, moving averages, desviaciones estándar en ventanas, indicadores de calendario (día semana, mes, trimestre), variables exógenas. Captura no-linealidades. Rápido de entrenar. No modela la estructura secuencial de forma nativa — depende de feature engineering.

### Redes neuronales para series temporales
- **N-BEATS**: bloques hacia atrás y adelante, no requiere features adicionales, competitivo en M4.
- **N-HiTS**: submuestreo jerárquico, mejor para horizontes largos.
- **TFT** (Temporal Fusion Transformer): incorpora variables estáticas (categoría, ubicación), variables futuras conocidas (feriados, promociones planificadas) y variables observadas. Interpretabilidad por attention weights.
- **DeepAR**: modelo autorregresivo sobre todas las series del catálogo. Pronosticos probabilísticos nativos. Muy útil cuando hay muchos SKUs con poca historia individual.

### Foundation models (emergentes, 2024-2026)
TimesFM (Google), Chronos (Amazon), Moirai, TimeGPT — preentrenados sobre grandes corpora de series temporales. Permiten zero-shot o fine-tuning mínimo. Prometedores para cold-start y baseline rápido.

---

## 4. Métricas de evaluación

| Métrica | Fórmula | Ventajas | Limitaciones |
|---------|---------|----------|-------------|
| MAE | mean(|real - forecast|) | Interpretable en unidades originales | No penaliza errores grandes |
| RMSE | √mean((real - forecast)²) | Penaliza errores grandes | Sensible a outliers en evaluación |
| MAPE | mean(|error|/|real|) × 100 | Comparable entre productos | Indefinido cuando real = 0 |
| WMAPE | Σ|errores| / Σ|reales| | Ponderado por volumen, maneja ceros | Sesgado hacia periodos de alta demanda |
| Bias | mean(forecast - real) / mean(real) | Detecta sobreestimación sistemática | No mide magnitud del error |
| MASE | MAE / MAE(naive estacional) | Scale-free, maneja ceros, comparable | Requiere calcular naive baseline |
| RMSSE | RMSE / RMSE(naive) | Penaliza errores grandes, scale-free | Idem |

**Para demanda intermitente**: MAPE es inutilizable. Usar **MASE** (Hyndman & Koehler 2006) — bien definido para todos los patrones, comparable entre series con escalas distintas.

**Naive baselines**:
- `seasonal naive`: replica último ciclo estacional → adecuado para series estacionales
- `lag-1 naive`: replica el último periodo → adecuado para smooth/erratic sin estacionalidad
- `mean naive`: promedio histórico → adecuado para intermittent/lumpy (demanda casi cero frecuente)

**fill_rate en backtest** (D38 resuelto): además del promedio, reportar `fill_rate_min` — el peor escenario de disponibilidad en las ventanas de backtest.

---

## 5. Segmentación para forecasting

### Matriz ABC-XYZ

| Segmento | Valor ABC | Predictibilidad XYZ | Estrategia |
|---------|-----------|---------------------|-----------|
| AX | Alto | Alta | Modelos sofisticados, revisión frecuente, monitoreo continuo |
| AY | Alto | Media | Modelos con exógenas, alerta de desviaciones |
| AZ | Alto | Baja | Modelos probabilísticos, escenarios, input de expertos |
| BX | Medio | Alta | Modelos automáticos estándar |
| BY | Medio | Media | Automatización con revisión periódica |
| BZ | Medio | Baja | Modelos simples con buffers de seguridad amplios |
| CX | Bajo | Alta | Automatización total, modelos simples |
| CY | Bajo | Media | Automatización total |
| CZ | Bajo | Baja | Considerar si vale la pena mantener el SKU |

### Segmentación por ciclo de vida y madurez

Nuevos (< 6 meses): necesitan analogía con productos similares o bayesiano con prior. Maduros: candidatos ideales para automatización. En declive: monitorear obsolescencia.

### Segmentación cruzada multidimensional

La más efectiva: cruzar patrón de demanda (smooth/erratic/intermittent/lumpy) × ABC × ciclo de vida, generando microsegmentos con reglas de decisión específicas para cada combinación.

---

## 6. Outliers y limpieza de datos

### Tipos de outliers en series de demanda

- **Outliers aditivos (AO)**: afectan un único punto. Picos o caídas aisladas. Ejemplo: error de registro, venta única improbable de repetirse.
- **Cambios de nivel (Level Shift, LS)**: el nivel medio cambia a partir de un punto. Ejemplo: nuevo cliente, pérdida de canal.
- **Cambios transitorios (TC)**: pico o caída que se disipa gradualmente. Ejemplo: efecto de una promoción.
- **Outliers innovativos (IO)**: se propagan a través de la estructura autorregresiva.

### Técnicas de detección

- **IQR**: [Q1 - 1.5·IQR, Q3 + 1.5·IQR]. Simple y rápido. No considera la estructura temporal.
- **Z-score**: |z| > 3 (o umbral elegido). Asume normalidad.
- **Descomposición STL + residuos**: respeta la estructura temporal; busca outliers en el componente residual.
- **Hampel filter**: medianas móviles + MAD. Robusto a lo largo de la serie.
- **Isolation Forest**: anomalías en el espacio de features. Útil con múltiples variables.

### Estrategias de tratamiento

- **Winsorización** (en planning_core): limitar valores extremos al percentil definido (ej. P95). Menos agresivo que eliminar.
- **Interpolación**: media o mediana de vecinos.
- **Imputación basada en modelo**: valor ajustado del modelo como reemplazo.
- **Mantención con flag**: preservar valor original pero marcarlo; modelos robustos reducen su influencia.

**Principio guía**: entender la causa antes de tratar. Si el outlier es una promoción → modelar explícitamente. Si es error de registro → corregir. Si es compra única improbable → atenuar.

---

## 7. Operacionalización a escala

### Arquitectura del pipeline de forecasting (según guía técnica)

1. Ingesta de datos (ERP, WMS, POS) con validación de calidad básica
2. Preprocesamiento: rellenar faltantes, alinear temporal, convertir unidades
3. Clasificación de demanda: ADI, CV², estacionalidad, tendencia
4. Detección y tratamiento de outliers
5. Selección y ajuste de modelos según clasificación
6. Generación de pronósticos con intervalos de confianza
7. Evaluación y monitoreo: alertas para SKUs con degradación
8. Almacenamiento y distribución a sistemas consumidores

### Estrategia de selección automática (horse-race)

El modelo horse-race es la estrategia recomendada para catálogos masivos. El horse-race puede ejecutarse con menor frecuencia que la generación de forecasts (mensual vs. diario/semanal) para reducir el costo computacional. En planning_core esto está implementado en `forecasting/selector.py` y materializado en `ForecastStore`.

### Paralelismo y frameworks

Miles de SKUs es un problema naturalmente paralelizable. En planning_core: `run_catalog_forecast()` acepta `n_jobs` para paralelismo. Frameworks especializados: **StatsForecast** (Nixtla) para modelos clásicos ultra-optimizados en C/Numba, **MLForecast** para ML con feature engineering automático.

### Ejecución recurrente

El pipeline en producción se ejecuta periódicamente (diaria/semanal/mensual según granularidad). Incluye: actualización de datos, re-evaluación de clasificación (menos frecuente), re-ajuste de modelos (periódico o disparado por degradación), generación de pronósticos, cálculo de métricas, generación de alertas, almacenamiento.

---

## 8. Aspectos críticos frecuentemente subestimados

### Calidad de datos como primer gate (guía técnica §9.1)

La mayoría de errores de forecast reportados en producción son errores de datos, no de modelos. El pipeline debe incluir un módulo de calidad que detecte: registros duplicados, cambios silenciosos de unidad de medida, series con gaps inexplicables (que no corresponden a quiebre de stock), cambios bruscos por migración de sistemas, códigos de producto reasignados.

Recomendación: cada SKU debe tener un **data quality score** antes del análisis. SKUs con score bajo → flujo de limpieza específico o revisión manual.

### Demanda censurada / Lost Sales (guía técnica §9.2)

Cuando hay quiebre de stock, la demanda observada es cero pero la demanda real no lo era. Entrenar modelos con estos ceros genera subestimación sistemática — ciclo vicioso donde el quiebre se perpetúa.

Solución: cruzar series de demanda con disponibilidad de inventario. Periodos donde stock ≤ umbral → marcar como censurados. Luego: excluir esos periodos del entrenamiento (estrategia pragmática), imputar demanda latente con promedio de periodos equivalentes con stock, o usar modelos de censura (Tobit).

En catalogos con CSL < 95%, más del 30% de series pueden estar afectadas por censura. Este efecto no se corrige con modelos más sofisticados — el problema está en los datos.

En planning_core: `preprocessing.py::mark_censored_demand()` implementa la detección; `classification/core.py` y `pipelines/classification.py` propagan el `censored_pct` al quality_score.

### Reconciliación jerárquica (guía técnica §9.3)

Cuando se hace forecast a múltiples niveles (SKU, categoría, ubicación, total empresa), los forecasts individuales pueden sumar más o menos que el forecast del nivel superior. Técnicas de reconciliación: Bottom-Up, Top-Down, Middle-Out, MinT (Minimum Trace reconciliation). En planning_core el modelo es por SKU × granularidad sin reconciliación jerárquica — oportunidad de mejora.

### Cold Start (guía técnica §9.4)

Productos nuevos con < 6 meses de historia no tienen suficiente señal para backtest. Opciones: analogía con productos similares (mismo proveedor, misma categoría), prior bayesiano informativo, o simplemente naive con buffer de SS amplio hasta acumular historia.

### Concept drift y monitoreo

Los patrones de demanda cambian. Un modelo excelente hoy puede degradarse silenciosamente en 3 meses. Señales de alerta: MASE del último periodo > 1.5× MASE de selección; Bias que crece monotónicamente. El sistema debe tener un monitor que re-dispare el horse-race cuando se detecta degradación significativa.

---

## 9. Framework de decisión integrado

| Paso | Acción | Resultado |
|------|--------|----------|
| 1 | Calcular ADI y CV² de la serie | Categoría base: Smooth, Erratic, Intermittent, Lumpy |
| 2 | Tests de estacionalidad y tendencia | Flags: estacional (si/no), tendencia (tipo) |
| 3 | Evaluar longitud del histórico | Flag: suficiente (≥ 24 per.) / insuficiente |
| 4 | Clasificar ciclo de vida | Etapa: nuevo, crecimiento, maduro, declive, inactivo |
| 5 | Asignar segmento ABC-XYZ | Prioridad y nivel de sofisticación |
| 6 | Seleccionar modelos candidatos según reglas | Lista de modelos a competir |
| 7 | Ejecutar backtest con modelos candidatos | Métrica de error para cada modelo |
| 8 | Seleccionar mejor modelo | Modelo ganador + métrica asociada |
| 9 | Generar forecast y almacenar | Pronóstico + intervalos + metadata |
| 10 | Monitorear y re-evaluar periódicamente | Alertas de degradación, reclasificación |

### Mapeo clasificación → modelos candidatos (según guía técnica §8.1)

| Clasificación | Modelos candidatos recomendados |
|---------------|--------------------------------|
| Smooth sin estacionalidad | SES, Holt (si tendencia), ETS auto, ARIMA auto |
| Smooth con estacionalidad | Holt-Winters, SARIMA, ETS auto, Prophet, STL+ETS |
| Erratic sin exógenas | ETS auto, ARIMA auto, Theta, mediana móvil robusta |
| Erratic con exógenas | Prophet, ARIMAX, XGBoost/LightGBM, TFT |
| Intermittent | SBA (Croston corregido), TSB, modelos de conteo (Poisson/NB) |
| Lumpy | SBA, Willemain bootstrap, ADIDA (agregación temporal), bayesiano |
| Nuevo producto (< 6 meses) | Analogía, bayesiano con prior informativo, media de cluster similar |
| Producto inactivo | No forecasting activo; monitoreo de reactivación |
