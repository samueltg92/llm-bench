# Verificación documental del catálogo

Revisión: 2026-09-12. Los precios son USD por millón de tokens, no cargos medidos.
La disponibilidad para una cuenta se comprueba por separado con credenciales.

| Despliegue | ID | Contexto documentado | Entrada / salida | Caché entrada |
| --- | --- | --- | --- | --- |
| Mistral | mistral-small-2603 | 256k | 0.15 / 0.60 | Sin tarifa configurada |
| Groq | openai/gpt-oss-120b | 131072 | 0.15 / 0.60 | 0.075 |
| Google | gemma-4-31b-it | 256k (familia mediana) | Gratis en nivel gratuito | Gratis |
| Z.ai | glm-5.3-flash | 1M | 0.15 / 0.50 | 0.03 |

Fuentes oficiales:

- [Mistral Small 4](https://docs.mistral.ai/models/mistral-small-4-0-26-03): ID, contexto, capacidades y tarifas.
- [Groq GPT-OSS-120B](https://console.groq.com/docs/model/openai/gpt-oss-120b): ID, contexto, herramientas y tarifas.
- [Gemma en Gemini API](https://ai.google.dev/gemma/docs/core/gemma_on_gemini_api): modelo alojado, sistema, streaming y herramientas.
- [Gemma 4](https://ai.google.dev/gemma/docs/core): contexto de los modelos medianos.
- [Tarifas de Google](https://ai.google.dev/gemini-api/docs/pricing): oferta gratuita y tratamiento de datos.
- [GLM-5.3-Flash](https://docs.z.ai/guides/vlm/glm-5.3-flash): ID, 1M de contexto, streaming y herramientas. El razonamiento no se puede desactivar.
- [Tarifas de Z.ai](https://docs.z.ai/guides/overview/pricing): entrada, salida y caché. Esta variante Flash tiene costo.

La oferta alojada de Gemma indica uso de datos para mejorar productos en el nivel gratuito.
Está deshabilitada inicialmente; habilitarla requiere evaluar las condiciones para los datos
que se enviarán. No se usa automáticamente un modelo distinto cuando falta una credencial.

GPT-4.1 es una referencia opcional deshabilitada, con precio pendiente. Agrega otra entrada si
necesitas comparar el mismo modelo en otro proveedor: registra su endpoint, tarifas y opciones.
