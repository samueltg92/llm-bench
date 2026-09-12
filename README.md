# llm-bench

Benchmark de conversaciones completas para comparar latencia, costo y cumplimiento de LLM.
Los proyectos se identifican exclusivamente como **Proyecto 1**, **Proyecto 2**, etc.
Los datos importados, escenarios reales, prompts y transcripciones viven fuera del repositorio.

## Instalación

Python 3.11 o posterior y [uv](https://docs.astral.sh/uv/).

```sh
uv sync --no-editable --extra dev
git config core.hooksPath .githooks
uv run --no-editable vbench --help
```

## Prueba local con datos ficticios

Crea un directorio externo a cualquier working tree Git. El ejemplo no consume APIs:

```sh
mkdir -p ../bench-private/scenarios
cp tests/fixtures/scenario.yaml ../bench-private/scenarios/
uv run --no-editable vbench extract --input tests/fixtures/mini_export.json --project project_1 --out ../bench-private/bundles
uv run --no-editable vbench run --data-dir ../bench-private --offline-demo --repetitions 1
```

`offline-demo` comprueba la instalación, persistencia y reportes. Sus respuestas son ficticias;
no demuestra capacidad, velocidad ni cumplimiento de un modelo real. Las pruebas automatizadas
sí ejercitan recorridos completos, validación de herramientas y manejo de errores.

## Datos privados y credenciales

Guarda una copia de `.env.example` en el directorio externo y complétala localmente.
No agregues exports ni escenarios reales al repositorio, ni siquiera renombrados.
El programa rechaza escribir bundles o resultados dentro de un working tree Git.

```sh
uv run --no-editable vbench extract --input /ruta/privada/export.json --project project_1 --out ../bench-private/bundles
uv run --no-editable vbench doctor --env-file ../bench-private/.env
uv run --no-editable vbench run --data-dir ../bench-private --dry-run
uv run --no-editable vbench run --data-dir ../bench-private --env-file ../bench-private/.env --projects project_1 --repetitions 1
```

Los escenarios se escriben en `../bench-private/scenarios/*.yaml`. Usa el ejemplo ficticio como
referencia del esquema. `tool_mocks`, reglas, variables y nombres internos quedan allí, privados.
El extractor acepta `--composition single_node` para agentes independientes por segmento.
Cada escenario de segmento declara `segment` y solo el de referencia usa `reference: true`.

## Conversaciones, nodos y herramientas

`active_node` **no es un benchmark de nodos aislados**. La conversación empieza en el nodo
inicial, conserva el historial completo, valida `route_node` y carga el nuevo prompt antes
de continuar. El modelo decide las transiciones; el evaluador nunca fuerza la ruta esperada.

`full` añade todos los prompts como prueba de estrés, manteniendo el estado y los permisos del
nodo activo. `subset` limita los nodos disponibles y registra si el recorrido sale del subconjunto.
Una conversación por segmento usa un solo nodo y no se duplica entre modos.

```sh
uv run --no-editable vbench run --data-dir ../bench-private --prompt-mode active_node,full --dry-run
uv run --no-editable vbench run --data-dir ../bench-private --all-segments --dry-run
```

Las herramientas usan mocks, nunca ejecutan handlers importados. El benchmark valida nombre,
argumentos JSON Schema, disponibilidad en el nodo, condiciones previas, expectativas por turno,
herramientas prohibidas y rutas esperadas. Mide primera emisión de herramienta, latencia del
mock y tiempo hasta texto después de las herramientas. Los errores simulados son configurables.
La latencia del mock **no es** la latencia de una integración externa real.

## Métricas y lectura del resultado

- `ttft_ms`: primer texto o delta de herramienta; excluye eventos vacíos y razonamiento separado.
- `first_text_ms`: primer texto del asistente en esa llamada; puede ser null si solo hay herramientas.
- `first_tool_ms`: primera emisión de herramienta.
- `turns[].first_text_ms`: espera desde el inicio del turno, incluyendo rondas de herramientas y reintentos.
- Latencia total, tokens/s, consumo, caché, razonamiento y costo por conversación y mil conversaciones.
- Herramientas inválidas/omitidas/prohibidas, reglas explícitas y señales de idioma distinto del español.

El texto visible en un canal de respuesta se considera texto aunque revele razonamiento; las
señales de idioma y reglas ayudan a detectarlo. Las reglas no implementadas no se consideran
aprobadas. La detección de idiomas es heurística y deja casos ambiguos como desconocidos.

Se guardan `manifest.json`, `calls.jsonl`, `runs.jsonl`, `transcripts/`, `summary.csv`, `REPORT.md` y `CONVERSATIONS.md`.
Este último permite revisar preguntas, respuestas y resultados simulados en orden.
La salida completa es **privada**. El sistema aplica permisos locales restrictivos y redacta
valores de credenciales del entorno. No publica reportes automáticamente.

```sh
uv run --no-editable vbench report --run-dir /ruta/privada/results/experimento
uv run --no-editable vbench compare --run-dir /ruta/privada/results/experimento --baseline gpt-4.1
```

La tabla principal excluye estrés, contexto elevado, concurrencia, protocolos de herramientas
en texto, reintentos y datos sintéticos. No mezcles escenarios distintos al elegir un modelo.
El hash canónico ayuda a verificar igualdad de instrucciones; el hash del request también
incluye el historial, que puede divergir entre modelos.

## Compatibilidad de contexto

La auditoría local compara el tamaño estimado de los prompts con la ventana configurada por
despliegue, reserva espacio para la salida y muestra evidencia de tamaños ya procesados:

```sh
uv run --no-editable vbench audit-context --data-dir ../bench-private --out ../bench-private/context-review --results-dir ../bench-private/results
```

No hace inferencias. Escribe `CONTEXT.md` y `context-audit.json` privados y contrasta
`active_node` con `full`; los segmentos independientes se mantienen separados. Los modelos
deshabilitados pueden incluirse explícitamente con `--models` para auditar sus límites sin llamarlos.

El historial se comprueba otra vez antes de cada llamada, incluso después de resultados grandes
de herramientas. No se recorta automáticamente. `skipped_context` también puede indicar que se
agotó el margen preventivo configurado, aunque no se haya alcanzado la ventana publicada.

El conteo entre modelos es aproximado. `usage` del proveedor permite documentar tamaños
procesados; no prueba por sí solo que la ventana completa funcione ni que el proveedor no haya
recortado internamente. La retención de información, las reglas y las tools se evalúan aparte.
Los límites de tokens por minuto de la cuenta tampoco equivalen a la ventana del modelo.

## Modelos, precios y gasto

El catálogo está en `config/models.yaml`; tarifas USD por millón de tokens en `config/pricing.yaml`.
Agregar otro despliegue compatible requiere una nueva entrada y su tarifa. Un modelo en otro
proveedor debe tener su propia clave: la latencia y el costo pertenecen al despliegue.

Antes de cualquier ejecución se imprime un preflight sin inferencia. Incluye una estimación de
gasto y una reserva conservadora usando máximos de contexto, llamadas y reintentos. No es una
cotización exacta: el historial, caché y ruta todavía no se conocen. Sobre USD 5 se pide confirmación
o `--yes`. Precios desconocidos bloquean inferencia. `doctor --online` realiza pruebas breves
pagables con un saludo ficticio; el modo predeterminado de doctor no llama APIs.

Las operaciones online requieren además un `budget.json` privado, junto al `.env`
o indicado con `--budget-file`. Ejemplo de presupuesto inicial para un piloto:

```json
{"limit_usd": 1, "max_operation_usd": 0.25, "reserved_usd": 0, "reservations": []}
```

El registro reserva una cota conservadora antes de llamar a proveedores, usando la
ventana de contexto completa, la salida máxima, las repeticiones, los reintentos y
el calentamiento. `--yes` no evita estos límites. Las reservas se escriben de forma
atómica. Una ejecución terminada libera solo capacidad de llamadas que no utilizó;
cada intento realizado, incluso fallido, conserva su cota completa sin descuento de caché.
El contador aumenta antes de iniciar la petición. Las ejecuciones interrumpidas y
los registros antiguos sin evidencia de finalización conservan su reserva original.
La conciliación conserva el historial y es idempotente. Estos montos siguen siendo
cotas prudenciales, no gasto facturado.
Para presupuestos independientes por modelo, agrega `models` al registro privado:

```json
{
  "limit_usd": 100,
  "max_operation_usd": 100,
  "reserved_usd": 0,
  "models": {
    "model-a": {"limit_usd": 25, "reserved_usd": 0},
    "model-b": {"limit_usd": 25, "reserved_usd": 0},
    "model-c": {"limit_usd": 25, "reserved_usd": 0},
    "model-d": {"limit_usd": 25, "reserved_usd": 0}
  },
  "reservations": []
}
```

Reemplaza los alias por las claves del catálogo. El preflight muestra la reserva por modelo,
incluyendo calentamiento y reintentos. Un modelo no puede utilizar el saldo de otro;
modelos sin presupuesto asignado se rechazan. Las reservas de varios modelos son atómicas.
El total reservado debe coincidir con la suma de los saldos reservados por modelo.
Al migrar un registro con consumo previo, conserva su historial y asigna también ese consumo.
No reinicies el registro entre ejecuciones y usa el mismo archivo para todos los
proveedores. Si ya hubo consumo, inclúyelo conservadoramente en `reserved_usd`.
La cota depende de precios y ventanas de contexto correctos; no controla consumos
externos a esta CLI, impuestos ni cambios de tarifas del proveedor.
`doctor --online --max-output-tokens 512` permite margen para modelos que razonan;
una respuesta truncada se informa como `output_limit`.

Para pilotos, `bench.yaml` permite `max_calls_per_conversation` como tope global
de llamadas por conversación, manteniendo las transiciones y el historial. El
plan de presupuesto usa ese mismo tope y la ejecución devuelve `call_limit` si
se agota. `min_request_interval_s` espacia solicitudes de cada modelo; esas
esperas se registran aparte como `rate_limit_wait_ms` y se excluyen de las
latencias de inferencia y de turno. Con cuota compartida entre modelos, usa
concurrencia 1 y evita otras ejecuciones simultáneas de la misma cuenta.

Gemma está deshabilitado inicialmente porque la oferta gratuita declara uso de datos para
mejorar productos. Revisa las condiciones del servicio antes de habilitarlo con datos privados.
Las verificaciones documentales y las pruebas de conectividad son distintas; estas últimas
requieren tus credenciales. Consulta [el catálogo verificado](docs/providers.md).

## Validación y límites

```sh
uv run --no-editable pytest -q
uv run --no-editable ruff check .
python3 scripts/check_public.py --staged
```

La v1 solo mide LLM. No incluye STT/TTS, handlers reales, bases de conocimiento, simulador de usuario
con LLM ni un juez semántico automático. Los guiones son deterministas; pueden declarar variantes
`content_by_node` para responder según el nodo alcanzado. Un camino fallido queda registrado.

Deduplicación desactivada por defecto: `--dedup` solo mueve párrafos exactos presentes una vez
en todos los nodos seleccionados, registra el experimento por separado y no promete equivalencia
semántica. Los conteos `o200k_base` son estimaciones entre modelos. Los cargos usan `usage` cuando
está disponible; no se inventan costos ni métricas para errores o saltos. Con pocas repeticiones,
p95 es exploratorio. El umbral de contexto del 85% es preventivo, no una degradación demostrada.

Consulta [seguridad](SECURITY.md) y [decisiones de diseño](docs/design.md).
