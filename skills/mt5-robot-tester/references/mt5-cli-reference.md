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
| `Report` | nombre **relativo sin extensión** | Build 6061 escribe el report en la carpeta de datos; el orquestador lo recoge al finalizar. |
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
| **3** | Todos los símbolos del Market Watch; no se usa en Ronda 1 porque build 6061 deja el XML vacío |

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

## Reports — comportamiento REAL verificado (build 6061)

Diagnosticado contra MetaTrader 5 build 6061 (cuenta FTMO-Demo):

- **`Report=` DEBE ser un nombre RELATIVO** (sin ruta ni extensión). MT5 **ignora
  una ruta absoluta** y escribe el reporte en la **raíz de la carpeta de datos**
  del terminal: `<data>\<nombre>.htm` (backtest) + `<nombre>.png` (gráfico). El
  orquestador da un nombre relativo y **recoge el fichero de la carpeta de datos**.
- **Codificación mixta**: el `.htm` del backtest es **UTF-16**; el XML de
  optimización es **UTF-8**. El lector detecta la codificación por BOM y densidad
  de bytes nulos.
- **Tabla de operaciones**: la columna de tiempo se llama **`Fecha/Hora`** (no
  `Hora`) y existe una columna **`Balance`** → de ahí se reconstruye la serie
  (meses/años/tiempo a nuevo máximo).
- **`Optimization=3` (todos los símbolos del Market Watch) NO rellena el XML**
  (`<nombre>.symbols.xml` sale con cabecera y **0 filas**). Los 73 resultados por
  símbolo se guardan solo en el **caché binario** `Tester\cache\*.opt`. Por eso
  este skill hace la **Ronda 1 con un backtest por símbolo** (`Optimization=0`),
  usando la lista `common.symbols` de la config, en vez de fiarse del XML de
  optimización.
- **Optimización de parámetros** (`Optimization=1`, un solo parámetro) → XML
  SpreadsheetML con una fila por combinación (usado en la Ronda 3).
- **El terminal se cierra solo** con `ShutdownTerminal=1` al terminar; el runner
  **espera a ese cierre** (no al primer avistamiento del fichero, que en
  optimización se escribe incrementalmente).

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
