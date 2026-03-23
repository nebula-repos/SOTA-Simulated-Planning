"""
Configuración para Generador de Datos Sintéticos de Transacciones
=================================================================
Dos perfiles seleccionables: "industrial" (oleohidráulica) y "retail" (supermercado).
Cambiar PROFILE para seleccionar el dominio.
"""

# ============================================================
# PERFIL ACTIVO
# ============================================================
PROFILE = "industrial"  # "industrial" o "retail"

# ============================================================
# PARÁMETROS COMPARTIDOS (independientes del perfil)
# ============================================================
RANDOM_SEED = 42
START_DATE = "2022-01-01"
END_DATE = "2024-12-31"
OUTPUT_DIR = "./output"

# Umbrales de clasificación XYZ
XYZ_THRESHOLDS = {
    "X_max_cv2": 0.25,
    "Y_max_cv2": 0.64,
}

# Umbrales Syntetos-Boylan
SYNTETOS_BOYLAN_THRESHOLDS = {
    "adi_cutoff": 1.32,
    "cv2_cutoff": 0.49,
}

# Parámetros de ruido en series temporales
LOGNORMAL_SIGMA_FACTOR = 0.7
NORMAL_NOISE_FLOOR = 0.1

# Probabilidad de registrar días de stockout en transacciones
STOCKOUT_RECORD_PROBABILITY = 0.3


# ============================================================
# PERFIL: INDUSTRIAL (Oleohidráulica - Talleres Lucas)
# ============================================================
_INDUSTRIAL = {
    "n_products": 800,

    "locations": [
        "Santiago",
        "Antofagasta",
        "Copiapó",
        "Concepción",
        "Lima",
    ],

    "brands": [
        "Parker", "Danfoss", "Poclain", "Rexroth", "Denison",
        "Commercial Hydraulics", "Argo Hytos", "Sun Hydraulics",
        "Olaer", "Webtec", "KTR", "Bezares", "Calzoni",
        "Scanreco", "FaberCom", "Bucher Hydraulics", "Hydac",
        "Casappa", "Marzocchi", "Brevini",
    ],

    "categories": {
        "Bombas Hidráulicas": {
            "subcats": ["Engranaje", "Paleta", "Pistones Axiales", "Pistones Radiales"],
            "seasonality": "mining_peak",
            "price_range": (150_000, 4_500_000),
            "margin_range": (0.12, 0.28),
        },
        "Motores Hidráulicos": {
            "subcats": ["Engranaje", "Orbitales", "Pistones"],
            "seasonality": "mining_peak",
            "price_range": (200_000, 5_000_000),
            "margin_range": (0.12, 0.25),
        },
        "Mandos Finales": {
            "subcats": ["Reductores Planetarios"],
            "seasonality": "mining_peak",
            "price_range": (800_000, 8_000_000),
            "margin_range": (0.10, 0.22),
        },
        "Comandos Direccionales": {
            "subcats": ["Direccionales Múltiples", "Proporcionales", "Monoblock"],
            "seasonality": "stable",
            "price_range": (120_000, 2_500_000),
            "margin_range": (0.15, 0.30),
        },
        "Válvulas": {
            "subcats": ["Cartridge", "Direccional", "Control Flujo", "Control Presión", "Proporcionales"],
            "seasonality": "stable",
            "price_range": (25_000, 800_000),
            "margin_range": (0.18, 0.35),
        },
        "Filtros": {
            "subcats": ["Spin-On", "Presión", "Retorno", "Alta Presión", "Accesorios Estanque"],
            "seasonality": "maintenance_cycle",
            "price_range": (8_000, 120_000),
            "margin_range": (0.25, 0.45),
        },
        "Acumuladores": {
            "subcats": ["Diafragma", "Vejiga", "Pistones"],
            "seasonality": "stable",
            "price_range": (80_000, 1_500_000),
            "margin_range": (0.15, 0.28),
        },
        "Instrumentación": {
            "subcats": ["Sensores Presión", "Caudalímetros", "Manómetros", "Data Loggers"],
            "seasonality": "project_driven",
            "price_range": (30_000, 600_000),
            "margin_range": (0.20, 0.35),
        },
        "Enfriadores": {
            "subcats": ["Agua-Aceite"],
            "seasonality": "summer_industrial",
            "price_range": (150_000, 2_000_000),
            "margin_range": (0.12, 0.25),
        },
        "Accesorios y Acoples": {
            "subcats": ["Acoples Rotex", "Campanas", "Adaptadores", "Tomas de Fuerza"],
            "seasonality": "stable",
            "price_range": (5_000, 250_000),
            "margin_range": (0.22, 0.40),
        },
        "Winches": {
            "subcats": ["Planetarios", "Izado", "Recuperación"],
            "seasonality": "mining_peak",
            "price_range": (500_000, 6_000_000),
            "margin_range": (0.10, 0.22),
        },
        "Control y Electrónica": {
            "subcats": ["Joystick", "Microcontroladores", "Control Remoto", "Displays"],
            "seasonality": "project_driven",
            "price_range": (50_000, 1_200_000),
            "margin_range": (0.18, 0.32),
        },
    },

    "demand_patterns": {
        "constant": {
            "description": "Demanda estable con baja variabilidad (consumibles regulares: filtros, sellos)",
            "cv_range": (0.05, 0.20),
            "adi_range": (1.0, 1.1),
            "weight": 0.06,
        },
        "smooth": {
            "description": "Demanda suave con variabilidad moderada (repuestos de alta rotación)",
            "cv_range": (0.15, 0.35),
            "adi_range": (1.0, 1.15),
            "weight": 0.08,
        },
        "erratic": {
            "description": "Demanda continua pero con alta variabilidad en cantidad",
            "cv_range": (0.50, 1.30),
            "adi_range": (1.0, 1.4),
            "weight": 0.10,
        },
        "seasonal": {
            "description": "Patrón estacional ligado a ciclos de mantenimiento o minería",
            "cv_range": (0.30, 0.65),
            "adi_range": (1.0, 1.3),
            "weight": 0.06,
        },
        "trend_up": {
            "description": "Tendencia creciente (producto nuevo ganando adopción)",
            "cv_range": (0.10, 0.35),
            "adi_range": (1.0, 1.2),
            "weight": 0.05,
        },
        "trend_down": {
            "description": "Tendencia decreciente (modelo descontinuado por fabricante)",
            "cv_range": (0.10, 0.35),
            "adi_range": (1.0, 1.5),
            "weight": 0.05,
        },
        "intermittent": {
            "description": "Demanda esporádica con intervalos irregulares, baja variabilidad en qty",
            "cv_range": (0.05, 0.35),
            "adi_range": (1.8, 5.0),
            "weight": 0.25,
        },
        "lumpy": {
            "description": "Demanda esporádica Y alta variabilidad en cantidad (equipos especializados)",
            "cv_range": (0.60, 1.80),
            "adi_range": (2.0, 8.0),
            "weight": 0.20,
        },
        "new_product": {
            "description": "Producto/línea nueva con ramp-up inicial",
            "cv_range": (0.30, 0.80),
            "adi_range": (1.2, 2.5),
            "weight": 0.05,
        },
        "project_driven": {
            "description": "Base baja con picos por órdenes de proyecto minero/construcción",
            "cv_range": (0.50, 1.20),
            "adi_range": (1.3, 3.0),
            "weight": 0.10,
        },
    },

    # Distribución ABC
    "abc_ratios": {"A": 0.10, "B": 0.25, "C": 0.65},

    # Demanda base por clase ABC (unidades/día cuando hay demanda)
    "base_demand_ranges": {
        "A": (2, 15),
        "B": (0.5, 4),
        "C": (0.1, 1.0),
    },

    # Parámetros de catálogo
    "supplier_profiles": {
        "Hydroline Andina": {"avg_lead_time_days": 14, "payment_terms_days": 30},
        "Tecfluid Norte": {"avg_lead_time_days": 21, "payment_terms_days": 30},
        "Andes Motion": {"avg_lead_time_days": 30, "payment_terms_days": 45},
        "Pacific Seal Supply": {"avg_lead_time_days": 45, "payment_terms_days": 45},
        "Industrial Flow Partners": {"avg_lead_time_days": 60, "payment_terms_days": 60},
        "Maestranza Integral": {"avg_lead_time_days": 90, "payment_terms_days": 60},
    },
    "moq_choices": [1, 2, 5, 10, 25],
    "extra_field_name": "warranty_months",
    "extra_field_choices": [None, 6, 12, 18, 24, 36],

    # Perfiles de estacionalidad
    "seasonality_profiles": {
        "mining_peak": {
            1: 1.15, 2: 1.10, 3: 1.20, 4: 1.25, 5: 1.15, 6: 1.00,
            7: 0.85, 8: 0.90, 9: 1.10, 10: 1.15, 11: 1.05, 12: 0.75,
        },
        "maintenance_cycle": {
            1: 0.90, 2: 0.85, 3: 1.25, 4: 0.90, 5: 0.85, 6: 1.25,
            7: 0.90, 8: 0.85, 9: 1.25, 10: 0.90, 11: 0.85, 12: 1.10,
        },
        "project_driven": {
            1: 1.10, 2: 1.05, 3: 1.15, 4: 1.10, 5: 0.95, 6: 0.90,
            7: 0.85, 8: 0.95, 9: 1.05, 10: 1.10, 11: 0.95, 12: 0.75,
        },
        "summer_industrial": {
            1: 1.35, 2: 1.30, 3: 1.10, 4: 0.90, 5: 0.75, 6: 0.70,
            7: 0.70, 8: 0.75, 9: 0.85, 10: 0.95, 11: 1.10, 12: 1.35,
        },
        "stable": None,
    },

    # Fuerza de estacionalidad por patrón de demanda
    "season_strength_ranges": {
        "seasonal": (0.6, 1.0),
        "constant": (0.0, 0.08),
        "smooth": (0.0, 0.10),
        "_default": (0.05, 0.25),
    },

    # Factores día de semana (Lun-Vie operación industrial)
    "dow_map": {
        0: 1.05,  # Lunes
        1: 1.10,  # Martes - pico de pedidos
        2: 1.05,  # Miércoles
        3: 1.00,  # Jueves
        4: 0.80,  # Viernes - media jornada
        5: 0.00,  # Sábado - cerrado
        6: 0.00,  # Domingo - cerrado
    },

    # Fracción de días hábiles (para compensar intermitencia)
    "weekday_fraction": 5 / 7,

    # Tendencia
    "trend_up_growth_rate": (0.0002, 0.0008),
    "trend_down_decay_rate": (0.0002, 0.0008),
    "trend_down_floor": 0.10,
    "new_product_ramp_days": (60, 180),
    "new_product_growth": (0.0001, 0.0004),

    # Picos de demanda (proyectos industriales)
    "spike_params": {
        "project_driven": {"n_spikes": (3, 8)},
        "_default": {"n_spikes": (0, 2)},
    },
    "spike_duration": (1, 5),
    "spike_multiplier": (3.0, 15.0),
    "spike_discount_range": (0.03, 0.12),

    # Stockouts
    "stockouts_per_abc": {
        "A": (0, 1),
        "B": (0, 3),
        "C": (1, 15),
    },
    "stockout_duration": (3, 21),

    # Umbral ADI para intermitencia
    "adi_threshold_for_continuous": 1.15,

    # Asignación de locaciones por ABC
    "locations_per_abc": {
        "A": (2, 5),
        "B": (1, 3),
        "C": (1, 2),
    },
    "location_factor_range": (0.5, 1.5),
}


# ============================================================
# PERFIL: RETAIL (Supermercado - valores originales)
# ============================================================
_RETAIL = {
    "n_products": 1200,

    "locations": [f"Tienda_{i:03d}" for i in range(1, 11)],

    "brands": [
        "MarcaA", "MarcaB", "MarcaC", "MarcaD", "MarcaE",
        "MarcaF", "MarcaG", "MarcaH", "MarcaI", "MarcaJ",
        "MarcaK", "MarcaL", "MarcaM", "MarcaN", "MarcaO",
    ],

    "categories": {
        "Bebidas": {
            "subcats": ["Gaseosas", "Jugos", "Agua", "Energéticas", "Cervezas"],
            "seasonality": "summer_peak",
            "price_range": (500, 5000),
            "margin_range": (0.15, 0.35),
        },
        "Lácteos": {
            "subcats": ["Leche", "Yogurt", "Quesos", "Mantequilla", "Crema"],
            "seasonality": "stable",
            "price_range": (800, 8000),
            "margin_range": (0.10, 0.25),
        },
        "Snacks": {
            "subcats": ["Papas Fritas", "Galletas", "Chocolates", "Frutos Secos", "Cereales"],
            "seasonality": "winter_peak",
            "price_range": (300, 4000),
            "margin_range": (0.20, 0.45),
        },
        "Limpieza": {
            "subcats": ["Detergente", "Desinfectante", "Jabón", "Cloro", "Suavizante"],
            "seasonality": "stable",
            "price_range": (1000, 12000),
            "margin_range": (0.15, 0.30),
        },
        "Cuidado Personal": {
            "subcats": ["Shampoo", "Crema Corporal", "Desodorante", "Pasta Dental", "Protector Solar"],
            "seasonality": "summer_peak",
            "price_range": (1500, 15000),
            "margin_range": (0.25, 0.50),
        },
        "Abarrotes": {
            "subcats": ["Arroz", "Fideos", "Aceite", "Azúcar", "Harina"],
            "seasonality": "stable",
            "price_range": (800, 6000),
            "margin_range": (0.08, 0.20),
        },
        "Congelados": {
            "subcats": ["Helados", "Pizzas", "Verduras Congeladas", "Mariscos", "Empanadas"],
            "seasonality": "mixed",
            "price_range": (1500, 12000),
            "margin_range": (0.18, 0.35),
        },
        "Panadería": {
            "subcats": ["Pan Molde", "Pan Artesanal", "Tortas", "Bollería", "Galletas Frescas"],
            "seasonality": "winter_peak",
            "price_range": (500, 8000),
            "margin_range": (0.30, 0.55),
        },
    },

    "demand_patterns": {
        "constant": {
            "description": "Demanda estable con baja variabilidad",
            "cv_range": (0.05, 0.15),
            "adi_range": (1.0, 1.05),
            "weight": 0.15,
        },
        "smooth": {
            "description": "Demanda suave con variabilidad moderada",
            "cv_range": (0.15, 0.30),
            "adi_range": (1.0, 1.1),
            "weight": 0.15,
        },
        "erratic": {
            "description": "Demanda continua pero con alta variabilidad en cantidad",
            "cv_range": (0.50, 1.20),
            "adi_range": (1.0, 1.3),
            "weight": 0.12,
        },
        "seasonal": {
            "description": "Patrón estacional claro con picos predecibles",
            "cv_range": (0.30, 0.60),
            "adi_range": (1.0, 1.2),
            "weight": 0.12,
        },
        "trend_up": {
            "description": "Tendencia creciente sostenida",
            "cv_range": (0.10, 0.30),
            "adi_range": (1.0, 1.1),
            "weight": 0.08,
        },
        "trend_down": {
            "description": "Tendencia decreciente (producto en declive)",
            "cv_range": (0.10, 0.30),
            "adi_range": (1.0, 1.3),
            "weight": 0.08,
        },
        "intermittent": {
            "description": "Demanda esporádica con intervalos irregulares, baja variabilidad en qty",
            "cv_range": (0.05, 0.30),
            "adi_range": (1.5, 3.0),
            "weight": 0.10,
        },
        "lumpy": {
            "description": "Demanda esporádica Y alta variabilidad en cantidad (el más difícil)",
            "cv_range": (0.60, 1.50),
            "adi_range": (1.5, 4.0),
            "weight": 0.08,
        },
        "new_product": {
            "description": "Producto nuevo con ramp-up inicial",
            "cv_range": (0.30, 0.70),
            "adi_range": (1.0, 1.5),
            "weight": 0.05,
        },
        "project_driven": {
            "description": "Base estable con picos de promoción",
            "cv_range": (0.40, 0.80),
            "adi_range": (1.0, 1.1),
            "weight": 0.07,
        },
    },

    # Distribución ABC
    "abc_ratios": {"A": 0.15, "B": 0.35, "C": 0.50},

    # Demanda base por clase ABC
    "base_demand_ranges": {
        "A": (15, 80),
        "B": (3, 20),
        "C": (0.5, 5),
    },

    # Parámetros de catálogo
    "supplier_profiles": {
        "Distribuidora Central": {"avg_lead_time_days": 3, "payment_terms_days": 7},
        "Foodservice Andino": {"avg_lead_time_days": 5, "payment_terms_days": 15},
        "Consumo Masivo Sur": {"avg_lead_time_days": 7, "payment_terms_days": 15},
        "Importadora Regional": {"avg_lead_time_days": 10, "payment_terms_days": 30},
        "Abastecimiento Nacional": {"avg_lead_time_days": 14, "payment_terms_days": 30},
        "Canal Moderno Supply": {"avg_lead_time_days": 21, "payment_terms_days": 45},
    },
    "moq_choices": [1, 6, 12, 24, 48],
    "extra_field_name": "shelf_life_days",
    "extra_field_choices": [None, 7, 14, 30, 60, 90, 180, 365],

    # Perfiles de estacionalidad
    "seasonality_profiles": {
        "summer_peak": {
            1: 1.4, 2: 1.3, 3: 1.1, 4: 0.9, 5: 0.8, 6: 0.7,
            7: 0.7, 8: 0.75, 9: 0.85, 10: 0.95, 11: 1.1, 12: 1.5,
        },
        "winter_peak": {
            1: 0.7, 2: 0.75, 3: 0.9, 4: 1.1, 5: 1.3, 6: 1.5,
            7: 1.5, 8: 1.3, 9: 1.1, 10: 0.9, 11: 0.8, 12: 0.7,
        },
        "mixed": {
            1: 1.3, 2: 1.1, 3: 0.9, 4: 0.85, 5: 0.8, 6: 0.9,
            7: 1.0, 8: 0.95, 9: 1.0, 10: 1.05, 11: 1.1, 12: 1.4,
        },
        "stable": None,
    },

    # Fuerza de estacionalidad por patrón
    "season_strength_ranges": {
        "seasonal": (0.7, 1.0),
        "constant": (0.0, 0.15),
        "smooth": (0.0, 0.15),
        "_default": (0.1, 0.4),
    },

    # Factores día de semana (retail: fines de semana venden más)
    "dow_map": {
        0: 0.90,  # Lunes
        1: 0.85,  # Martes
        2: 0.88,  # Miércoles
        3: 0.92,  # Jueves
        4: 1.05,  # Viernes
        5: 1.25,  # Sábado - pico
        6: 1.15,  # Domingo
    },

    # Fracción días hábiles (retail opera 7/7)
    "weekday_fraction": 1.0,

    # Tendencia
    "trend_up_growth_rate": (0.0003, 0.0012),
    "trend_down_decay_rate": (0.0003, 0.0010),
    "trend_down_floor": 0.15,
    "new_product_ramp_days": (30, 120),
    "new_product_growth": (0.0001, 0.0005),

    # Picos de demanda (promociones retail)
    "spike_params": {
        "project_driven": {"n_spikes": (8, 18)},
        "_default": {"n_spikes": (0, 5)},
    },
    "spike_duration": (3, 14),
    "spike_multiplier": (1.5, 4.0),
    "spike_discount_range": (0.10, 0.30),

    # Stockouts
    "stockouts_per_abc": {
        "A": (0, 2),
        "B": (0, 5),
        "C": (0, 10),
    },
    "stockout_duration": (2, 14),

    # Umbral ADI para intermitencia
    "adi_threshold_for_continuous": 1.1,

    # Asignación de locaciones por ABC
    "locations_per_abc": {
        "A": (3, 7),
        "B": (1, 4),
        "C": (1, 2),
    },
    "location_factor_range": (0.6, 1.4),
}


# ============================================================
# EXPORTAR VARIABLES DEL PERFIL ACTIVO
# ============================================================
_PROFILES = {
    "industrial": _INDUSTRIAL,
    "retail": _RETAIL,
}

_active = _PROFILES[PROFILE]

N_PRODUCTS = _active["n_products"]
LOCATIONS = _active["locations"]
BRANDS = _active["brands"]
CATEGORIES = _active["categories"]
DEMAND_PATTERNS = _active["demand_patterns"]

ABC_RATIOS = _active["abc_ratios"]
BASE_DEMAND_RANGES = _active["base_demand_ranges"]

SUPPLIER_PROFILES = _active["supplier_profiles"]
MOQ_CHOICES = _active["moq_choices"]
EXTRA_FIELD_NAME = _active["extra_field_name"]
EXTRA_FIELD_CHOICES = _active["extra_field_choices"]

SEASONALITY_PROFILES = _active["seasonality_profiles"]
SEASON_STRENGTH_RANGES = _active["season_strength_ranges"]
DOW_MAP = _active["dow_map"]
WEEKDAY_FRACTION = _active["weekday_fraction"]

TREND_UP_GROWTH_RATE = _active["trend_up_growth_rate"]
TREND_DOWN_DECAY_RATE = _active["trend_down_decay_rate"]
TREND_DOWN_FLOOR = _active["trend_down_floor"]
NEW_PRODUCT_RAMP_DAYS = _active["new_product_ramp_days"]
NEW_PRODUCT_GROWTH = _active["new_product_growth"]

SPIKE_PARAMS = _active["spike_params"]
SPIKE_DURATION = _active["spike_duration"]
SPIKE_MULTIPLIER = _active["spike_multiplier"]
SPIKE_DISCOUNT_RANGE = _active["spike_discount_range"]

STOCKOUTS_PER_ABC = _active["stockouts_per_abc"]
STOCKOUT_DURATION = _active["stockout_duration"]

ADI_THRESHOLD_FOR_CONTINUOUS = _active["adi_threshold_for_continuous"]

LOCATIONS_PER_ABC = _active["locations_per_abc"]
LOCATION_FACTOR_RANGE = _active["location_factor_range"]
