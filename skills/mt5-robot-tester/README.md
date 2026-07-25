# mt5-robot-tester — Selección de robots MT5 por línea de comandos

Pipeline de 3 rondas que selecciona los **mejores robots (EAs) de MetaTrader 5
que aún no han sido backtesteados**, ejecutando el Probador de Estrategias por
CLI, moviendo los bots entre carpetas y **aprendiendo en cada loop** para mejorar
la selección. Todo el proceso queda registrado y es **reanudable**.

> ⚠️ Requiere **Windows + MetaTrader 5** en tiempo de ejecución (lanza
> `terminal64.exe`) y **tick data** del broker (modelado por defecto: ticks
> reales, `Model=4`). Los parsers y la lógica son Python stdlib y se testean sin
> MT5.

## Carpetas (bajo `MQL5\Experts`)

| Carpeta | Rol |
|---------|-----|
| `bots candidatos` | Pendientes de testear (entrada). |
| `Bots en testeo` | Todos los ya testeados (se mueven aquí al terminar). |
| `bots finalistas` | Los que pasan todos los criterios (se **copian** aquí + su `.set`). |

## Procedimiento completo

Para **cada** `.ex5` de *candidatos*:

### Ronda 1 — screening sobre todos los pares
- Config del probador: intervalo **2020.01.01 → 2026.06.30**, **H1**,
  **Model=4** (ticks reales), retrasos 32 ms, depósito **10 000 USD**, **1:100**.
- Un **backtest por símbolo** de la lista `common.symbols` (un `Optimization=0`
  por símbolo). Cada símbolo = un resultado. *(En el build 6061 el XML de
  `Optimization=3` sale vacío — los resultados van solo al caché `.opt` — por eso
  se hace por símbolo; ver `references/mt5-cli-reference.md`.)*
- **Gate (ambas condiciones):**
  1. **≥5 símbolos con beneficio positivo** (los "azules"), y
  2. el **mejor símbolo** da **≥ 3× el depósito** (≥ 30 000).
- Si no pasa → el bot se descarta (se mueve a *en testeo*).

### Ronda 2 — backtest del mejor par
- Se elige el **símbolo con más beneficio** de la Ronda 1.
- Backtest simple (`Optimization=0`) en ese símbolo, misma config.
- Se analiza el backtest reconstruyendo la serie de balance desde la tabla de
  operaciones: **Bº%**, **DD máx = mayor(balance%, equity%)**, **% meses
  positivos**, **años positivos**, **LR Correlation**, **meses hasta nuevo
  máximo**. Referencia de "buen backtest": Bº≥300%, DD<15%, meses>70%, años todos
  positivos, **LR≥0.80**, meses-a-nuevo-máx ≤3.

### Ronda 3 — optimización secuencial de parámetros
- Se optimizan los **5–6 parámetros que van después de `MagicNumber`** (medias
  móviles, coeficientes…). Lo anterior (filling, comentario, MagicNumber) queda
  fijo.
- **Uno a uno**: para cada parámetro, rango **±50% del valor, paso 5%**
  (`Start=V×0.5`, `Step=V×0.05`, `Stop=V×1.5`; entero y `Step≥1` en periodos),
  `Optimization=1`. Se fija su mejor valor y se pasa al siguiente.
- Backtest final con el set optimizado.

### Decisión FINALISTA
El bot es finalista si tras optimizar **mejora** el resultado de la Ronda 2 **y**
logra **beneficio ≥ 4× depósito** (≥ 40 000) **y** **DD ≤ 12%**. Entonces se
**copia a *finalistas*** con su `.set` optimizado.

## Uso

1. Copia `assets/pipeline_config.template.json`, rellena las 3 rutas y (opcional)
   `terminal_path`. **No subas rutas personales al repo.**

2. Dry-run (genera los `.ini` de la Ronda 1 sin lanzar MT5):
   ```bash
   python3 scripts/mt5_batch_tester.py --config mi_config.json \
     --output-dir reports/mt5_pipeline --dry-run
   ```

3. Ejecución completa:
   ```bash
   python3 scripts/mt5_batch_tester.py --config mi_config.json \
     --output-dir reports/mt5_pipeline
   ```

4. Reanudar tras una interrupción (no repite lo ya hecho):
   ```bash
   python3 scripts/mt5_batch_tester.py --config mi_config.json \
     --output-dir reports/mt5_pipeline --resume
   ```

También puedes pasar `--candidates-dir` para sobreescribir la carpeta, y
`--terminal-path` / `$MT5_TERMINAL_PATH` para el terminal.

## Salidas

| Fichero | Contenido |
|---------|-----------|
| `state.json` | Estado por bot/ronda (reanudable). |
| `run.log` | Log cronológico de todo lo ejecutado. |
| `leaderboard_<ts>.md` / `.json` | Ranking con veredicto y métricas. |
| `learnings.json` + `references/learnings.md` | Aprendizaje acumulado entre loops. |
| `mt5_reports/`, `mt5_ini/`, `sets/` | Reports MT5, configs y `.set` por bot/ronda. |

## Auto-aprendizaje

En cada ejecución se acumulan estadísticas en `learnings.json`:
- **Impacto medio por parámetro** al optimizarlo → la Ronda 3 prueba primero los
  parámetros históricamente más influyentes (mejor rendimiento de selección).
- **Priores de símbolos** (frecuencia como mejor par, beneficio medio).
- **Veredictos por bot**.

Es determinista (estadística agregada), y `references/learnings.md` muestra el
resumen legible tras cada loop.

## Verificación / tests

```bash
python3 -m pytest scripts/tests/ -v
```

Cubre parsers (XML de optimización, HTML de backtest + serie de balance), gates,
cálculo de rangos, selección de parámetros tras `MagicNumber`, decisión finalista
y el almacén de aprendizaje. No requiere MT5.

## Notas y límites

- El **retraso de 32 ms** puede no ser configurable por clave INI; el terminal
  suele heredar el ajuste de la GUI (ver `references/mt5-cli-reference.md`).
- Verifica que el report `Report=` de tu build incluye la **tabla de operaciones**
  (necesaria para meses/años/tiempo-a-nuevo-máximo).
- `Model=4` requiere tick data del broker y es lento.
