# MetaTrader 5 — Referencia del Probador por línea de comandos

Cómo se automatiza el Probador de Estrategias de MT5 sin GUI, y las claves/
enums que usa este skill. Verifica los detalles marcados **(verificar)** contra
tu build de MT5, ya que el formato exacto de report puede variar.

## Lanzamiento

```bat
terminal64.exe /config:C:\ruta\al\tester.ini
```

- Con `ShutdownTerminal=1` el terminal ejecuta el test y **se cierra solo**, así
  que el proceso termina cuando acaba el backtest (el orquestador espera con un
  timeout).
- `/portable` usa la carpeta de datos junto al `.exe` en vez de la de `%APPDATA%`.
- El terminal debe tener **tick data descargada** del broker para `Model=4`
  (ticks reales), o el test fallará / usará datos incompletos.

## Sección `[Tester]`

| Clave | Valor | Notas |
|-------|-------|-------|
| `Expert` | `bots candidatos\MiBot.ex5` | Ruta **relativa a `MQL5\Experts`**. |
| `ExpertParameters` | `MiBot.set` | `.set` en `MQL5\Profiles\Tester\` (opcional). |
| `Symbol` | `US100.cash` | Símbolo; con `Optimization=3` itera el Market Watch. |
| `Period` | `H1` | Timeframe. |
| `Model` | `4` | Modelo de modelado (ver abajo). |
| `FromDate`/`ToDate` | `2020.01.01` | Formato `YYYY.MM.DD`. |
| `ForwardMode` | `0` | 0 = sin forward. |
| `Deposit` | `10000` | Depósito inicial. |
| `Currency` | `USD` | Divisa del depósito. |
| `Leverage` | `100` | Apalancamiento 1:N. |
| `Optimization` | `0/1/2/3` | Modo de optimización (ver abajo). |
| `OptimizationCriterion` | `0..7` | Criterio (ver abajo). |
| `Report` | ruta **absoluta sin extensión** | Evita que el report caiga en la carpeta de datos con hash. |
| `ReplaceReport` | `1` | Sobrescribe reruns. |
| `ShutdownTerminal` | `1` | Cierra el terminal al acabar (imprescindible para batch). |
| `Visual` | `0` | Sin modo visual. |

### `Model` (modelado)
| Valor | Significado |
|------|-------------|
| 0 | Cada tick |
| 1 | 1 minuto OHLC |
| 2 | Solo precios de apertura |
| 3 | Cálculo matemático |
| **4** | **Cada tick a base de ticks reales** ← usado por defecto |

### `Optimization`
| Valor | Significado |
|------|-------------|
| 0 | Deshabilitado (backtest simple) |
| 1 | Algoritmo lento (completo) |
| 2 | Genético rápido |
| **3** | **Todos los símbolos del Market Watch** ← Ronda 1 |

### `OptimizationCriterion`
| Valor | Criterio |
|------|----------|
| **0** | **Balance máximo / Rentabilidad máxima** ← usado |
| 1 | Balance × Profit Factor |
| 2 | Balance × Beneficio esperado |
| 3 | Balance × Drawdown mínimo |
| 4 | Balance × Recovery Factor |
| 5 | Balance × Sharpe |
| 6 | Personalizado (`OnTester`) |
| 7 | Criterio complejo máximo |

## Sección `[TesterInputs]` (parámetros del EA)

Un valor fijo:

```
StopLossCoef1=1.0
```

Un rango a optimizar — sintaxis `valor||start||step||stop||Y`:

```
GannHiLoPeriod1=43||43||4||129||Y
```

- El último campo `Y`/`N` habilita/deshabilita la optimización de ese parámetro.
- Este skill, en la Ronda 3, pone **un solo** parámetro en rango y **fija** el
  resto en su mejor valor, encadenando de uno en uno.

## Reports

- **Backtest simple** (`Optimization=0`) → HTML (`Report=...` genera `.htm`).
  Incluye el resumen y, cuando el report completo está disponible, la **tabla de
  operaciones/deals** (columnas `Hora … Beneficio Balance`) que este skill usa
  para reconstruir la serie de balance (meses/años/tiempo a nuevo máximo).
  **(verificar** que tu build incluye la tabla de deals en el report generado por
  `Report=`; si no, habrá que exportar el historial por otra vía**)**.
- **Optimización** (`Optimization>0`) → XML (SpreadsheetML) con **un pass por
  fila** (símbolo/inputs/beneficio…). El parser mapea cabeceras EN y ES.

## Caveats

- **Delay 32 ms** (Retrasos): no hay clave INI documentada para el retraso
  aleatorio basado en ping; el terminal suele **heredar el último ajuste de la
  GUI**. Configúralo una vez en el Probador (Retrasos → "… ping … 32 ms")
  **(verificar)**.
- **Encoding**: los `.ini` se escriben en UTF-8; para contenido no-ASCII en
  nombres/comentarios algún build puede requerir UTF-16.
- **Market Watch**: `Optimization=3` prueba exactamente los símbolos presentes en
  la Observación del Mercado; añade/quita símbolos ahí para controlar el universo.
- **Detección del terminal**: `--terminal-path` → `$MT5_TERMINAL_PATH` →
  `config.terminal_path` → instalaciones habituales en `Program Files`.
