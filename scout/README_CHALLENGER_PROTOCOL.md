# 🎯 Challenger Protocol Individual

**Sistema de análisis profundo para jugadores de League of Legends enfocado en alcanzar el top 1 del servidor.**

## 🌟 Características Principales

### 1. Detección Automática de Errores
Identifica 6 patrones de error recurrentes:
- **Early Solo Deaths**: Muertes 1v1 pre-15min
- **Gank Deaths**: Muertes por ganks (2+ enemigos)
- **Objective Throws**: Muertes en Baron/Drake que pierden el objetivo
- **Overextension**: Muertes lejos de torres
- **Poor Recall Timing**: Recalls con >1500g sin gastar
- **Vision Gaps**: Vision score bajo vs Challenger

### 2. Métricas Específicas de Jungle
8 KPIs únicos para jungle:
- Gank success rate
- Objective control %
- Scuttle control
- Early pressure score
- Counter jungle CS
- Clear speed
- Time per quadrant
- Invasion frequency

### 3. Visualizaciones Avanzadas
- **Death Heatmaps**: Densidad de muertes sobre el mapa
- **Jungle Pathing**: Rutas de movimiento visualizadas
- **Gold Diff Timelines**: Ventaja/desventaja minuto a minuto
- **Impact Score Evolution**: Tendencia de mejora/declive
- **Radar Charts**: Comparación 8D vs Challenger

### 4. Alertas Accionables
Cada alerta incluye:
- **Qué está mal**: Evidencia numérica
- **Por qué importa**: Impacto en WR
- **Cómo arreglarlo**: Drill específico para practicar

### 5. Análisis Comparativo
- Benchmarks vs Challenger (mismo campeón/rol)
- Peer ranking (posición entre los 10 de cada partida)
- Pilares de rendimiento normalizados

## 📊 Dashboard

### Tab 1: 🎯 SCOUT HUB
- Alertas críticas (P0)
- Pilares de rendimiento
- Métricas de jungle (si aplica)
- Radar vs Challenger
- Patrones de error con drills

### Tab 2: 🗺️ ANÁLISIS ESPACIAL
- Heatmap de muertes
- Jungle pathing (última partida)
- Zonas de peligro identificadas

### Tab 3: 📈 EVOLUCIÓN
- Tendencia de impact score
- Regresión lineal (mejora/declive)
- Análisis temporal

### Tab 4: 🏆 CHAMPION POOL
- Stats por campeón
- Consistencia por pick
- Recomendaciones de champion pool

### Tab 5: 🔬 MATCH ANALYSIS
- Selector de partida individual
- Gold diff timeline vs oponente
- Stats detalladas de la partida

## 🚀 Instalación

### Requisitos
- Python 3.9+
- Supabase configurado
- Riot API key (para sync inicial)

### Setup
```bash
# 1. Clonar repositorio
cd D:\AAPROYECTOS\LOL\scout

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Configurar variables de entorno
# .env
SUPABASE_URL=your_supabase_url
SUPABASE_SERVICE_ROLE_KEY=your_key
RIOT_API_KEY=your_riot_key

# 4. Crear perfil de jugador
# profiles/tu_nombre.yaml
riot_id: "TuNombre#TAG"
game_name: "TuNombre"
tag_line: "TAG"
primary_role: "JUNGLE"
platform: "la2"
region: "americas"

# 5. Sincronizar datos
python sync_player.py --profile profiles/tu_nombre.yaml

# 6. Ejecutar dashboard
streamlit run app_scout.py
```

## 📖 Guía de Uso

### Primer Uso
1. **Sincroniza tus datos**: `python sync_player.py --profile profiles/tu_perfil.yaml`
2. **Abre el dashboard**: `streamlit run app_scout.py`
3. **Revisa alertas P0**: Identifica tus 3 errores críticos
4. **Crea plan semanal**: Enfócate en 1-2 drills por semana

### Uso Diario
1. Juega tus ranked games
2. Después de cada sesión, revisa **Match Analysis**
3. Identifica errores específicos en tus derrotas
4. Practica los drills sugeridos

### Uso Semanal
1. Re-sincroniza datos: `python sync_player.py`
2. Revisa **Evolución** para ver tendencia
3. Compara métricas vs semana anterior
4. Ajusta plan según nuevas alertas P0

## 🎮 Ejemplo de Flujo de Mejora

### Semana 1: Identificación
```
Dashboard muestra:
- P0: "EARLY_SOLO_DEATHS: 2.3/game (-60% vs Challenger)"
- Drill: "Practice Tool 1v1s, 30min/day"
- Impact: +8% WR estimado
```

### Semana 2: Práctica
```
Acciones:
- 30min diarios de Practice Tool
- Estudiar powerspikes de campeones enemigos
- Revisar replays de primeras muertes
```

### Semana 3: Validación
```
Dashboard muestra:
- Early solo deaths: 2.3 → 1.2/game (-48%)
- WR: +4% real
- Nueva P0: "VISION_GAPS"
```

### Semana 4: Siguiente Objetivo
```
Nuevo foco:
- Mejorar vision score
- Drill: 2 control wards por back
- Ward jungle enemigo pre-objectives
```

## 🔧 Arquitectura Técnica

### Data Flow
```
Riot API → sync_player.py → Supabase
                                ↓
                    app_scout.py (Streamlit)
                                ↓
        ┌───────────────────────┼───────────────────────┐
        ↓                       ↓                       ↓
  data_loader_scout      jungle_metrics        error_patterns
        ↓                       ↓                       ↓
  features_scout          analysis_scout      visualization_scout
        ↓                       ↓                       ↓
                        Dashboard UI
```

### Módulos Principales

#### `data_loader_scout.py`
- Carga optimizada desde Supabase
- Filtros temporales (últimos N días)
- Queries específicas (death events, etc.)

#### `jungle_metrics.py`
- Cálculo de KPIs de jungle
- Análisis de pathing
- Detección de invasiones

#### `error_patterns.py`
- Detección de 6 anti-patrones
- Correlación con derrotas
- Generación de drills

#### `visualization_scout.py`
- Heatmaps (Gaussian KDE)
- Timelines (gold diff, impact)
- Radares (8 dimensiones)

#### `analysis_scout.py`
- Resúmenes de jugador
- Tendencias temporales
- Comparativas vs peers

## 📈 Métricas de Éxito

### Performance
- **Load time**: <3s para 30 partidas
- **Query efficiency**: Solo carga datos relevantes
- **Cache**: Resultados cacheados por 1 hora

### Análisis
- **8** métricas de jungle
- **6** patrones de error
- **5** visualizaciones avanzadas
- **3** niveles de prioridad (P0/P1/P2)

### Impacto Esperado
- **Mes 1**: +3-5% WR (fix errores críticos)
- **Mes 2**: +2-4% WR (optimizar recursos)
- **Mes 3**: +2-3% WR (consistencia)
- **Mes 4+**: Top 1 server

## 🎯 Diferencias vs App Original

### Antes
- 3 tabs básicos
- Solo KDA, WR, stats simples
- 1 gráfico (scatter plot)
- Alertas genéricas sin drills
- Sin análisis espacial
- Sin métricas de jungle

### Ahora
- 5 tabs especializados
- Análisis profundo multi-dimensional
- 5 visualizaciones avanzadas
- Alertas con drills específicos
- Heatmaps + jungle pathing
- 8 métricas de jungle
- Detección automática de errores
- Comparación vs Challenger

## 🛠️ Troubleshooting

### Error: "No hay datos para [profile]"
**Causa**: No se ha sincronizado el perfil
**Solución**: `python sync_player.py --profile profiles/tu_perfil.yaml`

### Error: Heatmap vacío
**Causa**: Faltan datos de posición en eventos
**Solución**: Verificar que `scout_events` tiene `position_x` y `position_y`

### Error: Jungle metrics no aparecen
**Causa**: `primary_role` no es "JUNGLE"
**Solución**: Actualizar `profiles/tu_perfil.yaml` con `primary_role: "JUNGLE"`

### Performance lento
**Causa**: Demasiados datos cargados
**Solución**: 
1. Reducir `days_back` en `load_player_summary_from_db()`
2. Limpiar cache: `streamlit cache clear`

## 📚 Documentación Adicional

- `IMPLEMENTATION_SUMMARY.md`: Detalles técnicos de implementación
- `QUICK_START.md`: Guía rápida de inicio
- `challenger-protocol-individual-db3e6d.md`: Plan completo original

## 🤝 Contribuciones

Este proyecto es parte del ecosistema Challenger Protocol. Para contribuir:
1. Fork el repositorio
2. Crea una branch (`feature/nueva-metrica`)
3. Commit cambios
4. Push y crea Pull Request

## 📝 Licencia

Proyecto interno - Challenger Protocol Team

## 🙏 Agradecimientos

- Riot Games API
- Supabase
- Streamlit
- Plotly

---

**Versión**: 2.0.0
**Última actualización**: 2026-05-05
**Autor**: Cascade AI + Challenger Protocol Team

**¡Buena suerte en tu camino al Top 1! 🏆**
