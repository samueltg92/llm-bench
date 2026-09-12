# Decisiones del benchmark

## Unidad experimental

Una repetición es una conversación completa. Cada intervención puede generar varias llamadas
al LLM y a herramientas. El estado contiene nodo activo, historial y variables del escenario.
Las transiciones se validan contra el nodo vigente, nunca contra la unión global de destinos.
No se fuerza una transición para completar el camino esperado.

Los proyectos con segmentos independientes declaran un segmento por escenario. Los proyectos
con grafo conservan el recorrido tanto en modo active_node como full. El subconjunto limita
contexto, pero no sustituye silenciosamente una conversación completa: se registra su modo.

## Comparabilidad

Se registran hash del corpus, escenario, sistema canónico y request adaptado, modelo y proveedor,
parámetros de razonamiento, temperatura, salida máxima, deduplicación, modo de herramientas,
concurrencia y reintentos. Los historiales generados pueden variar: misma semilla de conversación
no significa requests idénticos durante toda la conversación.

Medimos con perf_counter_ns en un único runner. El primer evento vacío, usage y razonamiento
separado no activan TTFT. Cada cliente persiste por modelo durante todo el experimento. Los SDK
no realizan reintentos ocultos; el runner conserva cada intento y excluye repeticiones afectadas
del ranking principal. Cargos desconocidos de intentos fallidos impiden afirmar un gasto total.

## Evaluación

La sintaxis válida no implica una herramienta correcta. Cada escenario puede establecer:

- Herramienta esperada y subconjunto de argumentos, con mínimos y máximos de invocaciones.
- Herramientas prohibidas, condiciones previas basadas en estado y nodo final por turno.
- Hitos ordenados del recorrido y reglas de texto requeridas, prohibidas o de longitud.
- Herramienta que debe ejecutarse antes de cualquier texto de un turno concreto.

Un resultado sin expectativas definidas tiene cumplimiento desconocido, no 100%. Un turno
no alcanzado no elimina expectativas obligatorias del denominador. Los mocks pueden simular
errores y actualizaciones de estado. Los handlers importados nunca se ejecutan.

El detector de idioma identifica señales de varios idiomas y guarda offsets y confianza en
archivos privados. No traduce ni modifica respuestas para mejorar artificialmente el resultado.

## Contexto y costos

Se verifica cada llamada incluyendo historial, schemas de herramientas y reserva de salida.
El 85% de ventana es una etiqueta preventiva. Sobre el 95% se omite, se intenta un subconjunto
declarado o se marca un fallo según la configuración. Estos porcentajes no demuestran pérdida
de calidad y los tokens estimados pueden diferir del tokenizador del proveedor.

La deduplicación es un experimento opcional y separado. No extiende a todos los nodos una regla
presente en solo el 80%. No exige reproducir números históricos incompatibles: el índice separa
sistema global, prompts de nodos y herramientas; el preflight mide la composición realmente enviada.

Los costos distinguen tokens cacheados y razonamiento incluido o adicional. Precio desconocido
produce null. El informe informa el gasto conocido y advierte si falta contabilizar intentos.

## Orden de trabajo y ejecución

1. Código genérico, almacenamiento externo y controles de publicación.
2. Parser, composición, escenarios privados y evaluación explícita.
3. Adaptadores, medición, costos y reportes.
4. Pruebas locales con fixtures ficticios y extracción privada de datos reales.
5. Preflight, credenciales y prueba pequeña de una conversación.
6. Comparación del flujo completo con active_node y segmentos independientes.
7. Estrés full y deduplicación opcional, reportados por separado.
