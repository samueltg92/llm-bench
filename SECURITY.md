# Publicación y datos privados

Este repositorio contiene exclusivamente código genérico, configuración pública y fixtures
ficticios. Los identificadores públicos siguen el patrón `project_1`, `project_2`, etc.
No se publica la correspondencia con organizaciones, clientes o nombres internos.

Los exports, prompts, bundles, escenarios de producción, transcripciones, resultados y claves
de API se guardan **fuera de cualquier working tree Git**. Renombrar un archivo privado no lo
convierte en un archivo publicable. No copiar el documento de requisitos original al repositorio.

El `.gitignore` aplica una lista permitida en la raíz; los hooks comprueban los bytes staged
antes del commit y todo el historial antes del push. CI repite el detector genérico. La verificación
no imprime el contenido encontrado. Los hooks se activan localmente:

```sh
git config core.hooksPath .githooks
git config bench.privateGuard /ruta/privada/publication-guard.json
```

El guard privado contiene `terms` (nombres prohibidos) y `corpus_files` (exports originales).
También admite `whole_word_terms` para siglas: detecta identificadores separados por espacios,
guiones o guiones bajos sin bloquear palabras o URLs que solo contienen esas letras.
Se compara el contenido público contra nombres y fragmentos del corpus sin copiar ese corpus
al repositorio. CI no recibe datos privados. Un guard configurado que no se puede leer bloquea
la revisión. La revisión local con corpus debe completarse antes de publicar.

Se pueden compartir nombres de modelos y resultados numéricos revisados y anonimizados.
Antes de publicar un resultado, revisar el archivo completo, metadatos, nombres de archivo,
rutas, tablas y etiquetas. Los datos crudos, prompts y transcripciones permanecen privados.
No publicar automáticamente el directorio de resultados ni la correspondencia de alias.

Los hooks pueden omitirse manualmente y el detector no garantiza descubrir todos los secretos
posibles. Revisa el diff staged y evita `git add -f` con archivos privados. No subas artefactos
de ejecuciones reales a Actions, issues o PRs. El flujo CI no publica artefactos de ejecución.

El programa no ejecuta código ni webhooks del export. Los clientes API se crean solo para una
ejecución explícita o `doctor --online`. Las credenciales se leen de variables de entorno o de un
`.env` externo. Los errores del proveedor no se imprimen completos porque pueden incluir entradas
o cabeceras. Los archivos privados usan permisos 0600 y directorios creados por la aplicación 0700.

Los prompts enviados durante una prueba real quedan sujetos a las condiciones del proveedor.
Evita endpoints o niveles de servicio incompatibles con la confidencialidad de tus datos.
