# Reconocimiento de palabras clave

El dueño crea una sola palabra correcta, sus mensajes y sus recordatorios en
«Palabras clave». Las variantes se reconocen en el bot, sin listas de alias,
migraciones, llamadas al modelo ni dependencias adicionales.

## Orden de reconocimiento

1. Coincidencia histórica del mensaje completo: minúsculas y espacios normalizados.
   Siempre gana, incluso mientras existan variantes guardadas como entradas distintas.
2. Coincidencia completa quitando únicamente puntuación Unicode exterior y espacios
   de los extremos. Se conservan tildes, `ñ`, símbolos como emojis y signos internos.
   Si varias filas quedan iguales tras quitar puntuación, no se elige ninguna.
3. Para palabras individuales alfabéticas de cinco letras o más, comparación de sus
   formas simples: palabra terminada en vocal más `s`, o palabra terminada en vocal
   más `s` sin esa `s`, si quedan al menos cinco letras. No es un analizador general
   de singular/plural: no transforma plurales en `es` ni resuelve cambios de acento.
4. Distancia de Levenshtein mínima entre esas formas: se permite una inserción,
   eliminación o sustitución. La implementación acotada devuelve 0, 1 o 2 (dos o más)
   y recorre las letras en tiempo lineal. No acepta transposiciones como un solo cambio.
5. Se selecciona únicamente la fila con menor distancia si es 0 o 1. Un empate
   entre filas no dispara el flujo; la conversación sigue su tratamiento habitual.

Los pasos 3 y 4 se evalúan juntos: equivalencia singular/plural tiene distancia 0,
y un error sobre cualquiera de esas formas tiene distancia 1. Una coincidencia del
paso 2 gana a ambas. Una variante devuelve el **id y nombre originales**, por lo que
no crea registros ni tareas con la falta de ortografía como palabra nueva.

| Una sola entrada `tareas` | Resultado |
| --- | --- |
| `TAREAS`, ` ¡tareas! `, `tareas.` | Misma entrada por normalización |
| `tarea` | Misma entrada por forma simple |
| `tares`, `terea`, `tereas` | Misma entrada por un cambio sobre sus formas |
| `quiero tareas`, `no quiero tareas` | No activa esta palabra clave |
| `/tareas`, `ta.reas`, `t@reas`, `😀tareas` | No activa esta palabra clave |
| `mareas` | Puede activar `tareas`: también está a una letra |

La distancia mide parecido, no intención. Por eso no garantiza cero falsas
activaciones. Las palabras menores de cinco letras solo admiten coincidencia exacta
o puntuación exterior, y las frases configuradas conservan coincidencia completa
sin tolerancia a errores. Los comandos que empiezan por `/` solo pueden coincidir
por el comportamiento exacto previo. La tolerancia limita la entrada a 61 caracteres
(máximo guardado de 60 más una posible `s`).

## Compatibilidad

- Solo participan palabras activas del proyecto actual; se conserva la caché de
  30 segundos. Si la base falla, el mensaje sigue hacia el comportamiento normal.
- Se mantienen los mensajes, adjuntos, registros, facturación y tareas existentes.
  Los bloqueos permanentes siguen impidiendo activar el flujo, y las palabras clave
  siguen teniendo prioridad sobre las pausas temporales.
- No se borran, renombran ni fusionan filas. El dueño puede limpiar variantes tras
  desplegar, conservando la entrada correcta y comprobando antes sus mensajes y tiempos.
- Borrar una entrada conserva el comportamiento previo: borra sus piezas asociadas;
  sus recordatorios pendientes no se transfieren a la entrada que se conserva.

## Validación

Ejecutar en las imágenes de desarrollo de Docker, con las suites deterministas
(sin `RUN_LLM_TESTS=1`):

```bash
docker compose -f docker-compose.local.yml run --rm bot_agent pytest
docker compose -f docker-compose.local.yml run --rm dashboard pytest
```

Las pruebas cubren la distancia contra una implementación completa de Levenshtein,
las variantes, las prioridades, los empates, los comandos, el aislamiento por
proyecto y la caducidad de caché. Las regresiones conectan el buscador real con el
orquestador en WhatsApp y Telegram, verificando identidad, adjuntos, una sola
programación, facturación y respeto de bloqueos. El dashboard usa su base `_test`,
separada de los datos operativos.

## GitHub y VPS

El cambio se publica en una rama con PR hacia `main`. Después de fusionarlo,
actualizar el checkout del VPS desde la raíz del proyecto:

```bash
git switch main
git pull --ff-only origin main
docker compose up -d --build --no-deps bot_agent whatsapp_webhook celery_worker dashboard
```

En EasyPanel, hacer el redeploy equivalente desde el commit fusionado. El compose
de producción copia el código en las imágenes: reiniciar sin reconstruir no aplica
este cambio. Conservar `.env`, los volúmenes y los servicios de datos. No usar
`down -v`, purgar Redis ni ejecutar los tests sobre la base operativa del VPS.

Tras desplegar, comprobar en un contacto de prueba una palabra correcta y una
variante, el mensaje recibido y sus recordatorios. Comprobar también una frase
normal y los registros del bot. Eliminar las variantes existentes es una operación
manual posterior del dueño, no un requisito para desplegar.

Para revertir, crear un commit de reversión del cambio (`git revert <commit>`),
publicarlo en GitHub y reconstruir los mismos servicios desde esa versión. No hay
migraciones que deshacer. La reversión del código no restaura entradas que el dueño
haya borrado manualmente.
