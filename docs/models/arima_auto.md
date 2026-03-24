# ARIMA / SARIMA Automático

**Librería**: `StatsForecast` (Nixtla) — clase `AutoARIMA`
**Fase**: 3.1

---

## ¿Qué es?

ARIMA (AutoRegressive Integrated Moving Average) modela la serie temporal como combinación de:
- **AR (p)**: autorregresión — el valor actual depende de los `p` valores pasados
- **I (d)**: diferenciación — hace la serie estacionaria eliminando tendencia
- **MA (q)**: media móvil — el valor actual depende de los `q` errores pasados

SARIMA extiende ARIMA con un término estacional `(P,D,Q)_m` que captura patrones que se repiten cada `m` períodos.

`AutoARIMA` busca automáticamente los órdenes óptimos `(p,d,q)(P,D,Q)_m` minimizando AIC o BIC. Implementa el algoritmo de Hyndman-Khandakar (2008).

---

## ¿Para qué tipo de demanda?

| Clasificación | Candidato ARIMA | Cuándo preferirlo sobre ETS |
|---|---|---|
| Smooth sin estacionalidad | ARIMA(p,d,q) | Cuando hay autocorrelación de corto plazo que ETS no captura (ACF significativa en lags 1-3) |
| Smooth con estacionalidad | SARIMA | Raramente mejor que Holt-Winters; útil si la estacionalidad tiene estructura compleja |
| Erratic | ARIMA | Con CV2 alto, puede manejar varianza variable mejor que ETS aditivo |
| Intermittent / Lumpy | **No aplicar** | Los ceros violan los supuestos de normalidad de ARIMA |

**Regla práctica**: si el ACF del SKU muestra lags 1-2 significativos pero no estacionalidad, ARIMA puede superar a ETS. Si el ACF es plano (caso SKU-00002 del ejemplo), ETS o incluso Naive son suficientes.

---

## ¿Qué se necesita para que funcione bien?

- **Estacionariedad** (o que AutoARIMA determine el `d` correcto): la serie no debe tener tendencia explosiva.
- **Historia suficiente**: mínimo 50 observaciones para estimar órdenes AR/MA con precisión. Con < 30, los órdenes seleccionados son inestables.
- **Sin outliers**: los outliers inflan las estimaciones de los parámetros AR y MA. Ejecutar `treat_outliers()` antes de ajustar.
- **Periodos sin ceros excesivos**: ARIMA asume distribución gaussiana del error — no adecuado si más del 30% de los valores son cero.

---

## Fallas conocidas y limitaciones

| Falla | Causa | Mitigación |
|---|---|---|
| Sobreajuste con órdenes altos | AutoARIMA puede seleccionar ARIMA(3,1,3) cuando la serie no lo justifica | Limitar `max_p=3`, `max_q=3`; verificar con AIC |
| Lento en escala sin vectorización | `statsmodels.ARIMA` es ~50x más lento que `StatsForecast.AutoARIMA` | **Siempre usar StatsForecast** |
| Intervalos de confianza demasiado anchos | Órdenes altos aumentan la incertidumbre paramétrica | Preferir modelos parsimoniosos (AIC penaliza complejidad) |
| No maneja bien cambios estructurales | Un break de nivel hace que d=1 diferencia en exceso | Detectar cambios con Mann-Kendall; si hay break claro, cortar el histórico antes del break |
| Confunde estacionalidad con tendencia | d=1 + D=1 puede sobrediferenciar | Usar SARIMA solo si el test de estacionalidad del clasificador lo confirmó |

---

## Parámetros clave

```python
from statsforecast.models import AutoARIMA

model = AutoARIMA(
    season_length=12,     # m: ciclos estacionales (12=mensual, 52=semanal)
    max_p=5,              # máximo orden AR no estacional
    max_q=5,              # máximo orden MA no estacional
    max_P=2,              # máximo orden AR estacional
    max_Q=2,              # máximo orden MA estacional
    d=None,               # None = determinar automáticamente (test ADF/KPSS)
    D=None,               # None = determinar automáticamente (test Canova-Hansen)
    information_criterion="aic",  # criterio de selección de órdenes
    approximation=True,   # más rápido en escala; ligeramente menos preciso
)
```

---

## Cuándo usar ARIMA vs. ETS en este proyecto

| Señal | Modelo preferido |
|---|---|
| ACF con 1-2 lags significativos, sin estacionalidad | AutoARIMA |
| ACF plano (ruido blanco) | ETS(A,N,N) o Naive |
| Estacionalidad fuerte (lag 12 significativo) | AutoETS (Holt-Winters) |
| Estacionalidad + autocorrelación de corto plazo | SARIMA o Prophet |
| CV2 > 0.49 (erratic/lumpy) | ETS multiplicativo, no ARIMA |

En la práctica, para el perfil industrial (800 SKUs, mayormente smooth), AutoETS será el ganador en la mayoría de los casos. ARIMA añadirá valor para el ~15% de SKUs con estructura AR detectable en el ACF.
