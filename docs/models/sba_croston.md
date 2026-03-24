# SBA / Croston / TSB

**Librería**: `StatsForecast` (Nixtla) — clases `CrostonOptimized`, `CrostonSBA`, `ADIDA`
**Fase**: 2.2

---

## ¿Qué son?

Son modelos diseñados específicamente para demanda intermitente — donde muchos periodos tienen demanda cero y los periodos con demanda positiva son esporádicos.

Los modelos convencionales (ETS, ARIMA) fallan en este contexto porque tratan los ceros como "baja demanda" y no como "ausencia de demanda". El promedio resultante subestima la demanda cuando ocurre y es incapaz de predecir cuándo ocurrirá.

### Croston (1972) — el método base

Descompone el problema en dos subseries independientes:
- `z_t`: tamaño de la demanda cuando ocurre (solo periodos con demanda > 0)
- `q_t`: intervalo entre ocurrencias sucesivas (en periodos)

Cada subserie se pronostica con SES (suavizado exponencial simple). El forecast final es:

```
F = z_hat / q_hat
```

**Problema conocido**: Croston produce un sesgo positivo sistemático — el denominador (`q_hat`) converge más lento que el numerador, sobreestimando la demanda esperada.

### SBA — Syntetos-Boylan Approximation (2005)

Corrección del sesgo de Croston. Multiplica el resultado por un factor de debiasing:

```
F_SBA = (1 - alpha/2) * z_hat / q_hat
```

donde `alpha` es el parámetro de suavizado del intervalo. **Es el método recomendado por defecto** sobre Croston original.

### TSB — Teunter-Syntetos-Babai (2011)

A diferencia de Croston y SBA que modelan el intervalo entre demandas, TSB modela directamente la **probabilidad de demanda en cada periodo**. Esto le permite detectar cuando un producto se está volviendo obsoleto (la probabilidad decae gradualmente).

---

## ¿Para qué tipo de demanda?

| Clasificación | Modelo recomendado |
|---|---|
| Intermittent (ADI ≥ 1.32, CV2 < 0.49) | **SBA** como baseline; TSB si hay sospecha de obsolescencia |
| Lumpy (ADI ≥ 1.32, CV2 ≥ 0.49) | **SBA** como baseline (intervalos amplios); Willemain bootstrap para planificación de inventario |
| Smooth / Erratic | No aplicar — mejor ETS |
| Inactive | No forecast |

En nuestro catálogo industrial (repuestos oleohidráulicos), los SKUs intermittent y lumpy son típicamente piezas de repuesto de baja rotación y alto costo unitario. Son los más críticos para el capital inmovilizado.

---

## ¿Qué se necesita para que funcione bien?

- Suficientes **eventos de demanda positiva**: mínimo 10-15 ocurrencias para que SES tenga historia suficiente en las dos subseries.
- Granularidad adecuada: si con granularidad mensual un SKU tiene solo 2-3 meses con demanda en 3 años, la serie es demasiado escasa — considerar pasar a granularidad anual o usar un modelo bayesiano.
- **No requiere** tratar los ceros ni imputar — los ceros son parte del patrón y Croston/SBA los maneja de forma nativa.

---

## Fallas conocidas y limitaciones

| Falla | Causa | Mitigación |
|---|---|---|
| Sesgo positivo (Croston) | Convergencia asimétrica de los dos SES | Usar SBA en lugar de Croston |
| No captura obsolescencia | Croston/SBA mantienen la estimación aunque no haya demanda reciente | Usar TSB, o monitorear con lifecycle = "declining/inactive" |
| Intervalos de confianza amplios en lumpy | Alta variabilidad de volumen → distribución de demanda muy dispersa | Para inventario, calcular distribución empírica de demanda acumulada con bootstrap |
| Estimación inestable con pocas ocurrencias | Alpha sobreajustado con 3-4 eventos | Fijar alpha = 0.1 como prior (no optimizar con tan pocos datos) |
| No modela estacionalidad | Croston/SBA no tienen componente estacional | Si el SKU intermittent muestra patrón estacional claro, considerar ADIDA (agregación temporal) |

---

## Parámetros clave

```python
from statsforecast.models import CrostonSBA, ADIDA

# SBA estándar
model_sba = CrostonSBA(
    alpha_d=None,   # None = optimizar automáticamente; rango típico 0.05-0.20
    alpha_p=None,   # alpha para el intervalo (puede ser igual a alpha_d)
)

# ADIDA: agrega temporalmente antes de aplicar Croston (reduce la intermitencia)
model_adida = ADIDA()
```

`StatsForecast` puede procesar miles de series intermittentes en paralelo con `CrostonSBA`.

---

## Output esperado

- Pronóstico puntual h períodos (tasa de demanda promedio esperada por periodo)
- **No produce intervalos de confianza nativos** — para distribuciones de incertidumbre, usar bootstrap sobre la serie histórica de intervalos y tamaños
- Alpha optimizados para z y q

---

## Relación con la clasificación ADI-CV2

```
ADI = 1.03, CV2 = 0.08  →  smooth  →  ETS
ADI = 1.89, CV2 = 0.15  →  intermittent  →  SBA
ADI = 2.40, CV2 = 0.75  →  lumpy  →  SBA (baseline), evaluar bootstrap
```

El clasificador del proyecto ya calcula ADI y CV2 para cada SKU — la selección de SBA vs. ETS se hace directamente con `sb_class`.
