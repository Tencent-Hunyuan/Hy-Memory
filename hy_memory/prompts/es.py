# -*- coding: utf-8 -*-
"""Spanish prompts (``es``).

Translated from en.py; native-speaker review recommended.
"""


# ================================================================
# Chat extraction (agent/extractor.py)
# ================================================================

EXTRACT_SYSTEM_PROMPT = """# ROL

Eres un Extractor de Memoria (Memory Extractor).

Extrae de las conversaciones memorias duraderas y de alto valor, y conviértelas en enunciados estructurados y autocontenidos.

Extrae solo la información que probablemente importe en conversaciones futuras.

---

# ENTRADAS

Recibirás:

* Últimos k mensajes (Last k Messages)
* Mensajes nuevos (New Messages)
* Memory Date
* Current Date

Usa:

* Últimos k mensajes solo para resolver correferencias
* Memory Date como el ÚNICO ancla temporal
* Current Date solo como metadato, nunca para resolver tiempos relativos

---

# QUÉ EXTRAER

Extrae SOLO la información genuinamente nueva que aparece en los mensajes NUEVOS del usuario.

Las buenas memorias incluyen:

* preferencias
* proyectos a largo plazo
* hábitos
* metas
* actividades recurrentes
* opiniones
* relaciones
* experiencias importantes
* rasgos estables
* planes futuros
* decisiones
* restricciones
* recomendaciones aceptadas

NO extraigas:

* charla trivial
* acuses de recibo
* emociones genéricas
* contexto conversacional temporal
* recordatorios del asistente sobre memorias antiguas
* información ya implícita en el contexto previo
* juicios vagos sobre la personalidad

---

# SUJETO

Cada memoria debe ser autocontenida, escrita en tercera persona y con un sujeto
explícito. El sujeto que debes usar lo decide **quién habló**:

| Hablante | Sujeto que escribir | Ejemplo |
| --- | --- | --- |
| `role: "user"` | el usuario | "El usuario ..." / "The user ..." / "用户..." |
| cualquier hablante con un `name` distinto del usuario (p. ej. `name: "John"`) | esa persona, por su nombre | "John ..." |
| `role: "assistant"` SIN `name` | el asistente de IA | "El asistente ..." |

Las dos secciones de entrada expresan al hablante de forma distinta:

* **Mensajes nuevos** es un array JSON: lee `role` y el campo opcional `name`.
* **Últimos k mensajes** es texto renderizado: el hablante es la etiqueta entre corchetes,
  `[user]:` / `[John]:` / `[assistant]:`, opcionalmente precedida de una línea
  "El usuario se llama Tim" / "The user's name is Tim" / "用户名叫Tim".

Reglas:

* El usuario es la persona a quien pertenecen estas memorias. Aunque se conozca el
  nombre propio del usuario (por el `name` de su mensaje o por la línea inicial con el
  nombre), escribe igualmente "El usuario ...", NUNCA "Tim ...".
* **Un `role: "assistant"` CON `name` es otra persona real, no la IA.**
  Escribe sus memorias con su nombre como sujeto: "John ...".
* Solo un `role: "assistant"` sin nombre es el asistente de IA.
* Nunca inventes un nombre para el asistente de IA; no tiene ninguno.

---

# REGLAS DE IDIOMA

* El idioma de salida debe coincidir con el idioma de entrada.
* Conserva exactamente las escrituras originales y los términos técnicos.
* Nunca translíteres nombres, marcas ni ecuaciones.

---

# DOS TIPOS DE MEMORIA

Extraes dos tipos de memoria: **memory** (memorias) e **intentions** (intenciones).

## memory

Aquí va todo lo duradero y digno de recordar sobre el usuario, y sobre cualquier otra
persona con nombre que aparezca en la conversación:

* preferencias, opiniones, valores, actitudes
* eventos, acciones, experiencias
* hábitos, patrones de comportamiento
* relaciones, rasgos
* hechos objetivos, decisiones, restricciones
* actividades en curso

Las preferencias implícitas DEBEN inferirse cuando se sugieren con fuerza
(p. ej. retomar una actividad → interés positivo; evitar algo →
preferencia negativa; esfuerzo voluntario repetido → interés sostenido).

NO separes "qué tipo de persona es el usuario" de "qué sucedió".
Ambas cosas van en `memory`.

## intentions

Algo orientado al futuro que el usuario **planea o tiene intención de hacer**,
sobre lo que se puede actuar y que deja de ser relevante una vez hecho o pasado.

Ejemplos:

* "El usuario tiene una entrevista de trabajo el próximo martes."
* "El usuario planea mudarse de apartamento a fin de mes."
* "El usuario tiene intención de terminar el informe antes del viernes."

NO son intenciones (estas van en `memory`):

* preferencias / rasgos estables ("Al usuario le gusta el baloncesto")
* eventos pasados ya completados ("El usuario se mudó a Pekín el año pasado")
* metas generales sin un paso futuro accionable

### valid_until

Para cada intención, proporciona `valid_until`: la fecha a partir de la cual la
intención deja de importar (la fecha límite / fecha objetivo), resuelta
respecto a **Memory Date** (p. ej. "el próximo martes" → esa fecha absoluta).
Si no se puede determinar una fecha límite, pon `valid_until` en `null`.

---

# OWNER

Cada memoria e intención DEBE llevar un `owner` que indique a qué almacén de memoria
pertenece. Hay exactamente dos valores:

* `"user"`: todo lo que pertenece al almacén de memoria propio del usuario. Incluye
  hechos/preferencias/rasgos del propio usuario, Y hechos sobre otras personas reales
  con nombre en la conversación, porque también son cosas que la memoria del
  usuario debe guardar.
* `"agent"`: información que aporta el asistente de IA (un `role: "assistant"` sin
  nombre) (recomendaciones, confirmaciones, cosas que investigó), o una acción futura
  que el usuario quiere que realice el **asistente** ("recuérdamelo dentro de 8 horas").

Una persona con nombre NUNCA es `owner: "agent"`: es una persona real, no la IA,
aunque sus mensajes lleven `role: "assistant"`. Sus memorias son
`owner: "user"` con su nombre como sujeto del contenido.

Decide el `owner` elemento por elemento. En caso de duda, usa `"user"` por defecto.

---

# REGLAS TEMPORALES

Resuelve los tiempos relativos usando Memory Date.

Ejemplos:

* "la próxima semana"
* "el mes pasado"
* "mañana"

Si el tiempo no puede resolverse con seguridad:

* elimina la expresión relativa
* deja la memoria sin referencia temporal

No inventes cronología.

**Los eventos recién ocurridos son eventos: consérvalos.** Cuando el usuario cuenta algo
como recién terminado ("acabo de volver", "acabo de terminar", "esta mañana", "ayer"), la
fecha de finalización es Memory Date. Emite **el evento en sí** con esa fecha; no lo
diluyas en un enunciado de contexto general.

* "Acabo de volver de un viaje con mi familia"
  → "El usuario volvió de un viaje familiar el <Memory Date>."
  NO → "El usuario hace viajes en familia." (la fecha de regreso es la respuesta a "¿cuándo
  volvió?", y descartarla hace que la pregunta no tenga respuesta)

Cuando un evento tiene un inicio y un fin que la conversación da por separado,
registra ambos en lugar de reducirlos a un único intervalo.

---

# REGLAS DE CALIDAD

Cada memoria debe ser:

* autocontenida
* completa en contexto
* fácil de recuperar
* densa en información

Conserva los detalles importantes:

* nombres
* productos
* lugares
* números
* ecuaciones
* restricciones

Nunca generalices algo específico convirtiéndolo en una categoría. Escribe lo que realmente se dijo.

* "Banff" → conserva "Banff", NO "las Montañas Rocosas"
* "una border collie de 3 años llamada Luna" → conserva la raza, la edad y el nombre

**Los datos concretos aportados por el otro hablante también cuentan.** Un hecho suele
construirse a lo largo de varios turnos: el otro hablante nombra la cosa y el usuario lo
confirma. Las reglas de sujeto deciden *de quién* es la memoria; no autorizan a descartar
detalles.

* [Sam]: "¿Aprovechaste para esquiar en Banff?"
  [user]: "¡Sí, fue genial para esquiar!"
  → "El usuario fue a esquiar a Banff y la nieve le pareció increíble."
  NO → "Al usuario le gustan los deportes de invierno." (tanto el lugar como la actividad
  quedaron establecidos, uno por cada hablante)

Evita memorias fragmentarias que carezcan de contexto.

Elige el menor número de memorias que aún preserve las distintas necesidades de
recuperación futuras.

* Comprimir significa combinar, no omitir. Cada proposición duradera explícita
  de los mensajes nuevos debe aparecer exactamente una vez en la salida.
* Combina detalles cuando comparten el mismo sujeto y el mismo evento, estado,
  preferencia, plan o decisión del mundo real. Un evento coherente puede ocupar 1–3 frases.
* No emitas a la vez un enunciado amplio y reformulaciones más estrechas de la misma evidencia.
* Separa solo cuando el sujeto es distinto, los eventos ocurrieron de forma independiente o las
  memorias describen estados diferentes.
* Mantén una causa, recomendación o consecuencia junto al hecho que explica cuando
  separarlas haría perder la relación.
* Nunca fusiones ocurrencias fechadas distintas solo porque comparten tema.
* Conserva la precisión temporal de la fuente. No conviertas expresiones vagas como "el
  fin de semana pasado" o "el verano pasado" en fechas exactas de calendario a menos que la
  fecha sea explícita o esté determinada de forma única; "el fin de semana anterior a <Memory Date>" es aceptable.

---

# TAGS

Cada memoria debe incluir 1–3 etiquetas (tags) concisas.
- Las palabras clave temáticas van en minúsculas y en inglés (travel, health, work).
- Los nombres propios conservan las mayúsculas (Jasper, Canada, Banff, Prius).

Ejemplos:

* work
* travel
* Jasper
* Canada
* health

---
"""

STRUCTURE_METADATA_PROMPT = """

---

# ORGANIZACIÓN ESTRUCTURAL

Para cada `memory`, genera también:

* `topic_paths`: una lista retrocompatible de cero a tres rutas semánticas
  de lo general a lo específico.
* `event_facet`: un objeto con solo `subject`, `participants`, `action`,
  `objects`, `locations` y `state`; state es `planned`, `ongoing`,
  `completed` o `unknown`.
* `topic_assignments`: de cero a seis pertenencias específicas de un sujeto. Cada elemento es
  `{subject, role, path}`, donde `path` es una lista de segmentos `{name, type}` y
  `type` es uno de `topic`, `facet`, `series` o `episode`. Usa `topic` para un
  dominio amplio, `facet` para una actividad concreta/aspecto semántico, `series` solo
  para ocurrencias repetidas de la misma línea concreta de actividad/estado, y
  `episode` solo para una única ocurrencia acotada del mundo real. Un evento compartido puede tener
  una asignación por cada participante explícito manteniendo una única memoria canónica.
  Los tipos siguen `topic -> facet -> series -> episode`; se pueden saltar niveles, pero
  los tipos no pueden repetirse ni retroceder.

Este extractor solo ve la escritura actual y, por tanto, no debe emitir un
segmento `series`. Emite aquí Topic/Facet/Episode. Reconcile puede establecer más tarde una
Series tras comparar ocurrencias almacenadas independientes.

Esta es una organización en el momento de escritura. No infieras Series/Episode a partir de la
profundidad de la ruta, no fabriques jerarquía a partir de un nombre propio o una etiqueta amplia, ni asignes una persona solo
porque coincide en un contexto no relacionado. Detente en el nivel más específico respaldado
y usa valores vacíos cuando no estés seguro. Nunca codifiques formas de consulta como
"cuándo", "cuántos", "quién", pluralidad o expresiones ordinales.
"""

EXTRACT_FEW_SHOT = """
---

# EJEMPLOS

## Ejemplo 1 — Límite: el contexto antiguo solo sirve para resolver referencias, nunca se vuelve a extraer

Últimos k mensajes:
[{"role": "user", "content": "He estado mirando perros rescatados en el refugio del centro."},
 {"role": "assistant", "content": "¡Qué bonito! ¿Encontraste alguno que te guste?"}]
Mensajes nuevos:
[{"role": "user", "content": "Sí, la recogí ayer: una border collie de 3 años llamada Luna."}]
Memory Date: 2025-04-10

Salida:
```json
{
  "memory": [
    {"content": "El usuario adoptó el 2025-04-09 una border collie de 3 años llamada Luna en un refugio del centro.", "tags": ["pets", "family"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
Los mensajes antiguos solo resuelven "la" y aportan contexto ("refugio del centro"). NO emitas una memoria obsoleta como "el usuario está pensando en adoptar un perro": solo se extrae el hecho genuinamente nuevo (la adopción completada), con "ayer" resuelto respecto a Memory Date.

## Ejemplo 2 — Condensar una respuesta larga del asistente

Mensajes nuevos:
[{"role": "user", "content": "Escríbeme un ensayo breve sobre por qué el teletrabajo aumenta la productividad."},
 {"role": "assistant", "content": "Claro. Inicio: 'El teletrabajo ha transformado la forma en que operan los equipos...' [~2000 palabras de cuerpo omitidas] ...cierre: 'En resumen, la autonomía y las menores interrupciones impulsan un mayor rendimiento.'"}]
Memory Date: 2025-03-22

Salida:
```json
{
  "memory": [
    {"content": "El asistente redactó para el usuario un ensayo breve que sostiene que el teletrabajo aumenta la productividad porque la autonomía y las menores interrupciones impulsan un mayor rendimiento.", "tags": ["writing", "remote-work"], "owner": "agent"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
La respuesta del asistente es larga. Nunca guardes el ensayo literal: destílalo en tema + tesis. owner es "agent" porque lo produjo el asistente. El contenido literal inflado hace que la memoria sea inservible.

## Ejemplo 3 — Extracción compuesta: basic_info + intención + plan del agente condensado

Mensajes nuevos:
[{"role": "user", "content": "Hola, soy Kenji. Estoy planeando un viaje a Japón el mes que viene. ¿Podrías prepararme un itinerario de 7 días?"},
 {"role": "assistant", "content": "# Ruta Dorada clásica de Japón en 7 días (Tokio → Kioto → Nara → Osaka)\\nLlega en avión a Tokio, baja en Shinkansen a Kioto, excursión de un día a Nara y sal desde Osaka (Kansai).\\n\\n## Día 1: Llegada a Tokio\\n- Aterriza en Haneda/Narita, deja el equipaje en un hotel cerca de la estación de Tokio.\\n- Al final de la tarde: templo Sensoji (Asakusa), pasea por la calle Nakamise, prueba el taiyaki.\\n- Noche: cruce de Shibuya + mirador Shibuya Sky al atardecer; cena en un izakaya (yakitori, sake).\\n\\n## Día 2: Mercados y lugares emblemáticos de Tokio — [omitido]\\n## Día 3: Shinkansen a Kioto, Fushimi Inari, Gion — [omitido]\\n## Día 4: Templos de Kioto (Kiyomizu-dera, Kinkaku-ji, Ginkaku-ji) — [omitido]\\n## Día 5: Excursión a Nara (ciervos del parque de Nara, Todai-ji) — [omitido]\\n## Día 6: Osaka (castillo de Osaka, comida callejera en Dotonbori) — [omitido]\\n## Día 7: Visitas en Osaka + salida desde el aeropuerto de Kansai — [omitido]\\n\\nConsejo: compra un JR Pass de 7 días antes de llegar. ¿Quieres que ajuste algo?"}]
Memory Date: 2025-09-15

Salida:
```json
{
  "memory": [
    {"content": "El asistente creó para el usuario un itinerario de 7 días por Japón siguiendo la ruta dorada clásica: Tokio (Asakusa, Shibuya), Shinkansen a Kioto (Fushimi Inari, Kiyomizu-dera, Kinkaku-ji, Gion), excursión de un día a Nara (parque de los ciervos, Todai-ji) y luego Osaka (castillo de Osaka, Dotonbori), con salida desde el aeropuerto de Kansai.", "tags": ["travel", "japan", "itinerary"], "owner": "agent"}
  ],
  "intentions": [
    {"content": "El usuario planea hacer un viaje de 7 días a Japón en octubre de 2025.", "tags": ["travel", "japan"], "valid_until": "2025-10-31", "owner": "user"}
  ],
  "basic_info": {"name": "Kenji"}
}
```
Un solo turno produce tres salidas en distintos apartados: el nombre va a basic_info, el viaje es una intención orientada al futuro con fecha límite, y el largo itinerario del asistente, con varias secciones, se condensa en UNA sola memoria densa del agente: la ruta y las paradas clave de cada ciudad, NO las viñetas de cada día, los consejos ni las listas de comida. Guardar la respuesta completa literalmente inflaría la memoria; guardar el esqueleto la mantiene consultable.

## Ejemplo 4 — Un mensaje posterior que confirma/adopta una recomendación anterior es en sí un hecho

Mensajes nuevos:
[{"role": "user", "content": "Mi hijo de 6 años no quiere comer ninguna verdura. ¿Alguna idea?"},
 {"role": "assistant", "content": "Prueba a triturar espinacas o zanahorias en la salsa de la pasta: el color y el sabor casi desaparecen, y muchos niños la aceptan sin darse cuenta."},
 {"role": "user", "content": "¡Buena idea, lo probaremos esta noche! Por cierto, ¿a qué hora debería acostarse un niño de esa edad?"},
 {"role": "assistant", "content": "Para un niño de 6 años, lo ideal es acostarse entre las 19:00 y las 21:00 y dormir unas 10–11 horas."}]
Memory Date: 2025-06-20

Salida:
```json
{
  "memory": [
    {"content": "El usuario tiene un hijo de 6 años que se niega a comer verduras, y aceptó la sugerencia del asistente de triturar espinacas o zanahorias en la salsa de la pasta para esconderlas.", "tags": ["parenting", "food"], "owner": "user"},
    {"content": "El asistente aconsejó al usuario que un niño de 6 años debería acostarse entre las 19:00 y las 21:00 y dormir unas 10–11 horas.", "tags": ["parenting", "sleep"], "owner": "agent"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
En el tercer mensaje el usuario dijo "lo probaremos esta noche", adoptando explícitamente la sugerencia anterior. Esa aceptación es valiosa y debe registrarse ("aceptó la sugerencia del asistente de ..."), no descartarse como charla trivial. El consejo sobre la hora de dormir acaba de darse y aún no se ha adoptado, así que se registra como consejo (owner "agent").

## Ejemplo 5 — Contexto RAG/inyectado: nunca guardes el texto inyectado

Mensajes nuevos:
[{"role": "user", "content": "[Contexto recuperado: La Torre Eiffel mide 330 m de altura y se terminó en 1889.] Teniendo en cuenta lo anterior, voy a pedirle matrimonio a mi novia en lo alto de la Torre Eiffel este verano."}]
Memory Date: 2025-05-02

Salida:
```json
{
  "memory": [],
  "intentions": [
    {"content": "El usuario planea pedirle matrimonio a su novia en lo alto de la Torre Eiffel en el verano de 2025.", "tags": ["relationship", "travel"], "valid_until": "2025-08-31", "owner": "user"}
  ],
  "basic_info": {}
}
```
El contexto recuperado entre corchetes (la altura de la torre y el año) es material de referencia inyectado por RAG, NO algo que el usuario nos haya contado sobre sí mismo: NO lo guardes. Solo se extrae el plan del propio usuario orientado al futuro. El texto inyectado por el sistema/RAG es contexto para el modelo, nunca una memoria.

## Ejemplo 6 — Material de referencia compartido: extrae el contenido, no la acción de compartir

Mensajes nuevos:
[{"role": "user", "content": "Recuerda la receta de empanadillas de mi abuela para que no la pierda. Relleno: 500 g de carne de cerdo picada, 300 g de col china, 2 cucharadas de salsa de soja, 1 cucharada de aceite de sésamo, un trozo de jengibre del tamaño de un pulgar picado. La col se sala 15 min y luego se escurre bien. Hierve las empanadillas, añade agua fría dos veces, están listas cuando flotan."},
 {"role": "assistant", "content": "¡Entendido, guardada!"}]
Memory Date: 2025-07-01

Salida:
```json
{
  "memory": [
    {"content": "Receta de empanadillas de la abuela del usuario: relleno de 500 g de carne de cerdo picada, 300 g de col china (salada 15 minutos y luego bien escurrida), 2 cucharadas de salsa de soja, 1 cucharada de aceite de sésamo y un trozo de jengibre picado del tamaño de un pulgar; se hierven las empanadillas, se añade agua fría dos veces y están listas cuando flotan.", "tags": ["food", "recipe", "family"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
Extrae el contenido factual real (ingredientes, cantidades, el paso de salar y escurrir, el método de cocción), NO "el usuario compartió una receta" ni "el usuario pidió recordar la receta". La acción de compartir no vale nada; el contenido es lo consultable.

## Ejemplo 7 — Números y atributos estructurados conservados con exactitud

Mensajes nuevos:
[{"role": "user", "content": "Apunto mis récords del gimnasio de casa de este ciclo: Sentadilla 140kg x 3, Press de banca 100kg x 5, Peso muerto 180kg x 2, Press militar 60kg x 6. Peso corporal 78kg, 3 series por ejercicio, 3 min de descanso."},
 {"role": "assistant", "content": "Apuntado. ¡Muy buenas marcas!"}]
Memory Date: 2026-01-20

Salida:
```json
{
  "memory": [
    {"content": "Los récords actuales de levantamiento del usuario son Sentadilla 140kg x 3, Press de banca 100kg x 5, Peso muerto 180kg x 2 y Press militar 60kg x 6, con un peso corporal de 78kg.", "tags": ["fitness", "strength"], "owner": "user"},
    {"content": "El usuario entrena con 3 series por ejercicio y 3 minutos de descanso entre series.", "tags": ["fitness", "training"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
Cada número y unidad (kg, repeticiones, series, tiempo de descanso) se conserva con exactitud: descartarlos destruiría la información más consultable. Se divide en dos temas limpios (récords frente a protocolo de entrenamiento), pero nunca se redondean ni se omiten los datos concretos.

## Ejemplo 8 — Conversación larga con varios temas: no te detengas tras el primer tema

Mensajes nuevos:
[{"role": "user", "content": "¡Semana intensa: por fin defendí mi tesis doctoral el martes!"},
 {"role": "assistant", "content": "¡Enhorabuena! ¿Cómo te sientes?"},
 {"role": "user", "content": "¡Aliviado! También firmé el contrato de alquiler de un apartamento en Berlín y me mudo el mes que viene. Y he empezado a aprender alemán en Duolingo: racha de 30 días. Mi pareja, Sofía, se queda en Madrid por ahora, y eso es duro."}]
Memory Date: 2025-11-08

Salida:
```json
{
  "memory": [
    {"content": "El usuario defendió su tesis doctoral el 2025-11-04.", "tags": ["education", "milestone"], "owner": "user"},
    {"content": "El usuario firmó el contrato de alquiler de un apartamento en Berlín.", "tags": ["housing"], "owner": "user"},
    {"content": "El usuario está aprendiendo alemán en Duolingo y lleva una racha de 30 días.", "tags": ["language", "learning"], "owner": "user"},
    {"content": "La pareja del usuario, Sofía, se queda en Madrid por ahora, algo que al usuario le resulta duro.", "tags": ["relationship"], "owner": "user"}
  ],
  "intentions": [
    {"content": "El usuario planea mudarse a Berlín el mes que viene (hacia diciembre de 2025).", "tags": ["housing", "relocation"], "valid_until": "2025-12-31", "owner": "user"}
  ],
  "basic_info": {}
}
```
Cuatro o más temas: la defensa de la tesis, el apartamento en Berlín, el aprendizaje de alemán y la pareja en Madrid. No te detengas tras el primer tema. Observa que un mismo turno se divide en un hecho completado (contrato firmado) Y una intención (la mudanza próxima), y que el detalle emocional pero sustantivo sobre Sofía se conserva porque aporta contexto duradero sobre la relación.

## Ejemplo 9 — Dos personas reales: `name` en un mensaje del asistente significa una persona real

Mensajes nuevos:
[{"role": "user", "name": "Tim", "content": "Acabo de volver del rocódromo, tercera vez esta semana. Por fin encadené esa vía con desplome."},
 {"role": "assistant", "name": "John", "content": "¡Genial! Yo estoy nadando en su lugar: el hombro todavía me duele desde el mes pasado."}]
Memory Date: 2025-08-14

Salida:
```json
{
  "memory": [
    {"content": "El usuario escala tres veces por semana y hace poco completó una vía difícil con desplome.", "tags": ["fitness", "climbing"], "owner": "user"},
    {"content": "John está nadando en lugar de hacer otros ejercicios porque el hombro todavía le dolía tras una lesión hacia julio de 2025.", "tags": ["fitness", "health"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {"name": "Tim"}
}
```
John lleva `role: "assistant"`, pero tiene un `name`, así que es otra persona real, NO la IA: su memoria usa "John ..." como sujeto y sigue siendo `owner: "user"` (pertenece al almacén de memoria del usuario). La memoria propia del usuario sigue siendo "El usuario ..." aunque sepamos que se llama Tim; nunca "Tim escala". `basic_info.name` sale de `name: "Tim"` en el mensaje del usuario, que es una fuente autorizada aunque Tim nunca dijera su nombre en voz alta.

## Ejemplo 10 — Contraejemplo: los atributos de otra persona nunca van en basic_info

Mensajes nuevos:
[{"role": "user", "name": "Tim", "content": "What have you been up to?"},
 {"role": "assistant", "name": "John", "content": "I'm a nurse at a hospital in Berlin, working night shifts. Pretty exhausting."}]
Memory Date: 2025-09-02

Salida:
```json
{
  "memory": [
    {"content": "John is a nurse at a hospital in Berlin, works night shifts, and finds it exhausting.", "tags": ["work", "health"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {"name": "Tim"}
}
```
La profesión y la ubicación de John son atributos SUYOS, así que van en `memory` con "John" como sujeto. NUNCA deben acabar en `basic_info.occupation` / `basic_info.location`: `basic_info` es exclusivamente el perfil propio del usuario. Solo `name` (Tim, del campo `name` del mensaje del usuario) va ahí. Observa que el idioma de salida sigue al de la entrada (este ejemplo está en inglés).

## Ejemplo 11 — Contraejemplo: la charla trivial no tiene basic_info

Mensajes nuevos:
[{"role": "user", "content": "Hola"},
 {"role": "assistant", "content": "¡Hola! El usuario se llama El usuario. ¿En qué puedo ayudarte?"}]
Memory Date: 2025-09-03

Salida:
```json
{
  "memory": [],
  "intentions": [],
  "basic_info": {}
}
```
El usuario no dijo nada sobre sí mismo. La frase del asistente no es un turno del usuario, y "El usuario" es un marcador de posición, no un nombre. Devuelve `{}`; nunca `{"name": "El usuario"}` ni `{"name": "usuario"}`.

---
"""

EXTRACT_PROMPT = """## Últimos k mensajes

{last_messages}

---

## Mensajes nuevos

{content}

---

## Memory Date

{memory_at}

---

## Current Date

{current_date}

---

# Formato de salida

Devuelve SOLO un objeto JSON dentro de un bloque delimitado ```json.

```json
{{
  "memory": [
    {{
      "content": "...",
      "tags": ["..."],
      "owner": "user"
    }}
  ],
  "intentions": [
    {{
      "content": "...",
      "tags": ["..."],
      "valid_until": "YYYY-MM-DD",
      "owner": "user"
    }}
  ],
  "basic_info": {{}}
}}
```

Reglas:

* `memory` = información duradera digna de recordar: preferencias, opiniones, actitudes,
  rasgos, eventos, planes, experiencias, hábitos, decisiones, sobre el usuario o
  sobre cualquier otra persona real con nombre, con el sujeto escrito según "# SUJETO"
* `intentions` = cosas orientadas al futuro que el usuario (u otra persona con nombre) planea /
  tiene intención de hacer, con `valid_until` = la fecha límite/fecha objetivo
  (resuelta respecto a Memory Date) o `null` si no la hay
* `owner` (obligatorio en cada memoria e intención) = `"user"` o `"agent"`
  (ver "# OWNER" más arriba; `"user"` por defecto en caso de duda)
* `basic_info` = **solo el perfil propio del usuario**. Incluye campos de perfil
  estables (p. ej. nombre, edad, ubicación, profesión, empleador) **declarados explícitamente**
  en los mensajes NUEVOS del usuario. Nunca infieras, nunca inventes. Si ningún campo se
  declara explícitamente, devuelve `{{}}`.
  * REGLA ESTRICTA: rellena un campo SOLO si el usuario lo declaró en un turno `user`.
    Lo que solo dijo el asistente (incluido el asistente citando una memoria
    anterior como "El usuario se llama X") NO cuenta. Si nada
    cumple el requisito, devuelve `"basic_info": {{}}`.
  * NUNCA escribas como valor el propio nombre del campo ni un marcador de posición ("user",
    "The user", "el usuario", "用户", "name", "N/A", "unknown", "none", "-"). Omite el campo.
  * Ejemplo negativo: charla trivial como `[user]: Hola` / `[assistant]: ¿En qué
    puedo ayudarte?`, o `[user]: Responde exactamente: PONG` → `"basic_info": {{}}`.
  * Excepción para `name`: el campo `name` del propio mensaje del usuario, o una línea
    inicial "El usuario se llama X" / "The user's name is X" / "用户名叫X", es una fuente autorizada para
    `name`, aunque el usuario nunca lo haya dicho en voz alta.
  * NUNCA pongas aquí los atributos de otra persona real. Si John (un mensaje con rol de
    asistente que lleva `name`) dice "Soy enfermero en Berlín", eso es una `memory`
    ("John es enfermero en Berlín."), NO `basic_info.occupation` / `.location`.
* Sin información duplicada entre secciones
* Las etiquetas deben contener 1–3 palabras clave concisas en minúsculas
* Usa arrays vacíos `[]` o `{{}}` cuando no se extraiga nada
* Devuelve solo JSON válido

Genera el JSON ahora."""

EXTRACT_PROMPT_NO_INTENTION = """## Últimos k mensajes

{last_messages}

---

## Mensajes nuevos

{content}

---

## Memory Date

{memory_at}

---

## Current Date

{current_date}

---

# Formato de salida

Devuelve SOLO un objeto JSON dentro de un bloque delimitado ```json.

```json
{{
  "memory": [
    {{
      "content": "...",
      "tags": ["..."],
      "owner": "user"
    }}
  ],
  "basic_info": {{}}
}}
```

Reglas:

* `memory` = información duradera digna de recordar: preferencias, opiniones, actitudes,
  rasgos, eventos, experiencias, hábitos, decisiones, **y planes futuros / acciones
  próximas**, sobre el usuario o sobre cualquier otra persona real con nombre, con el
  sujeto escrito según "# SUJETO". **No** inventes un apartado de intentions separado.
* `owner` (obligatorio en cada memoria) = `"user"` o `"agent"`
  (ver "# OWNER" más arriba; `"user"` por defecto en caso de duda)
* `basic_info` = **solo el perfil propio del usuario**. Incluye campos de perfil
  estables (p. ej. nombre, edad, ubicación, profesión, empleador) **declarados explícitamente**
  en los mensajes NUEVOS del usuario. Nunca infieras, nunca inventes. Si ningún campo se
  declara explícitamente, devuelve `{{}}`.
  * REGLA ESTRICTA: rellena un campo SOLO si el usuario lo declaró en un turno `user`.
    Lo que solo dijo el asistente (incluido el asistente citando una memoria
    anterior como "El usuario se llama X") NO cuenta. Si nada
    cumple el requisito, devuelve `"basic_info": {{}}`.
  * NUNCA escribas como valor el propio nombre del campo ni un marcador de posición ("user",
    "The user", "el usuario", "用户", "name", "N/A", "unknown", "none", "-"). Omite el campo.
  * Ejemplo negativo: charla trivial como `[user]: Hola` / `[assistant]: ¿En qué
    puedo ayudarte?`, o `[user]: Responde exactamente: PONG` → `"basic_info": {{}}`.
  * Excepción para `name`: el campo `name` del propio mensaje del usuario, o una línea
    inicial "El usuario se llama X" / "The user's name is X" / "用户名叫X", es una fuente autorizada para
    `name`, aunque el usuario nunca lo haya dicho en voz alta.
  * NUNCA pongas aquí los atributos de otra persona real. Si John (un mensaje con rol de
    asistente que lleva `name`) dice "Soy enfermero en Berlín", eso es una `memory`
    ("John es enfermero en Berlín."), NO `basic_info.occupation` / `.location`.
* Sin información duplicada entre secciones
* Etiquetas: 1–3 palabras clave concisas; etiquetas temáticas en minúsculas; los nombres propios conservan las mayúsculas
* Usa arrays vacíos `[]` o `{{}}` cuando no se extraiga nada
* Devuelve solo JSON válido; **nunca** incluyas una clave `intentions`

Genera el JSON ahora."""


# ================================================================
# Chat reconcile (agent/reconciler.py)
# ================================================================

SEARCH_QUERY_PROMPT = """Eres un generador de consultas de búsqueda. Dada una lista de memorias recién extraídas, genera un conjunto de consultas de búsqueda cortas que sirvan para encontrar memorias existentes relacionadas en una base de datos vectorial.

El objetivo es maximizar el recall: encontrar memorias existentes semánticamente relacionadas con las nuevas, aunque la redacción sea muy distinta.

## Memorias nuevas:
{new_memories}

## Instrucciones

Genera consultas de búsqueda que cubran:
- Los temas, entidades y asuntos clave mencionados en las memorias
- Versiones reformuladas o abstraídas de los conceptos centrales
- Conceptos relacionados que podrían existir en el almacén de memoria del usuario

Devuelve un array JSON de cadenas de consulta (5-15 consultas, cortas y enfocadas):

["query1", "query2", "query3", ...]

Devuelve solo el array JSON, sin ningún otro texto."""

RECONCILE_PROMPT = """Eres un sistema de gestión de memoria.

Tu tarea es integrar memorias nuevas en una base de memoria existente manteniéndola:

* sin pérdidas
* sin contradicciones
* compacta
* altamente recuperable

---

# Operaciones

## ADD

Úsala cuando la información sea completamente nueva.

```json
{{
  "op": "ADD",
  "content": "...",
  "owner": "user",
  "tags": ["..."]
}}
```

---

## UPDATE

Úsala cuando:

* la memoria nueva es compatible con una existente
* ambas describen el mismo tema
* fusionarlas mejora la completitud o la recuperabilidad

Reglas:

* fusiona sin pérdidas
* conserva toda la información útil
* NO crees duplicados
* si las dos discrepan en un valor, conserva el valor de la memoria con el `memory_at`
  más tardío; nunca dejes que una memoria más antigua sobrescriba un valor más reciente

### Misma ocurrencia frente a otra ocurrencia

Usa **UPDATE** solo cuando la memoria nueva añade detalle a la misma ocurrencia
del mundo real o al mismo hecho duradero ya registrado.

Si la misma persona realiza otra vez el mismo tipo de actividad/evento en otro momento
(otro viaje, partido, lesión, compra, susto de salud, etc.), mantén la ocurrencia
como un **ADD** separado. Conserva su propia fecha y sus detalles. Cuando una memoria existente
sea claramente otra ocurrencia de esa misma serie concreta, enlázala con
`related: [{{"memory_id":"...","relation":"same_series"}}]`.

No fusiones ocurrencias fechadas distintas en una única memoria larga de recuento/cronología.
La capa de recuperación construye las cronologías a partir de las memorias L2 enlazadas.

```json
{{
  "op": "UPDATE",
  "memory_id": "...",
  "content": "...",
  "owner": "user",
  "tags": ["..."]
}}
```

---

## SUPERSEDE

Úsala SOLO cuando:

* la memoria nueva contradice una memoria existente
* ambas no pueden ser ciertas al mismo tiempo
* la memoria antigua daría ahora una respuesta errónea sobre el estado actual
* el `memory_at` de la memoria nueva es igual o POSTERIOR al `memory_at` de la memoria existente

Ejemplos:

* cambió de ciudad
* cambió de empleador
* cambió su situación sentimental

NO la uses para:

* detalle añadido
* preferencias compatibles
* diferencias de redacción
* equivalencia semántica
* una memoria nueva cuyo `memory_at` sea ANTERIOR al de la memoria existente: es un
  estado más antiguo y nunca debe reemplazar uno más reciente (ver "Orden temporal")

Reglas:

* content debe contener SOLO el nuevo estado
* NO copies la información que no ha cambiado
* el sistema conserva el nodo antiguo y lo enlaza automáticamente en la cadena de evolución; no necesitas gestionar el historial

```json
{{
  "op": "SUPERSEDE",
  "memory_id": "...",
  "content": "...",
  "supersede_reason": "...",
  "owner": "user",
  "tags": ["..."]
}}
```

### Plegar varias memorias existentes en una sola cadena

Si DOS O MÁS memorias existentes siguen la MISMA dimensión y la memoria nueva es
el último cambio en esa dimensión (su `memory_at` es posterior al `memory_at` de todas las
memorias plegadas), puedes plegarlas en una única cadena de
evolución. Usa `memory_ids` (una lista ordenada, de la MÁS ANTIGUA → a la MÁS RECIENTE) en lugar de un único
`memory_id`:

```json
{{
  "op": "SUPERSEDE",
  "memory_ids": ["<oldest_id>", "<newer_id>"],
  "content": "<solo el estado más reciente>",
  "supersede_reason": "...",
  "owner": "user",
  "tags": ["..."]
}}
```

Las memorias listadas se encadenan de la más antigua → a la más reciente y la memoria nueva pasa a ser la
cabeza de la cadena. `content` sigue describiendo SOLO el estado más reciente: nunca copies en él el texto de las
memorias anteriores; siguen siendo consultables en el historial de la cadena.

---

# Reglas

* Conserva toda la información significativa.
* Omite los duplicados exactos.
* Prefiere fusionar fragmentos relacionados.
* Las ocurrencias fechadas distintas se mantienen como ADD separados; usa `same_series` solo para el
  mismo sujeto y el mismo tipo concreto de actividad/evento.
* Mantén cada memoria centrada en un único tema coherente.
* Sigue el "Orden temporal" que aparece más abajo.
* **NUNCA fusiones ni sustituyas entre sujetos distintos.** El sujeto es aquel de quien
  trata el contenido: "El usuario ..." frente a "John ..." frente a "Mary ...". Las memorias sobre
  personas distintas son hechos independientes aunque la frase sea por lo demás
  idéntica: "Al usuario le gusta el café" y "A John le gusta el café" son dos memorias
  separadas, nunca un UPDATE ni un SUPERSEDE la una de la otra.
* `owner` (`user`/`agent`): cópialo de la memoria que se está escribiendo. NUNCA fusiones ni sustituyas entre owners distintos: un hecho del usuario y un hecho del asistente son distintos aunque traten el mismo tema.
* Un SUPERSEDE o bien apunta a UNA memoria (`memory_id`) o bien pliega varias memorias de la misma dimensión en una cadena (`memory_ids`, de la más antigua a la más reciente). Un UPDATE apunta exactamente a UNA memoria.
* Nunca toques la misma memoria existente desde más de una operación.
* El idioma de salida coincide con el idioma de las memorias de entrada.

---

# Orden temporal

Toda memoria, nueva o existente, tiene `memory_at`: cuándo ocurrió. Las memorias
NO siempre se escriben en el orden en que ocurrieron: el historial puede importarse tarde,
y una memoria puede añadirse mientras todavía se procesa historial más antiguo. Por tanto, una
memoria nueva puede ser MÁS ANTIGUA que una existente.

* Decide qué memoria es más reciente SOLO por `memory_at`. "Memorias nuevas" significa "que se
  están escribiendo ahora", no "más recientes".
* Una memoria más antigua nunca debe reemplazar a una más reciente: nada de SUPERSEDE de una
  memoria más reciente, ni de UPDATE que sobrescriba un valor más reciente con uno más antiguo.
* Si una memoria nueva es más antigua que una memoria existente con la que entra en conflicto, conserva la
  memoria existente como estado actual. Haz ADD de la nueva como estado pasado, redactada
  en pasado con su fecha (por ejemplo "En 2023 el usuario vivía en Shanghái"),
  o no devuelvas nada si no aporta información.
* `memory_at: null` significa tiempo desconocido. Desconocido NO es más reciente: nunca hagas SUPERSEDE de una
  memoria que tiene un `memory_at` conocido con otra cuyo `memory_at` sea null.
* No inventes cronología.

---

# Opcional: `related`

En cualquier operación PUEDES añadir un array `related` que registre cómo se relaciona la memoria que se está escribiendo
con una memoria EXISTENTE. Esto no cambia lo que se almacena: solo
registra un enlace.

```json
{{
  "op": "ADD",
  "content": "...",
  "owner": "user",
  "tags": ["..."],
  "related": [{{"memory_id": "<existing_id>", "relation": "causes"}}]
}}
```

Solo se permiten estas cuatro relaciones:

* `causes`: las dos memorias están en una relación de causa/efecto
  (p. ej. "empezó a correr a diario" → "empezó a dolerle la rodilla")
* `elaborates`: añade detalle a la memoria existente, o es un desarrollo posterior de ella
* `contrasts`: están en tensión pero AMBAS siguen siendo ciertas (si una hace falsa a la
  otra, eso es un SUPERSEDE, no un enlace `contrasts`)
* `same_series`: ocurrencias fechadas distintas del mismo tipo concreto de actividad o
  evento para el mismo sujeto (por ejemplo, dos viajes de esquí diferentes)

Reglas:

* Enlaza solo con memorias que aparezcan en "Memorias existentes": nunca inventes un id.
* Emite un enlace solo cuando la relación sea **explícita y específica**. Que dos memorias
  traten simplemente del mismo tema amplio NO es una relación: omite `related`.
* `same_series` requiere el mismo sujeto y una acción/evento concreto repetido;
  compartir etiquetas amplias como health, travel, sport, food o work no basta.
* Omite el campo por completo cuando no haya nada que enlazar. Cero enlaces es el caso normal.
* Nunca uses `related` como sustituto de UPDATE / SUPERSEDE.

---
{few_shot_section}
###################
Datos de entrada
###################

Fecha actual: {current_date}

## Memorias existentes

{existing_memories}

## Memorias nuevas

{new_memories}

---

# Salida

Devuelve SOLO un array JSON dentro de un bloque de código delimitado ```json.

Si no se necesita ningún cambio:

```json
[]
```

Genera ahora el array JSON."""

RECONCILE_STRUCTURE_METADATA_PROMPT = """

Contrato adicional de salida estructural:

Para cada resultado ADD, UPDATE o SUPERSEDE, conserva o refina `topic_paths`,
`event_facet` y `topic_assignments`. `event_facet` contiene solo `subject`,
`participants`, `action`, `objects`, `locations` y `state`.
`topic_assignments` contiene entradas `{subject, role, path}` específicas de un sujeto;
cada segmento de la ruta es `{name, type}` y type es `topic|facet|series|episode`.
Los tipos siguen `topic -> facet -> series -> episode`; se pueden saltar niveles, pero
los tipos no pueden repetirse ni retroceder.
Usa Series solo para ocurrencias repetidas de una línea concreta de actividad/estado y
Episode solo para una única ocurrencia acotada del mundo real. Nunca infieras estos tipos a partir de
la profundidad, de una etiqueta amplia compartida o de una coincidencia no relacionada. Cuando una memoria existente
ya tenga una ruta específica del sujeto semánticamente equivalente, reutiliza exactamente sus nombres de nodo
y sus tipos en lugar de crear una bifurcación sinónima. Para solicitar una reutilización exacta, emite
`structure_references: [{"memory_id":"<existing_id>", "subject":"...",
"through":"topic|facet|series|episode"}]`. El ID referenciado debe aparecer en
las Memorias existentes. `through` significa copiar ese prefijo de ruta existente hasta el
tipo estructural indicado; un sufijo más profundo redactado explícitamente sigue siendo nuevo. La reutilización
está prohibida cuando difieren el sujeto, la línea concreta de actividad/estado o el límite del Episode.
Estos campos describen la memoria, nunca una forma de consulta futura como
"cuándo", "cuántos" o "quién".

Series es una relación del momento de reconciliación: establécela solo cuando la memoria nueva y
al menos una memoria existente independiente sean ocurrencias distintas de la misma
línea concreta de actividad/estado. Varias proposiciones de la escritura actual no
demuestran repetición.
"""

RECONCILE_FEW_SHOT = """
###################
Ejemplos
###################

## Ejemplo 1 — Una memoria nueva: una parte entra en conflicto con una cadena existente y otra no está relacionada

Memorias existentes:
[
  {"memory_id": "m1", "content": "El usuario trabaja como product manager en Stripe.", "owner": "user", "history_versions": [{"content": "El usuario trabaja como product manager en una startup fintech."}]}
]
Memorias nuevas:
[
  {"content": "El usuario ha dejado Stripe y ahora es product lead en Notion, y hace poco ha empezado a hacer cerámica los fines de semana.", "owner": "user"}
]

Salida:
```json
[
  {"op": "SUPERSEDE", "memory_id": "m1", "content": "El usuario trabaja como product lead en Notion.", "supersede_reason": "el empleador cambió de Stripe a Notion", "owner": "user", "tags": ["work"]},
  {"op": "ADD", "content": "El usuario ha empezado hace poco a hacer cerámica los fines de semana.", "owner": "user", "tags": ["hobby"]}
]
```
UNA SOLA memoria nueva contiene dos cosas: el cambio de trabajo (misma dimensión que la cadena de empleo existente m1, que ya tiene historial → SUPERSEDE, pasa a ser la nueva cabeza de la cadena) y un detalle no relacionado (cerámica → ADD). Divide esa memoria en la operación adecuada para cada parte: la nueva cabeza describe SOLO la situación laboral, nunca la cerámica.

## Ejemplo 2 — Plegar dos memorias existentes no enlazadas + el nuevo cambio en una cadena de 3 nodos

Memorias existentes:
[
  {"memory_id": "a1", "content": "El usuario está aprendiendo a tocar la guitarra.", "owner": "user", "memory_at": "2024-02-10 09:00"},
  {"memory_id": "a2", "content": "El usuario ya sabe tocar algunas canciones completas a la guitarra.", "owner": "user", "memory_at": "2024-09-05 09:00"}
]
Memorias nuevas:
[
  {"content": "El usuario ahora toca la guitarra en noches de micrófono abierto locales.", "owner": "user"}
]

Salida:
```json
[
  {"op": "SUPERSEDE", "memory_ids": ["a1", "a2"], "content": "El usuario toca la guitarra en noches de micrófono abierto locales.", "supersede_reason": "progreso con la guitarra: aprendiendo → tocando canciones → actuando en directo", "owner": "user", "tags": ["music", "hobby"]}
]
```
a1 y a2 nunca se enlazaron, pero siguen la misma dimensión (progreso con la guitarra) y la memoria nueva es el último paso. Pliégalas con `memory_ids` ordenados de la más antigua a la más reciente; forman una cadena de 3 nodos (a1 ← a2 ← nueva cabeza). El `content` de la cabeza describe SOLO el estado actual: no repite "aprendiendo" ni "algunas canciones".

## Ejemplo 3 — La nueva cabeza NO debe copiar el contenido de la memoria antigua

Memorias existentes:
[
  {"memory_id": "c1", "content": "El usuario vive de alquiler en un apartamento de un dormitorio en Shibuya, Tokio, paga ¥180,000/mes, le encanta estar a 5 minutos a pie de la estación, pero se queja de que el piso es ruidoso por la noche y no tiene aparcamiento.", "owner": "user"}
]
Memorias nuevas:
[
  {"content": "El usuario acaba de comprar una casa de tres dormitorios en las afueras de Yokohama, con jardín y garaje para dos coches, y se ha mudado a ella.", "owner": "user"}
]

Salida:
```json
[
  {"op": "SUPERSEDE", "memory_id": "c1", "content": "El usuario compró una casa de tres dormitorios en las afueras de Yokohama, con jardín y garaje para dos coches, y se mudó a ella.", "supersede_reason": "la vivienda cambió de un apartamento de alquiler en Shibuya a una casa en propiedad en Yokohama", "owner": "user", "tags": ["housing"]}
]
```
La memoria nueva hace SUPERSEDE de c1 y pasa a ser la cabeza de la cadena. El `content` de la cabeza describe SOLO la nueva casa de Yokohama. NO arrastres al nuevo nodo los detalles distintivos del apartamento antiguo (Shibuya, el alquiler de ¥180,000, el paseo a la estación, las quejas por ruido/aparcamiento): pertenecen claramente a la vivienda antigua, no a la nueva. Se quedan en el historial de la cadena y siguen siendo recuperables; copiarlos haría que la cabeza se contradijera a sí misma (alquilar en Shibuya Y ser propietario en Yokohama a la vez).

## Ejemplo 4 — Los sujetos distintos nunca se fusionan, por parecida que sea la redacción

Memorias existentes:
[
  {"memory_id": "d1", "content": "El usuario toma café solo todas las mañanas.", "owner": "user"}
]
Memorias nuevas:
[
  {"content": "John toma café solo todas las mañanas.", "owner": "user"},
  {"content": "El usuario ahora toma café solo dos veces al día, por la mañana y después de comer.", "owner": "user"}
]

Salida:
```json
[
  {"op": "ADD", "content": "John toma café solo todas las mañanas.", "owner": "user", "tags": ["food"]},
  {"op": "UPDATE", "memory_id": "d1", "content": "El usuario toma café solo dos veces al día, por la mañana y después de comer.", "owner": "user", "tags": ["food"]}
]
```
Las dos memorias nuevas tienen una redacción casi idéntica pero sujetos DISTINTOS. "John ..." trata de otra persona, así que es un ADD simple: nunca un UPDATE ni un SUPERSEDE de d1, aunque d1 diga casi lo mismo sobre el usuario. Solo la memoria cuyo sujeto también es "El usuario" puede fusionarse con d1.

## Ejemplo 5 — Evento recurrente: actualizar el recuento y mantener una cronología (UPDATE, no ADD)

Memorias existentes:
[
  {"memory_id": "i1", "content": "John se lesionó el tobillo la temporada pasada y necesitó reposo y fisioterapia.", "owner": "user", "memory_at": "2023-05-21 19:48"}
]
Memorias nuevas:
[
  {"content": "John volvió a torcerse el tobillo en un partido reciente.", "owner": "user", "memory_at": "2023-10-13 13:50"}
]

Salida:
```json
[
  {"op": "UPDATE", "memory_id": "i1", "content": "John se ha lesionado el tobillo dos veces: la primera la temporada pasada (hacia la temporada 2022–2023), con necesidad de reposo y fisioterapia; la segunda durante un partido hacia el 2023-10-13.", "owner": "user", "tags": ["health", "basketball", "injury"]}
]
```
El mismo tipo de evento vuelve a ocurrir → UPDATE de la memoria antigua. Indica el **recuento** y enumera las ocurrencias con sus fechas cuando se conozcan. NO hagas ADD de un segundo hecho "John se lesionó el tobillo", y NO hagas SUPERSEDE (la primera lesión no dejó de ser cierta).
"""


# ================================================================
# Working / coding memory (coding/)
# ================================================================

CODING_EXTRACT_PROMPT = """\
Extraes memorias de TRABAJO (working memories) duraderas de un segmento de sesión de ejecución de tareas.
Una sesión de "trabajo" es cualquier tarea práctica en la que el usuario maneja un ordenador /
sistema mediante herramientas; abarca TANTO el trabajo de software/código (escribir, depurar,
refactorizar, compilar, desplegar, uso de APIs/bibliotecas) COMO el trabajo de sistemas y operaciones
(ejecutar comandos de shell/PowerShell, análisis o limpieza de disco/almacenamiento, operaciones con
archivos y carpetas, instalar/configurar software, preparar el entorno/dispositivo,
inspeccionar la salud del hardware/disco, copias de seguridad, programación/automatización, procesamiento de datos).

Produces DOS capas a partir del MISMO segmento:

  EXPERIENCES (procedimental, "cómo hacerlo"): método reutilizable, extraído solo
  cuando supera el UMBRAL DE VALOR de abajo. Un paquete (task, search_keys, solution,
  boundary_envs).

  FACT (episódico, "qué pasó esta vez"): EXACTAMENTE UN registro de ESTA
  sesión: qué quería el usuario, cómo se abordó y cómo terminó. Sin
  umbral de valor, pero nunca hay más de uno.

Las dos capas responden a preguntas distintas y se almacenan por separado. Un método
que se generaliza va en EXPERIENCES; la historia de esta sesión concreta,
válida solo para este sistema / este proyecto / ahora mismo, va en el FACT.

═══════════════════════════════════════════════════════════════════
UMBRAL DE VALOR (SOLO EXPERIENCES): NO lo cruces a la ligera.
═══════════════════════════════════════════════════════════════════
Una memoria DEBE cumplir TODO lo siguiente:
  (a) Resuelve un problema que probablemente se repita para el mismo usuario
  (b) La solución no es trivial: combina varias piezas de información / pasos, O
      incluye la justificación de una decisión, O recoge una solución alternativa difícil de conseguir, O es un
      procedimiento/receta reutilizable para una tarea operativa (p. ej. cómo analizar y
      limpiar el uso de disco, cómo organizar archivos por lotes, cómo inspeccionar la salud
      del hardware, cómo configurar una tarea programada)
  (c) Un futuro agente / usuario preferiría consultarla antes que volver a descubrirla

IMPORTANTE: las tareas de análisis / diagnóstico / investigación CUENTAN:
Una tarea NO tiene que hacer un cambio ni "arreglar" algo para merecer ser recordada.
El trabajo de solo lectura / analítico / de resolución de problemas es valioso cuando su PROCEDIMIENTO o su
CONCLUSIÓN son reutilizables. Extrae explícitamente:
  - Recetas de análisis / diagnóstico: el procedimiento reutilizable + lo que reveló
    (p. ej. "cómo analizar qué carpetas ocupan el espacio de la unidad C:": los comandos de escaneo,
    los problemas encontrados y el hallazgo). "Solo produjo un informe, sin cambios"
    NO es motivo para omitirla: el MÉTODO reutilizable es la memoria.
  - Resultados de investigación, INCLUIDOS los negativos / de no encontrado
    (p. ej. "se buscó la carpeta X en C: y D: con rg / fs_search / índice SQL;
    no se encontró en ningún sitio"). Un callejón sin salida registrado evita que el próximo agente repita
    la misma búsqueda infructuosa, así que supera el umbral.
NO rechaces una memoria solo porque "el asistente podría regenerar los
comandos" o porque "en realidad no se modificó nada". Si el procedimiento o la conclusión
ahorrarían esfuerzo en el futuro, consérvala.

NO extraigas:
  - Operaciones de un solo paso realmente triviales sin procedimiento reutilizable ("renombrar un archivo",
    "git status", "ejecutar ls una vez")
  - Problemas puntuales ya resueltos sin lección transferible
  - Información que ya está en el repositorio/sistema (no dupliques package.json)
  - Tareas desechables por PR / por sesión, salvo que codifiquen un patrón reutilizable
  - El acto de PEDIRLE AL USUARIO información que falta. Solicitar un parámetro,
    enumerar lo que se desconoce o recabar campos obligatorios es comportamiento
    conversacional, no un método: todo agente ya sabe hacer una pregunta, así que
    no hay nada que consultar después. Nunca emitas una experiencia cuyo task o
    search_keys tengan la forma "cómo pedir X", "recabar el Y que falta",
    "obtener los campos obligatorios", "qué aclarar antes de empezar". Ten en cuenta que esto
    queda fuera solo de EXPERIENCES: que la sesión terminara esperando al usuario
    es un estado real y pertenece al FACT.

Si el segmento no hizo ningún avance sustancial porque se quedó esperando al
usuario (el asistente solo pidió parámetros, o quedó bloqueado antes de hacer ningún
trabajo real), emite "experiences": [] y escribe solo el FACT. Todavía no se ha
aprendido nada, así que no hay ningún método que guardar. Esto importa más de lo que parece:
una experiencia destilada de un bloqueo parece una habilidad legítima al recuperarse,
y un segmento posterior de la misma tarea coincidirá con ella y volverá a preguntar al usuario
en lugar de avanzar, de modo que el bloqueo se reproduce durante el resto de la
sesión. Juzga la sustancia por lo que realmente hicieron las herramientas, no por si el
turno produjo un texto fluido.

Heurística: si el usuario (o un futuro agente que haga el mismo tipo de tarea) querría
tener esto por escrito en lugar de averiguarlo de nuevo, consérvalo. Un procedimiento
operativo de varios pasos (incluso uno de sistemas/operaciones o de análisis/búsqueda de solo lectura, no
solo de código) normalmente califica. Descártalo solo si es un único comando que cualquier usuario
encontraría con `--help` en 30 segundos Y no incluye ninguna decisión/trampa digna de
recordarse.

═══════════════════════════════════════════════════════════════════
CAMPOS DE EXPERIENCE
═══════════════════════════════════════════════════════════════════

task          — Nombre con forma de consulta; formúlalo como un usuario lo PREGUNTARÍA más adelante.
                "publicar una versión en la plataforma xx", no
                "publish_release_internal"
                Autocontenido (sin "esto", "el archivo", "nosotros").

search_keys   — De 0 a 5 formulaciones alternativas que capten aspectos importantes de
                la solución que `task` por sí solo no cubriría. Formúlalas como preguntas
                o temas sobre los que futuros usuarios PREGUNTARÍAN. Distintas de task.
                Omítelas si task ya lo cubre todo.

solution      — Contenido completo y autocontenido que el usuario realmente necesita:
                comandos, ubicaciones de credenciales (NO los VALORES de las credenciales),
                rutas de archivos, pasos ordenados, trampas, justificación de las decisiones.
                Agrega el trabajo de extremo a extremo de varios turnos en UNA sola solution si
                forma una tarea coherente; divídelo en varias memorias solo cuando
                el usuario hizo tareas no relacionadas.

boundary_envs — Fijación concreta de la que depende la solution, en varias líneas "clave: valor":
                  runtime / SDK / biblioteca + versión
                  endpoint de la API o firma del método
                  servicio externo / versión del middleware
                  NOMBRE de la variable de entorno (no su valor)
                  ruta del archivo de configuración
                  identificador de host / clúster / región
                Si la solution no depende del entorno, déjalo como "" (vacío).
                Propósito: cuando el SDK/middleware subyacente se actualice, los futuros
                consumidores pueden revisarlo para decidir si la solution sigue siendo válida.

confidence  — de 0.0 a 1.0
  1.0   el usuario lo indicó explícitamente como regla / instrucción
  0.7   inferido con buena evidencia a partir de las acciones de las herramientas
  0.5   inferido débilmente; una sola observación; incierto

═══════════════════════════════════════════════════════════════════
CAMPO FACT: EXACTAMENTE UNO por segmento, sin umbral de valor
═══════════════════════════════════════════════════════════════════

Escribes UN solo fact para todo el segmento: un registro de estado recuperable de esta
sesión, no una retrospectiva pulida y nunca una lista de hallazgos separados. Un
futuro agente debe poder retomar el trabajo a partir de él sin confundir una sugerencia,
una inferencia o una búsqueda parcial con algo verificado.

subject    — QUIÉN o QUÉ es el objeto de esta sesión: la única entidad sobre la que o para la que se
             realizó el trabajo. Un sintagma nominal corto, sin frase, sin verbo.
             Elige la entidad más específica de la que *trata* la sesión, no la
             herramienta que usó:
               el nombre de una persona cuando la sesión atiende a una persona concreta
                 ("Dylan", "Matthew")
               el archivo / módulo / símbolo cuando el trabajo apunta a código
                 ("auth/middleware.ts", "parse_plan_text")
               el servicio / host / dispositivo / unidad en trabajo de operaciones
                 ("D:\\WXWork", "redis-cache-prod", "ci.yml")
             Es una CLAVE ESTRICTA, no prosa: dos sesiones sobre personas distintas
             o archivos distintos DEBEN tener subjects distintos, y dos sesiones
             sobre el mismo DEBEN tener la misma cadena, escrita de forma idéntica.
             Prefiere el identificador exacto tal como aparece en la traza antes que una
             descripción del mismo. Si el segmento realmente no tiene un único sujeto,
             devuelve "".

             Por qué importa: subject es la compuerta que decide si una sesión posterior
             puede sobrescribir esta como "la misma tarea,
             continuada". Si te equivocas, el estado de una persona reemplaza silenciosamente
             el de otra.

statement  — Un registro compacto de ESTA sesión. Tiene dos partes, y la segunda es la
             que se pierde.
             Conserva los datos dados propios de la tarea y las restricciones concretas en lugar de
             sustituirlos por una valoración genérica del resultado. Enuncia
             cada regla una sola vez: el texto de instrucciones repetido compite con los datos
             que la memoria debe retener.

             PARTE 1 — qué pasó, unos 300 caracteres latinos (~150 CJK).
             Empieza con el objetivo y el objeto concretos ("recuperar el espacio en disco ocupado
             por D:\\WXWork", "localizar la carpeta new_file_search"), no con un genérico
             "arreglar un bug" / "encontrar un archivo". Luego, a grandes rasgos, qué se inspeccionó, se probó
             o se cambió. Luego cómo terminó, con la semántica de exactamente uno de:
             completado, parcialmente completado, fallido, bloqueado o
             pendiente de verificación. Incluye los resultados negativos y los callejones sin salida
             sin exagerarlos.

             PARTE 2 — los DATOS DADOS DE LA TAREA: todo aquello sobre lo que funciona la tarea COMPLETA,
             aunque este segmento no lo haya establecido y aunque parezca
             demasiado elemental para mencionarlo. Vinculaciones (qué representa cada nombre/símbolo/etiqueta),
             valores fijos que la tarea proporciona en lugar de derivarlos,
             convenciones adoptadas (base de indexación, unidades, zona horaria, valores por defecto) y
             el propio resultado de este segmento CON su valor ("x = 7", no "se
             calculó el valor").

             Escribe la PARTE 2 como datos, un elemento por línea, con los valores tal como se dieron:
               presupuesto de reintentos = 5; timeout 30s; el índice empieza en 0
               entrada del paso 3: n=64, tolerancia 1e-6
               franja B: tipo=privada, coste 688.7-721.3 por unidad
             Una línea debería ser casi por completo valores. Mantén la notación de la traza
             (símbolos, unidades, números, grafías) y elimina sus frases: quita
             las expresiones que anuncian un dato en lugar de serlo ("preferiría",
             "debería estar entre … y …"). Se repiten una vez
             por elemento, así que una lista de 15 elementos las paga 15 veces.
             Inclúyelos en CADA fact; nunca escribas "como arriba" ni "igual que en la
             sesión anterior": la recuperación devuelve un puñado de facts por
             similitud y puede que el referente no esté entre ellos.

             Apunta a menos de 1200 caracteres en total. Si la PARTE 2 no cabe, ajusta la
             notación, nunca la cobertura: statement es el vector de recuperación Y
             el único campo que un segmento posterior puede leer (details no se embebe
             ni se inyecta), así que lo que se recorte de él se pierde. Comprime la redacción
             en torno a un dato; nunca el dato.

             La prueba: si el siguiente segmento no puede ver el material original, ¿puede
             continuar solo a partir de este registro? Solo cuenta un sí. Una
             vinculación que falta no falla de forma ruidosa: el nombre sigue pareciendo significativo, así que
             se lee con su significado de uso general y la respuesta que
             sigue es fluida y trata de otra pregunta.

             IDIOMA — el mismo idioma que la traza de la sesión: statement es el
             vector de recuperación y las consultas llegan en el idioma de trabajo del usuario,
             así que otro idioma paga una penalización interlingüística en cada búsqueda.

             FIDELIDAD A LA EVIDENCIA — registra solo las acciones realmente ejecutadas y
             la evidencia explícitamente presente en la traza. Conserva literalmente los
             comandos, flags, argumentos, errores y números relevantes.
             NUNCA completes un comando con flags habituales, y nunca conviertas
             consejos o planes en acciones:
               "se ejecutó Get-ChildItem -Force" → "se ejecutó Get-ChildItem"
                 (la traza no contenía `-Force`; no lo inventes)
             Limita cada hallazgo negativo a lo que realmente se buscó, y
             enumera CADA límite de cobertura explícito que pudiera invalidarlo,
             especialmente los fallos de permisos y los límites de profundidad: conserva las raíces,
             los filtros y la profundidad que estaban en vigor ("no se encontró bajo C:\\ ni
             D:\\ con profundidad 3; algunos directorios no se cubrieron por Access
             denied"), nunca lo amplíes a "no existe". No omitas un límite
             para que el registro quede más bonito.
             Nunca conviertas una búsqueda parcial en una ausencia global.
             Un intento bloqueado conserva lo que lo bloqueó.
             Conserva la distinción entre intentado, observado, verificado,
             inferido y sin resolver; exprésala de forma natural ("se intentó … pero lo
             bloqueó Access denied", "se sospecha …, aún no verificado"), no como
             etiquetas. Sin salida de herramientas o confirmación explícita, NUNCA afirmes que un
             arreglo funcionó, que se estableció una causa o que una ruta no existe.

             NO registres saludos ni pura charla: es el único caso
             que recibe "fact": null. Un segmento que solo enunció requisitos, o que
             se bloqueó antes de hacer trabajo, aun así estableció algo.

details    — La SALIDA concreta de la sesión, copiada literalmente. `statement` es
             un resumen y un resumen necesariamente descarta los valores individuales;
             una búsqueda posterior casi siempre quiere un valor concreto, no la idea general.
             Así que, sea cual sea el resultado direccionable que produjo esta sesión, reprodúcelo
             aquí en su forma y estructura originales:
               un plan / horario / itinerario → conserva cada línea, cada día, cada
                 franja y cada precio exactamente como se escribieron
               un bloque de configuración o de código → conserva el bloque
               la salida relevante de un comando → conserva las líneas que contienen los
                 hallazgos (tamaños, recuentos, versiones, texto de error)
               un resultado estructurado (tabla, lista de registros) → conserva las filas
             Conserva el formato original: saltos de línea, prefijos `Day 1:` / clave: valor,
             unidades, símbolos de moneda, ids. No vuelvas a resumir, no
             reordenes, no redondees números, no traduzcas.

             Copia SOLO lo que está presente en la traza. Nunca rellenes un hueco: si una franja
             quedó sin rellenar, déjala sin rellenar o dilo; un valor inventado
             es peor que uno ausente, porque se recuperará y se usará.
             Límite de unos 4000 caracteres; si la salida es más larga, conserva las partes
             que contienen valores concretos y descarta la prosa decorativa, indicando lo que se
             descartó.

             "" cuando la sesión no produjo ningún artefacto de ese tipo (investigación pura
             todavía sin hallazgos, o un bloqueo esperando al usuario).

             Este campo NO forma parte del vector de recuperación: `statement` y
             `subject` se encargan de encontrar, y `details` aporta el valor una vez encontrado.
             Así que no escribas aquí cebo para la recuperación, y no dupliques el
             resumen; escribe el material en bruto.

files      — 0..N rutas absolutas o relativas al repositorio de ARCHIVOS O DIRECTORIOS realmente
             manipulados o investigados durante esta sesión. Conserva las raíces investigadas
             como `C:\\` y `D:\\`. Incluye solo rutas que se hayan
             accedido/buscado realmente, no rutas meramente sugeridas o planificadas;
             [] si no aplica ninguna.
═══════════════════════════════════════════════════════════════════
CONTEXTO DEL HOST (proporcionado por el SDK)
═══════════════════════════════════════════════════════════════════
- workspace_id presente: {workspace_present}
- branch presente: {branch_present}

Cuando no hay workspace_id, la solution no puede depender de contexto
local del proyecto no explicitado: detalla cualquier repositorio / ruta / servicio que necesite dentro de
`solution` o `boundary_envs` para que la memoria siga siendo utilizable por sí sola.

═══════════════════════════════════════════════════════════════════
CONVERSACIÓN
═══════════════════════════════════════════════════════════════════
{conversation_block}

═══════════════════════════════════════════════════════════════════
SALIDA (solo JSON estricto, sin markdown)
═══════════════════════════════════════════════════════════════════
{{
  "experiences": [
    {{
      "task": "...",
      "search_keys": ["...", "..."],
      "solution": "...",
      "boundary_envs": "...",
      "confidence": 0.9
    }}
  ],
  "fact": {{
    "subject": "...",
    "statement": "...",
    "files": ["..."],
    "details": "..."
  }}
}}

POR DEFECTO, EXTRAE. Cuando el segmento hizo trabajo real, DEBES devolver al menos
una experiencia, aunque la tarea parezca simple o haya sido un éxito a la primera;
recoge igualmente la esencia reutilizable de lo que se hizo (el método / comando / cómo hacerlo).
Cuando la sesión es escasa, basta con la experiencia más valiosa.

La ÚNICA excepción es el caso de bloqueo descrito arriba: si el segmento no hizo trabajo real
porque quedó bloqueado pidiendo al usuario información que faltaba, devuelve
"experiences": [] y escribe solo el fact. No fabriques una experiencia para
cumplir esta instrucción: una inventada ("cómo pedir la ciudad
de salida") es peor que ninguna, porque luego se recupera y se sigue. El vacío está
permitido SOLO por ese motivo, nunca como atajo cuando realmente se hizo trabajo.

"fact" es un ÚNICO objeto, nunca una lista: un registro por sesión.

Escribe SIEMPRE el fact. El listón para el fact no es "¿fue valioso?" sino "¿pasó
algo en absoluto?", y un segmento que te llega ya lo ha superado.
Esto incluye los segmentos que parecen preparación más que trabajo: un único
mensaje del usuario que enuncia requisitos, parámetros o un estado inicial, sin
ejecutar herramientas, es justo el material que una sesión posterior no puede reconstruir por sí sola
— escribe en `statement` los requisitos, los parámetros y el estado inicial junto con la situación (en notación compacta, sin copiar literalmente las frases de la conversación),
y no los relegues a `details` (`details` no entra en el vector de recuperación y el lado de lectura tampoco lo inyecta). Usa
"fact": null SOLO para saludos y pura charla sin contenido alguno.
En caso de duda, escribe el fact: un registro innecesario cuesta una fila, mientras que uno
descartado cuesta cada sesión posterior que lo necesitaba y no tiene forma de saber que existía.
"""

CODING_EXTRACT_PROMPT_CODING = """\
╔═══════════════════════════════════════════════════════════════════════════╗
║ RESTRICCIÓN ESTRICTA: léela antes que nada; prevalece sobre cualquier     ║
║ formulación posterior de este prompt que parezca pedir una narración.     ║
║                                                                           ║
║ NO estás escribiendo un informe de lo que hizo una persona. Escribes lo   ║
║ que un AGENTE FUTURO necesita para actuar. Ese agente ya tiene su propia  ║
║ tarea; no le importa lo que quería este usuario. Necesita saber: qué se   ║
║ decidió aquí, en qué coordenadas del código, qué se probó y se descartó,  ║
║ y si se verificó.                                                         ║
║                                                                           ║
║ En concreto, en cada `statement` y en cada `solution`:                    ║
║   · El sujeto gramatical es el CÓDIGO o LA DECISIÓN, nunca "el usuario"   ║
║     y nunca "el agente". No escribas "El usuario intentó…",               ║
║     "El usuario quería…", "Luego el agente comprobó…".                    ║
║   · Nunca cierres con una frase administrativa como "la sesión se         ║
║     completó", "se tomó nota del error", "aún no se encontró solución".   ║
║     Cierra con la decisión y su estado de verificación. Si no se decidió  ║
║     nada, cierra con lo que se DESCARTÓ y la siguiente pista sin          ║
║     explorar: esa es la parte reutilizable.                               ║
║   · Conserva el síntoma concreto (cadena de error, comando que falla,     ║
║     salida incorrecta): es lo que coincidirá con una búsqueda futura.     ║
║                                                                           ║
║ mal:  "El usuario intentó escribir una QTable en RST con header_rows y    ║
║        obtuvo un TypeError. La sesión se completó anotando el error."     ║
║ bien: "el writer RST de ascii.rst rechaza header_rows: `tbl.write(..., \\ ║
║        format='ascii.rst', header_rows=['name','unit'])` lanza TypeError  ║
║        porque RST.__init__ no lo reenvía, a diferencia de                 ║
║        ascii.fixed_width, que sí lo acepta. No se escribió ningún         ║
║        arreglo: el writer necesita la misma canalización de header_rows   ║
║        que fixed_width."                                                  ║
╚═══════════════════════════════════════════════════════════════════════════╝

Extraes memorias de TRABAJO (working memories) duraderas de un segmento de sesión de ejecución de tareas.
Una sesión de "trabajo" es cualquier tarea práctica en la que el usuario maneja un ordenador /
sistema mediante herramientas; abarca TANTO el trabajo de software/código (escribir, depurar,
refactorizar, compilar, desplegar, uso de APIs/bibliotecas) COMO el trabajo de sistemas y operaciones
(ejecutar comandos de shell/PowerShell, análisis o limpieza de disco/almacenamiento, operaciones con
archivos y carpetas, instalar/configurar software, preparar el entorno/dispositivo,
inspeccionar la salud del hardware/disco, copias de seguridad, programación/automatización, procesamiento de datos).

Produces DOS capas a partir del MISMO segmento:

  EXPERIENCES (procedimental, "cómo hacerlo"): método reutilizable, extraído solo
  cuando supera el UMBRAL DE VALOR de abajo. Un paquete (task, search_keys, solution,
  boundary_envs).

  FACT (episódico, "qué quedó establecido esta vez"): EXACTAMENTE UN registro de ESTA
  sesión. NO es una narración de lo que hizo el usuario. Registra, en este orden:
  la tarea y su síntoma concreto (esto es lo que la hace localizable después), las
  coordenadas del código, EN QUÉ SE QUEDÓ y qué se descartó, y hasta dónde se
  verificó. Sin umbral de valor, pero nunca hay más de uno.

Las dos capas responden a preguntas distintas y se almacenan por separado. Un método
que se generaliza va en EXPERIENCES; la historia de esta sesión concreta,
válida solo para este sistema / este proyecto / ahora mismo, va en el FACT.

═══════════════════════════════════════════════════════════════════
UMBRAL DE VALOR (SOLO EXPERIENCES): NO lo cruces a la ligera.
═══════════════════════════════════════════════════════════════════
Una memoria DEBE cumplir TODO lo siguiente:
  (a) Resuelve un problema que probablemente se repita para el mismo usuario
  (b) La solución no es trivial: combina varias piezas de información / pasos, O
      incluye la justificación de una decisión, O recoge una solución alternativa difícil de conseguir, O es un
      procedimiento/receta reutilizable para una tarea operativa (p. ej. cómo analizar y
      limpiar el uso de disco, cómo organizar archivos por lotes, cómo inspeccionar la salud
      del hardware, cómo configurar una tarea programada)
  (c) Un futuro agente / usuario preferiría consultarla antes que volver a descubrirla

IMPORTANTE: las tareas de análisis / diagnóstico / investigación CUENTAN:
Una tarea NO tiene que hacer un cambio ni "arreglar" algo para merecer ser recordada.
El trabajo de solo lectura / analítico / de resolución de problemas es valioso cuando su PROCEDIMIENTO o su
CONCLUSIÓN son reutilizables. Extrae explícitamente:
  - Recetas de análisis / diagnóstico: el procedimiento reutilizable + lo que reveló
    (p. ej. "cómo analizar qué carpetas ocupan el espacio de la unidad C:": los comandos de escaneo,
    los problemas encontrados y el hallazgo). "Solo produjo un informe, sin cambios"
    NO es motivo para omitirla: el MÉTODO reutilizable es la memoria.
  - Resultados de investigación, INCLUIDOS los negativos / de no encontrado
    (p. ej. "se buscó la carpeta X en C: y D: con rg / fs_search / índice SQL;
    no se encontró en ningún sitio"). Un callejón sin salida registrado evita que el próximo agente repita
    la misma búsqueda infructuosa, así que supera el umbral.
NO rechaces una memoria solo porque "el asistente podría regenerar los
comandos" o porque "en realidad no se modificó nada". Si el procedimiento o la conclusión
ahorrarían esfuerzo en el futuro, consérvala.

NO extraigas:
  - Operaciones de un solo paso realmente triviales sin procedimiento reutilizable ("renombrar un archivo",
    "git status", "ejecutar ls una vez")
  - Problemas puntuales ya resueltos sin lección transferible
  - Información que ya está en el repositorio/sistema (no dupliques package.json)
  - Tareas desechables por PR / por sesión, salvo que codifiquen un patrón reutilizable

Heurística: si el usuario (o un futuro agente que haga el mismo tipo de tarea) querría
tener esto por escrito en lugar de averiguarlo de nuevo, consérvalo. Un procedimiento
operativo de varios pasos (incluso uno de sistemas/operaciones o de análisis/búsqueda de solo lectura, no
solo de código) normalmente califica. Descártalo solo si es un único comando que cualquier usuario
encontraría con `--help` en 30 segundos Y no incluye ninguna decisión/trampa digna de
recordarse.

═══════════════════════════════════════════════════════════════════
TRABAJO CON CÓDIGO: lo que un agente futuro no puede volver a deducir, así que DEBE recogerse
═══════════════════════════════════════════════════════════════════
Cuando el segmento es trabajo de software sobre un repositorio, el método reutilizable es solo
la mitad. La otra mitad es DÓNDE vive el trabajo y QUÉ VERSIÓN ganó. Eso
no se puede recuperar leyendo el código más tarde: el código muestra el estado actual,
no en qué rama está ni qué se descartó ya.

Esta sección NO pide un registro más largo. Pide que GASTES EL MISMO
PRESUPUESTO en tokens de mayor valor: elimina los objetivos repetidos, la narración de lo que el
asistente "decidió luego" y el código pegado que solo estás repitiendo; dedica esos
caracteres a los cuatro elementos de abajo. Un registro que omite la rama pero
cita 30 líneas de diff es un mal intercambio.

1. RAMA / UBICACIÓN DE TRABAJO: literal, en el `statement` del fact. Si el segmento
   menciona o hace checkout de `feat/x` / `fix/y`, ese token DEBE aparecer. Un agente
   que retoma el trabajo en la rama equivocada produce cambios que se descartan en silencio,
   así que este único token pesa más que cualquier otro detalle.

2. QUÉ VERSIÓN GANÓ: cuando el usuario cambió de opinión, nombra la ganadora y marca
   la perdedora en una sola cláusula: "se optó por A; se descartó la B intermedia". No ambas
   completas: una cláusula. Quedarse solo con la última es un error: un agente que se encuentre el
   código descartado debe reconocerlo, o "arreglará" las cosas volviendo a la variante descartada.
   Expresiones como `scratch that / go back to / actually, let's / no, not that`
   lo señalan: lo que sigue es lo que vale, lo que precede queda sustituido.

3. COORDENADAS EXACTAS, NO CÓDIGO PEGADO: ruta relativa al repositorio
   (`qutebrowser/utils/debug.py`, nunca solo `debug.py`) más la función /
   clase modificada. Si cambió una firma, da la nueva firma. Si se decidió una constante,
   regex o literal, cita ESO literalmente, pero cita solo eso, no el
   bloque que lo rodea. Una línea precisa vale más que un párrafo de aproximación.

4. VERIFICADO frente a SOLO ESCRITO: bastan unas pocas palabras ("tests ejecutados y pasando" /
   "confirmado solo con git diff, tests no ejecutados"). "el cambio está hecho" y "el cambio está hecho
   y probado" llevan a un agente futuro a hacer cosas muy distintas a continuación.

Antipatrón: resumir el objetivo y descartar las coordenadas:
  mal:  "se refactorizó la lógica de parseo de colores para que quedara más limpia."
  bien: "en feat/q4, en qutebrowser/config/configtypes.py QtColor._parse_value:
         se optó por `mult = 360.0 if kind == 'h' else 255.0`; se descartó la versión intermedia que
         usaba 255.0 para todo. Tests no ejecutados."
  (la buena es MÁS CORTA que la mayoría de las malas: simplemente dedica cada carácter
   a algo que no se puede volver a deducir)

═══════════════════════════════════════════════════════════════════
CAMPOS DE EXPERIENCE
═══════════════════════════════════════════════════════════════════

task          — Nombre con forma de consulta; formúlalo como un usuario lo PREGUNTARÍA más adelante.
                "publicar una versión en la plataforma xx", no "publish_release_internal"
                Autocontenido (sin "esto", "el archivo", "nosotros").

search_keys   — De 0 a 5 formulaciones alternativas que capten aspectos importantes de
                la solución que `task` por sí solo no cubriría. Formúlalas como preguntas
                o temas sobre los que futuros usuarios PREGUNTARÍAN. Distintas de task.
                Omítelas si task ya lo cubre todo.

solution      — Contenido completo y autocontenido que el usuario realmente necesita:
                comandos, ubicaciones de credenciales (NO los VALORES de las credenciales),
                rutas de archivos, pasos ordenados, trampas, justificación de las decisiones.
                Agrega el trabajo de extremo a extremo de varios turnos en UNA sola solution si
                forma una tarea coherente; divídelo en varias memorias solo cuando
                el usuario hizo tareas no relacionadas.

boundary_envs — Fijación concreta de la que depende la solution, en varias líneas "clave: valor":
                  runtime / SDK / biblioteca + versión
                  endpoint de la API o firma del método
                  servicio externo / versión del middleware
                  NOMBRE de la variable de entorno (no su valor)
                  ruta del archivo de configuración
                  identificador de host / clúster / región
                Si la solution no depende del entorno, déjalo como "" (vacío).
                Propósito: cuando el SDK/middleware subyacente se actualice, los futuros
                consumidores pueden revisarlo para decidir si la solution sigue siendo válida.

confidence  — de 0.0 a 1.0
  1.0   el usuario lo indicó explícitamente como regla / instrucción
  0.7   inferido con buena evidencia a partir de las acciones de las herramientas
  0.5   inferido débilmente; una sola observación; incierto

═══════════════════════════════════════════════════════════════════
CAMPO FACT: EXACTAMENTE UNO por segmento, sin umbral de valor
═══════════════════════════════════════════════════════════════════

Escribes UN solo fact para todo el segmento: un registro de estado recuperable de esta
sesión, no una retrospectiva pulida y nunca una lista de hallazgos separados. Un
futuro agente debe poder retomar el trabajo a partir de él sin confundir una sugerencia,
una inferencia o una búsqueda parcial con algo verificado.

statement  — Un resumen compacto de ESTA sesión. Tiene dos funciones a la vez, y
             debe cumplir AMBAS: es el vector de recuperación (un agente futuro lo encuentra
             buscando con un informe de incidencia nuevo, redactado como un usuario redacta un
             problema), y es la carga útil sobre la que ese agente actúa después. Así que:

             ABRE con la tarea y el síntoma, con las palabras que usaría un usuario.
                Esto es lo que la hace localizable: mantén la forma superficial cercana a cómo
                se reportaría el problema. Nombra el objetivo y el objeto concretos
                ("recuperar espacio en disco bajo D:\\WXWork", "ajustar el multiplicador de tono
                en QtColor._parse_value en feat/q4", "localizar la carpeta
                new_file_search"), nunca un genérico "arreglar un bug" / "buscar un archivo". Incluye
                el síntoma concreto: la cadena de error, el comando que falla, la
                salida incorrecta.

             LUEGO da las coordenadas y la decisión.
                ruta relativa al repositorio, símbolo y rama en el trabajo con código. Indica en qué
                SE QUEDÓ, y en la misma cláusula marca lo que se descartó
                ("se optó por X; se descartó la Y intermedia"). Esta es la parte que
                no se puede volver a deducir leyendo el código: el código solo muestra la
                ganadora, nunca que se probó Y y se rechazó. Un agente que se encuentre la
                variante rechazada más adelante debe poder reconocerla, o "arreglará"
                el código volviendo a la versión descartada.

             CIERRA con el estado de la evidencia.
                Exactamente uno de: completado, parcialmente completado, fallido, bloqueado o
                pendiente de verificación, más cómo se comprobó ("tests ejecutados y
                pasando" / "confirmado solo con git diff, tests no ejecutados"). Incluye
                los resultados negativos y los callejones sin salida sin exagerarlos.

             ⚠️ Nunca te quedes en la narración. "El usuario probó X y obtuvo el error Y; aún no se
             encontró solución" es un registro desperdiciado: se recupera bien pero no le da a un
             agente futuro nada sobre lo que actuar. Si la sesión realmente no llegó a ninguna
             decisión, di qué se descartó y cuál es la siguiente pista sin explorar:
             esa es la parte reutilizable.

             El sujeto de las frases debe ser el código y la decisión, no
             "el usuario". De 2 a 4 frases, prosa fluida, sin secciones etiquetadas.
             Techo estricto de ~400 caracteres latinos (~200 caracteres CJK).

             FIDELIDAD A LA EVIDENCIA — obligatoria:
             - Registra solo las acciones realmente ejecutadas y la evidencia explícitamente
               presente en la traza. Conserva literalmente los comandos, flags,
               argumentos, errores y números relevantes. NUNCA completes un
               comando con flags habituales ni conviertas consejos/planes en acciones.
             - Conserva la distinción entre intentado, observado, verificado,
               inferido y sin resolver. No añadas esas etiquetas a la prosa;
               expresa su significado de forma natural, p. ej. "se intentó … pero lo bloqueó
               Access denied" o "se sospecha … , aún no verificado".
             - Sin salida de herramientas o confirmación explícita, NUNCA afirmes que un arreglo
               funcionó, que se estableció una causa, que un archivo/carpeta no
               existe o que los tests pasaron.
             - Limita cada hallazgo negativo a lo que realmente se buscó y
               enumera CADA límite de cobertura explícito que pudiera invalidarlo,
               especialmente los fallos de permisos y la profundidad máxima de búsqueda:
               "no se encontró en las partes buscadas de C:\\ y D:\\; algunos
               directorios se omitieron por Access denied, y la búsqueda también tenía
               límite de profundidad". No omitas un límite para que el
               resumen quede más bonito. Nunca conviertas una búsqueda parcial en una ausencia
               global.
             - Presenta la causalidad como cierta solo cuando la traza la verifica
               directamente. En caso contrario, conserva la cautela ("se sospecha" / "se infiere" / "aún no
               verificado"); no reescribas una
               suposición incorrecta de un test como la causa raíz del sistema.

             CRÍTICO: este texto ES el vector de recuperación, y es lo único
             con lo que se compara una consulta futura. Un resumen genérico no
             vale nada, porque TODAS las sesiones parecen un resumen genérico:
             coincidiría con todas las consultas y no respondería a ninguna. Así que DEBES nombrar
             los anclajes concretos que hacen que ESTA sesión sea localizable: rutas de archivo
             reales, nombres de funciones / símbolos, nombres de ramas, comandos, cadenas de error,
             versiones, claves y valores de configuración, nombres de hosts / servicios.
             Si alguien busca más adelante cualquiera de esos datos concretos, este
             resumen tiene que ser lo que aparezca.
               bien: "arreglar un `npm run build` que falla en CI. Se leyeron
                      .github/workflows/ci.yml y package.json; se rastreó hasta webpack
                      5.74 que da ERR_OSSL_EVP_UNSUPPORTED con node 18. Subir el node
                      del workflow a 20 hizo que la build pasara; node 18 sigue
                      reproduciéndolo en local."
               bien: "en la rama feat/q0, en qutebrowser/utils/debug.py signal_name:
                      PyQt<5.11 solo expone el nombre mediante repr(sig), y repr aparece en
                      una forma con corchetes y otra con paréntesis. Se optó por dos patrones
                      probados en orden con fullmatch, cortando en la primera coincidencia; se
                      descartó la versión de una sola regex. Confirmado con git diff, tests no
                      ejecutados."
               mal:  "el usuario quería arreglar un bug de build, el agente miró algunos archivos
                      de configuración, cambió algo de código y lo arregló."
                      (sin ruta, sin comando, sin cadena de error, sin versión: nada
                       sobre lo que recuperar; todas las sesiones se leen exactamente así)
               mal:  "se arregló el problema de compatibilidad del parseo de nombres de señales en una
                      rama de funcionalidad."
                      (¿qué rama? ¿qué archivo? ¿qué función? ¿cuál de las dos
                       versiones en competencia ganó? nada de esto sobrevive a ser
                       recuperado una semana después)
             Ejemplos de fidelidad (mal → bien):
               "se ejecutó Get-ChildItem -Force" → "se ejecutó Get-ChildItem"
                 (la traza no contenía `-Force`; no lo inventes)
               "se confirmó que new_file_search no existe" →
                 "new_file_search no se encontró en las partes buscadas de C:\\ y
                  D:\\; algunos directorios se omitieron por Access denied o por el límite
                  de profundidad, así que la investigación solo está hecha en parte"
               "las cargas idénticas provocan un error de bloqueo espurio" →
                 "el script de verificación suponía que el último escritor es siempre un rank
                  fijo; esa suposición no se cumple, así que si hay un bug de bloqueo
                  sigue sin verificarse"

             NO registres saludos ni pura charla. Si el segmento no estableció
             nada en absoluto, devuelve "fact": null.

files      — 0..N rutas absolutas o relativas al repositorio de ARCHIVOS O DIRECTORIOS realmente
             manipulados o investigados durante esta sesión. Conserva las raíces investigadas
             como `C:\\` y `D:\\`. Incluye solo rutas que se hayan
             accedido/buscado realmente, no rutas meramente sugeridas o planificadas;
             [] si no aplica ninguna.

═══════════════════════════════════════════════════════════════════
CONTEXTO DEL HOST (proporcionado por el SDK)
═══════════════════════════════════════════════════════════════════
- workspace_id presente: {workspace_present}
- branch presente: {branch_present}

Cuando no hay workspace_id, la solution no puede depender de contexto
local del proyecto no explicitado: detalla cualquier repositorio / ruta / servicio que necesite dentro de
`solution` o `boundary_envs` para que la memoria siga siendo utilizable por sí sola.

═══════════════════════════════════════════════════════════════════
CONVERSACIÓN
═══════════════════════════════════════════════════════════════════
{conversation_block}

═══════════════════════════════════════════════════════════════════
SALIDA (solo JSON estricto, sin markdown)
═══════════════════════════════════════════════════════════════════
{{
  "experiences": [
    {{
      "task": "...",
      "search_keys": ["...", "..."],
      "solution": "...",
      "boundary_envs": "...",
      "confidence": 0.9
    }}
  ],
  "fact": {{
    "statement": "...",
    "files": ["..."]
  }}
}}

OBLIGATORIO: DEBES devolver al menos UNA experiencia. NO devuelvas una lista vacía.
Aunque la tarea parezca simple o haya sido un éxito a la primera, recoge igualmente la
esencia reutilizable de lo que se hizo (el método / comando / cómo hacerlo). Cuando la sesión es escasa
solo se requiere la experiencia más valiosa, pero nunca
devuelvas un array "experiences" vacío.

"fact" es un ÚNICO objeto, nunca una lista: un registro por sesión. Usa
"fact": null solo cuando el segmento sea pura charla que no estableció nada.
"""

CODING_RECONCILE_PROMPT = """\
Decides cómo fusionar los nuevos borradores de memoria de código con las memorias existentes del usuario.

Para cada borrador nuevo, decide una de estas opciones:
  ADD     — ninguna memoria existente describe la misma tarea/aspecto
  UPDATE  — una memoria existente describe la misma tarea; el nuevo borrador la reemplaza
            (puede que la solution haya cambiado, que el entorno se haya actualizado, etc.)
  SKIP    — duplicado de una existente; nada nuevo

Además, puedes emitir operaciones DELETE sobre candidatos existentes cuando el nuevo
contenido los deja obsoletos o inválidos:
  DELETE  — la nueva conversación sustituye/cancela/invalida EXPLÍCITAMENTE una
            memoria existente que no se puede reparar con UPDATE. Ejemplos:
              * el usuario dice "ya no uses X" y X solo está presente como una
                memoria aparte (no la que se está añadiendo/actualizando)
              * el boundary_envs del candidato (servicio / endpoint / SDK) ya no existe
                y la nueva conversación confirma la retirada

Reglas:
- "Misma tarea" significa que la memoria existente y el nuevo borrador serían ambas respuestas
  válidas a la misma consulta futura del usuario.
- Si la solution difiere, prefiere UPDATE (no guardamos historial).
- Si tanto task como solution coinciden estrechamente, SKIP.
- DELETE solo cuando haya evidencia EXPLÍCITA de que el candidato es inválido/obsoleto.
  Nunca hagas DELETE solo porque un candidato parezca "anticuado".
- Las operaciones de salida pueden superar en número a los borradores cuando se producen DELETE.

═══════════════════════════════════════════════════════════════════
BORRADORES NUEVOS
═══════════════════════════════════════════════════════════════════
{drafts_block}

═══════════════════════════════════════════════════════════════════
CANDIDATOS EXISTENTES (por borrador, recuperados por similitud de task)
═══════════════════════════════════════════════════════════════════
{candidates_block}

═══════════════════════════════════════════════════════════════════
SALIDA (solo JSON estricto, sin markdown)
═══════════════════════════════════════════════════════════════════
{{
  "ops": [
    {{"draft_idx": 0, "action": "ADD"}},
    {{"draft_idx": 1, "action": "UPDATE", "target_experience_id": "exp_xxx", "reason": "..."}},
    {{"draft_idx": 1, "action": "DELETE", "target_experience_id": "exp_yyy", "reason": "el usuario declaró explícitamente obsoleto Y"}},
    {{"draft_idx": 2, "action": "SKIP",   "reason": "duplicado"}}
  ]
}}

Si no hay borradores, devuelve {{"ops": []}}.
"""

CODING_FACT_RECONCILE_PROMPT = """\
Mantienes FACTS DE TRABAJO a nivel de sesión. Cada statement resume una
traza/sesión completa: la tarea del usuario, el enfoque a grandes rasgos y el resultado. No es una
afirmación atómica.

Todo nuevo fact de sesión de abajo SE almacenará; esa no es tu decisión. Tu único
trabajo es detectar la continuación de tareas: ¿la nueva sesión sigue avanzando en la
MISMA tarea del usuario que un fact de sesión existente? Si es así, haz SUPERSEDE del fact más antiguo para que
los enlaces formen una cadena de progreso entre sesiones para esa tarea.

Marca un fact existente como sustituido SOLO cuando haya evidencia sólida de que:
  - Los subjects coinciden: ambos registros tratan de la MISMA entidad: la misma
    persona, el mismo archivo/módulo, el mismo host/servicio. Un subject compartido es
    necesario pero no suficiente; Y
  - El objetivo del usuario es sustancialmente el MISMO, no simplemente del mismo tema/proyecto;
    Y
  - La nueva traza continúa, reintenta, amplía, verifica o termina el trabajo
    registrado por la traza existente.

NO hagas SUPERSEDE cuando:
  - Los dos registros nombran subjects distintos. Dos personas de un mismo grupo, dos archivos
    de un mismo módulo, dos hosts de un mismo clúster son PARALELOS, no sucesivos: el
    estado de una persona no es una versión más reciente del de otra. Cuando un subject se muestra
    como (unspecified), juzga solo por el statement.
  - Las sesiones simplemente mencionan el mismo archivo, símbolo, componente, repositorio,
    error o tecnología, pero persiguen objetivos de usuario distintos.
  - Una tarea solo aporta contexto o una técnica para otra tarea.
  - La relación es incierta. Sé conservador: INSERT es la opción segura por defecto;
    fusionar sesiones no relacionadas destruye su historial de tareas separado.

Trata cada statement como texto acotado por la evidencia. No reinterpretes una inferencia
contenida en él como una causa establecida, ni uses una conjetura/error compartido como prueba de que
los objetivos del usuario son los mismos. Tu decisión trata solo de la continuación de la tarea; no
"corrijas", completes ni adornes ninguno de los registros de sesión.

Ejemplos:
  existente: "El usuario necesitaba arreglar el npm run build de CI; se rastreó hasta
             ERR_OSSL_EVP_UNSUPPORTED con Node 18, workflow aún sin cambiar."
  nuevo:     "El usuario siguió arreglando la misma build de CI; subió Node en
             .github/workflows/ci.yml a 20 y npm run build pasó."
                                                                 → SUPERSEDE

  existente: "El usuario necesitaba arreglar el manejo de la caducidad de JWT en
             auth/middleware.ts; se añadió la lógica de refresco."
  nuevo:     "El usuario necesitaba añadir logging de rendimiento a
             auth/middleware.ts; se introdujo una métrica de latencia."
                                                                 → conservar ambos
            (mismo archivo/proyecto, distinto objetivo del usuario)

═══════════════════════════════════════════════════════════════════
NUEVOS FACTS DE SESIÓN
═══════════════════════════════════════════════════════════════════
{drafts_block}

═══════════════════════════════════════════════════════════════════
FACTS DE SESIÓN EXISTENTES (por fact nuevo, recuperados por similitud del statement)
═══════════════════════════════════════════════════════════════════
{candidates_block}

═══════════════════════════════════════════════════════════════════
SALIDA (solo JSON estricto, sin markdown)
═══════════════════════════════════════════════════════════════════
{{
  "ops": [
    {{"draft_idx": 0, "supersedes": ["fact_xxx"], "reason": "continúa la misma tarea de arreglo de CI"}},
    {{"draft_idx": 1, "supersedes": []}}
  ]
}}

Usa solo ids de fact que aparezcan en la lista de candidatos de ese mismo draft_idx.
Si no se sustituye nada en ningún caso, devuelve {{"ops": []}}.
"""

CODING_CHAIN_SUMMARY_PROMPT = """\
Una tarea del usuario en un almacén de memoria de trabajo ha avanzado a lo largo de varias sesiones. Abajo están
los resúmenes de sesión de la cadena de progreso de esa tarea, del más antiguo al más reciente, terminando con
la sesión actual.

Resume cómo avanzó la MISMA tarea a lo largo de las sesiones hasta su estado actual.
Cubre cada etapa importante en orden cronológico. Para cada etapa, conserva
cuando estén disponibles: la acción o el cambio real, su resultado directo y cualquier
problema sin resolver o condición limitante. Termina con un estado actual explícito.

Reglas:
  - Esto es solo compresión. Informa SOLO de lo que dicen las entradas; nunca añadas ni
    completes un comando, causa, motivo, acción, resultado de test, estado de éxito o
    conclusión negativa global.
  - Conserva exactamente el estado de la evidencia: intentado no es completado, observado no es
    verificado, inferido no es establecido, y lo no resuelto sigue sin resolver.
    Mantén las palabras de incertidumbre y los límites de búsqueda/permisos/cobertura.
  - Indica fielmente el estado final actual (completado, parcialmente completado,
    fallido, bloqueado o pendiente de verificación). Nunca lo mejores basándote en
    una historia causal plausible ni en una entrada posterior que no lo verificó.
  - Cuando las entradas mencionen una causa sospechada y un resultado posterior, relaciónalos
    causalmente solo si una entrada dice directamente que esa causa se verificó.
  - Conserva los anclajes concretos de recuperación: rutas de archivo, símbolos, comandos, cadenas
    de error, versiones, claves/valores de configuración y valores numéricos importantes que
    importen para el progreso.
  - Para cadenas de 2-3 generaciones, no comprimas en exceso la progresión en una sola
    frase. Prefiere 2-4 párrafos compactos cuando sea útil, o prosa legible con
    un objetivo de tarea claro, la progresión etapa por etapa y el estado actual.
  - No copies mecánicamente todo el historial. Omite repeticiones y detalles de poco valor
    conservando las acciones importantes, los resultados directos y lo pendiente.
  - No inventes títulos de etapa ni fechas. Menciona una fecha solo cuando las entradas
    la hagan significativa; las marcas de tiempo sueltas son ruido.
  - Si las entradas dicen que se omitieron revisiones anteriores, indica explícitamente que
    se omitió historial anterior; no des a entender que las entradas proporcionadas están completas.
  - Escribe en el mismo idioma en que están escritas las entradas.
  - Sin preámbulo ni comillas envolventes.

Ejemplo
  entradas:
    (2026-07-20) El usuario necesitaba arreglar el npm run build de CI; se rastreó hasta
                 ERR_OSSL_EVP_UNSUPPORTED con Node 18, workflow aún sin
                 cambiar.
    (2026-07-24) El usuario continuó ese arreglo de CI; subió Node en
                 .github/workflows/ci.yml a 20, pero la caché de dependencias
                 seguía reproduciendo el error antiguo.
    (now)        El usuario continuó verificando el mismo arreglo; tras limpiar la
                 caché de npm, npm run build pasó.
  salida:
    El objetivo de la tarea es arreglar el npm run build de CI. Primero se rastreó hasta
    ERR_OSSL_EVP_UNSUPPORTED con Node 18, con el workflow aún sin cambiar;
    después se subió .github/workflows/ci.yml a Node 20, pero la caché de
    dependencias mantuvo presente el error antiguo. Por último, tras limpiar la caché de npm,
    npm run build pasó, así que este arreglo de la build está actualmente verificado como completado.

═══════════════════════════════════════════════════════════════════
ENTRADAS
═══════════════════════════════════════════════════════════════════
{chain_block}

═══════════════════════════════════════════════════════════════════
SALIDA (solo el resumen, nada más)
═══════════════════════════════════════════════════════════════════
"""


# ================================================================
# Episode / topic summaries
# ================================================================

EPISODE_SUMMARY_PROMPT = """Estás resumiendo UN episodio de conversación con el usuario.

Un episodio es un único tramo de conversación (un asunto del que se habló),
posiblemente a lo largo de varias sesiones de escritura.

## Memorias registradas durante este episodio (de la más antigua → a la más reciente)

{memories}

---

Escribe un único resumen denso de lo que ocurrió / de lo que se habló en este episodio.

Reglas:

* De 2 a 4 frases, en tercera persona, empezando por "El usuario ..." (o por la persona con nombre
  si las memorias tratan de otra persona).
* Une las memorias en un único relato coherente: un episodio suele tener un
  hilo conductor (un plan, un problema, una salida, una decisión).
* Conserva los detalles concretos: nombres, lugares, números, fechas.
* NO inventes nada que no esté en las memorias.
* NO enumeres las memorias una por una; sintetízalas.
* El idioma de salida debe coincidir con el idioma de las memorias.

Devuelve SOLO el texto del resumen, sin preámbulo, sin JSON, sin comillas."""

TOPIC_SUMMARY_PROMPT = """Estás resumiendo todo lo que se sabe sobre UN tema.

## Tema

{topic_name}

## Memorias de este tema (de la más antigua → a la más reciente)

{memories}

---

Escribe un único resumen denso de lo que se sabe sobre el usuario en relación con este
tema.

Reglas:

* De 2 a 4 frases, en tercera persona, empezando por "El usuario ..." (o por la persona con nombre
  si las memorias tratan de otra persona).
* Cubre el tema en su conjunto: indica la situación actual y menciona cómo
  evolucionó solo cuando el cambio importe.
* Conserva los detalles concretos: nombres, lugares, números, fechas.
* NO inventes nada que no esté en las memorias.
* NO enumeres las memorias una por una; sintetízalas.
* El idioma de salida debe coincidir con el idioma de las memorias.

Devuelve SOLO el texto del resumen, sin preámbulo, sin JSON, sin comillas."""


# ================================================================
# System 2 evolve (pipelines/system2_agent.py)
# ================================================================

SYSTEM2_AGENT_PROMPT = """Eres un Agent de procesamiento cognitivo. Tu trabajo es hacer evolucionar estructuras cognitivas de orden superior a partir de los datos de memoria en bruto de un usuario.

## Arquitectura de memoria

### VDB (base de datos vectorial): solo lectura
| Capa | Descripción |
|-------|-------------|
| L0 BASIC_INFO | Atributos del usuario (nombre, edad, profesión) |
| L1 RAW | Transcripciones originales de las conversaciones |
| L2 FACT | Hechos atómicos extraídos de las conversaciones |
| L3 SUMMARY | Resúmenes de sesión |
| L4 IDENTITY | Rasgos de identidad, preferencias, hábitos |
| L5 KNOWLEDGE | Conocimiento de dominio |

### Graph (grafo de conocimiento): eres el único que escribe en él
| Capa | Descripción |
|-------|-------------|
| L6 SCHEMA | Patrones de comportamiento: ligados a un dominio, atómicos, inmutables una vez creados |

## ¿Qué es un Schema L6?

Un Schema es un patrón de comportamiento transversal a varios eventos dentro del modelo de usuario a largo plazo y
contiene los tres componentes siguientes:


- **Circunstancia**: El dominio/tema/situación en el que se observa este patrón. Un Schema DEBE mantenerse dentro de su circunstancia: NO generalices entre dominios. La abstracción entre dominios la gestiona el sistema por separado.
- **Patrón**: El comportamiento habitual, el estilo de pensamiento o la tendencia de acción del usuario en esta circunstancia.
- **Insight**: El impulsor psicológico o modelo mental subyacente.

### Formato del contenido
Usa una sola frase que combine los tres:
"Cuando [circunstancia], el usuario [patrón], lo que refleja [insight]."

Para patrones duraderos relacionados con emociones, terapia, estrés, duelo o trauma, no
borres el trasfondo autobiográfico específico convirtiéndolo en una etiqueta genérica de
personalidad. Prefiere una estructura anclada que conserve:
"Desencadenante/trasfondo -> respuesta emocional recurrente -> recurso de afrontamiento observado."
Incluye solo desencadenantes, respuestas y recursos de afrontamiento presentes en evidencia
propia del usuario (user-owned). Los consejos del asistente no son un recurso de afrontamiento observado del usuario.

### Reglas
- **Atómico**: Un patrón por Schema. Dos patrones distintos → dos Schemas.
- **Inmutable**: Una vez creado, el contenido NUNCA cambia.
- **Solo evidencia**: Los hechos nuevos que simplemente respaldan la redacción existente → llama a
  `add_evidence`. Si la nueva evidencia cambia, corrige o acota sustancialmente el
  estado duradero actual, usa `revise_schema` para que la versión antigua se conserve para
  auditoría pero se retire de la recuperación activa.
- **Ligado al dominio**: Mantente dentro del dominio observado. La abstracción entre dominios la gestiona un proceso del sistema aparte.
- **Separa hechos de patrones**: Una única identidad explícita, estado de salud,
  uso de equipamiento, afiliación, proyecto o preferencia pertenece a la memoria de estado L2,
  no a L6. L6 requiere evidencia propia del usuario que represente al menos dos escrituras raw
  o sesiones independientes. Un solo hecho L2 reconciliado puede representar varias escrituras;
  revisa sus source_raw_memory_ids en lugar de contar nodos de hecho.
- **Seguridad del sujeto**: Los hechos que comparten un fragmento de origen pueden describir a otras personas o
  ejemplos. Crea un Schema del usuario solo cuando el material atribuya explícitamente
  el patrón al usuario. De lo contrario, no emitas ninguna operación para ese material.
- **Cobertura de candidatos**: En una revisión source-context, da cuenta de cada hecho
  candidato listado: o lo usas como evidencia en `create_schema`/`add_evidence`,
  o emites `skip_fact`. El contexto raw puede confirmar su sujeto o restaurar detalles
  omitidos, pero no debe dar pie a Schemas no relacionados.
- **Umbral de evidencia**: Crear un L6 requiere uno o más hechos de evidencia propios
  del usuario que representen al menos dos escrituras raw o sesiones independientes.
  Los consejos del asistente no establecen por sí solos un patrón del usuario.

### Ejemplos buenos y malos
✅ "Cuando cocina, el usuario sigue estrictamente los pasos de la receta y mide con precisión los ingredientes, lo que refleja una necesidad de estructura externa para gestionar la incertidumbre."
✅ "Cuando juega a videojuegos, el usuario siempre consulta guías para lograr el 100% de compleción, impulsado por una baja tolerancia a lo incompleto."
✅ "Tras la pérdida inesperada de un mentor, los recordatorios de la mortalidad pueden reavivar el duelo de repente; el usuario se detiene a reflexionar y da paseos tranquilos para recuperar la estabilidad."
❌ "El usuario aborda la música como un medio de síntesis creativa y de construcción de puentes culturales, mezclando constantemente diversas tradiciones musicales..." (varios patrones fusionados, demasiado amplio)
❌ "Al usuario le apasionan muchas cosas y valora la calidad." (sin circunstancia, demasiado vago)
❌ "El usuario se guía por el sentido y es resiliente." (borra el desencadenante duradero y la evidencia de afrontamiento observada)

## Tu flujo de trabajo

Materiales de entrada (solo se te invoca cuando existe al menos un clúster):
- **Resultados de clústeres**: Entrada principal: hechos agrupados por similitud semántica
- **Hechos sin clúster (complementarios)**: Hechos que no están en ningún clúster; menor prioridad que los resultados de clústeres

Para cada grupo de hechos (con o sin clúster):
1. **Busca Schemas existentes**: Si un Schema existente ya recoge este patrón, llama a `add_evidence`. Usa `revise_schema` solo cuando la redacción antigua quedaría sustancialmente incompleta o resultaría engañosa tras añadir la nueva evidencia.
2. **Crea un Schema nuevo**: Solo si ningún Schema existente cubre este patrón. Una frase, tres componentes.
3. **Construye relaciones**: Si dos Schemas están relacionados temáticamente, usa `add_edge` (RELATED_TO).

## Principios

- Todas las operaciones sobre el Graph DEBEN pasar por las tools
- El contenido de un Schema es INMUTABLE: nunca vuelvas a crear lo que ya existe
- Prefiere `add_evidence` antes que crear duplicados
- Prefiere no crear antes que crear nodos de baja calidad
- Un Schema = un patrón atómico en un dominio
- Al etiquetar, prefiere reutilizar las etiquetas de la lista de etiquetas existentes"""


# ================================================================
# Fragments assembled in code
# ================================================================

# Display name of this language, used in logs and docs.
LANGUAGE_NAME = "Español"

# Appended to every memory-writing prompt ONLY when the caller set the
# language explicitly (per call or MemoryConfig.language). Auto-detected
# requests never see it, so their prompts stay exactly as before.
OUTPUT_LANGUAGE_DIRECTIVE = """

---

# IDIOMA DE SALIDA

Escribe en español todos los valores en lenguaje natural que generes (texto de las memorias, resúmenes, motivos, consultas), sea cual sea el idioma de la conversación o de las memorias existentes. Esta regla prevalece sobre cualquier regla anterior que indique igualar el idioma de entrada. Mantén exactamente como están las claves JSON, los nombres de campo, los valores de enumeración, los nombres de operación, los IDs, el código, las rutas de archivo, los comandos, los mensajes de error y los nombres propios.
"""

# enable_intention=False: the "two kinds of memory" section of
# EXTRACT_SYSTEM_PROMPT is replaced by a single-bucket section. The pattern
# must match this language's EXTRACT_SYSTEM_PROMPT.
NO_INTENTION_SECTION_PATTERN = r"# DOS TIPOS DE MEMORIA.*?(?=\n# OWNER\n)"

NO_INTENTION_MEMORY_SECTION = (
    "# MEMORIA\n"
    "\n"
    "Extraes un solo tipo de memoria: **memory** (memorias).\n"
    "\n"
    "## memory\n"
    "\n"
    "Aquí va todo lo duradero y digno de recordar sobre el usuario, y sobre cualquier otra\n"
    "persona con nombre que aparezca en la conversación:\n"
    "\n"
    "* preferencias, opiniones, valores, actitudes\n"
    "* eventos, acciones, experiencias\n"
    "* hábitos, patrones de comportamiento\n"
    "* relaciones, rasgos\n"
    "* hechos objetivos, decisiones, restricciones\n"
    "* actividades en curso\n"
    "* **planes futuros / acciones próximas** que merezca la pena recordar\n"
    "  (ponlos en `memory`; NO inventes un apartado de intentions separado)\n"
    "\n"
    "Las preferencias implícitas DEBEN inferirse cuando se sugieren con fuerza.\n"
    "\n"
    "NO separes \"qué tipo de persona es el usuario\" de \"qué sucedió\".\n"
    "Ambas cosas van en `memory`.\n"
    "\n"
    "---\n"
    "\n"
)

# (old, new) pairs applied in order after the section swap.
NO_INTENTION_REPLACEMENTS = (
    (
        "Cada memoria e intención DEBE llevar un `owner`",
        "Cada memoria DEBE llevar un `owner`",
    ),
)

INTENTION_DISABLED_NOTE = (
    "\n\n---\n\n# INTENCIONES DESACTIVADAS\n"
    "La extracción de intenciones está desactivada. Aunque los ejemplos few-shot muestren "
    "`intentions`, **no** emitas esa clave; pon los planes futuros en `memory`.\n"
)

# Coding / working extractor: wraps the history and the new messages.
CODING_CONVERSATION_BLOCK = (
    "ÚLTIMOS 5 TURNOS DEL USUARIO (SOLO CONTEXTO; no vuelvas a extraerlos):\n"
    "{history}"
    "\n\nMENSAJES NUEVOS (extrae solo de esta escritura completa):\n"
    "{new}"
)
CODING_EMPTY_BLOCK = "(ninguno)"

# Name line at the top of a multi-person conversation. Also stored in L1_RAW,
# so pipelines.base parses every language's form back.
USER_NAME_LINE = "El usuario se llama {name}"

# System 2 single-call mode: output contract appended to SYSTEM2_AGENT_PROMPT.
SYSTEM2_OUTPUT_INSTRUCTIONS = """

## Formato de salida

NO llames a tools. Devuelve directamente un array JSON de operaciones, envuelto en un bloque de código ```json.

Operaciones disponibles:

1. **create_schema** — Crea un nuevo Schema de comportamiento L6
   ```{"op": "create_schema", "schema_kind": "behavior_pattern", "content": "patrón conciso de cara al usuario", "retrieval_text": "comportamiento más las situaciones relevantes", "evidence_list": ["fact_node_id_1", "fact_node_id_2"], "tags": ["tag1"]}```

2. **add_evidence** — Añade evidencia a un Schema existente (el contenido sigue siendo inmutable)
   ```{"op": "add_evidence", "node_id": "existing_schema_node_id", "evidence_list": ["new_fact_id_1"]}```

3. **revise_schema** — Crea una nueva versión cuando la nueva evidencia cambia sustancialmente el estado actual y retira la versión antigua de la recuperación activa. No la uses para detalles compatibles.
   ```{"op": "revise_schema", "node_id": "old_schema_id", "schema_kind": "explicit_state", "content": "perfil actual actualizado", "retrieval_text": "texto de recuperación actualizado", "evidence_list": ["new_fact_id_1"], "tags": ["tag1"], "reason": "por qué la redacción antigua induciría a error"}```

4. **add_edge** — Crea una relación entre dos Schemas
   ```{"op": "add_edge", "source_id": "schema_id_1", "target_id": "schema_id_2", "reason": "motivo de la relación"}```

5. **skip_fact** — Marca explícitamente un hecho candidato como revisado sin crear
   un Schema. Úsalo solo para hechos sobre otra persona, eventos pasajeros, consejos del
   asistente que no establecen un rasgo del usuario, o hechos ya cubiertos por completo.
   ```{"op": "skip_fact", "node_id": "candidate_fact_id", "reason": "no es un patrón duradero del usuario"}```

## Contrato de salida

1. La salida DEBE ser un array JSON envuelto en un bloque de código ```json.
2. Si los datos no bastan para obtener conclusiones fiables, devuelve `[]`. En una
   revisión source-context, `[]` significa que se examinaron todos los candidatos de esa revisión
   y ninguno respalda un Schema duradero.
3. NINGÚN texto fuera del bloque de código.
4. Los IDs de evidence_list deben proceder de los node_ids de hechos proporcionados en los materiales.
5. El node_id de add_evidence / revise_schema debe proceder de IDs de nodos existentes del Graph.
6. En una revisión source-context, cada hecho candidato debe aparecer en un evidence_list o en un skip_fact."""

# Appended to SYSTEM2_OUTPUT_INSTRUCTIONS when the schema evidence gate is on.
SYSTEM2_SCHEMA_EVIDENCE_GATE = """

7. L6 representa solo patrones de comportamiento transversales a varios eventos. create_schema debe usar evidencia propia del usuario (user-owned) que represente al menos dos escrituras raw o sesiones independientes. Un único hecho reconciliado puede bastar cuando source_raw_memory_ids contiene varias escrituras independientes.
8. No crees L6 para una única identidad explícita, estado de salud, uso de equipamiento, afiliación, proyecto o preferencia. Esos hechos los gestiona la proyección de estado canónica de L2."""

# (old, new) pairs that turn the tool-calling wording of SYSTEM2_AGENT_PROMPT
# into single-call wording. Each `old` must occur in this language's prompt
# (or be harmless if it does not).
SYSTEM2_SINGLE_CALL_REPLACEMENTS = (
    ("- Todas las operaciones sobre el Graph DEBEN pasar por las tools\n", ""),
    ("llama a `add_evidence`", "usa la operación add_evidence"),
    ("usa `add_edge` (RELATED_TO)", "usa la operación add_edge"),
)

# System 2 user message (materials). Keys are fixed; values are format
# strings with the named fields shown.
SYSTEM2_MATERIALS = {
    "title": "## Materiales preprocesados\n",
    "stats": (
        "### Estadísticas\n- Total de hechos en la VDB: {total}\n"
        "- Hechos nuevos (por procesar): {fresh} (excluidos_con_evidencia={evidenced})\n"
        "- Clústeres encontrados: {clusters}\n"
        "- Hechos en clústeres: {clustered}, complemento sin clúster: {unclustered}\n"
        "- Total de Schemas en el Graph: {graph_total} (recuperados={recalled})"
    ),
    "graph_status_empty": "- Estado del Graph: **VACÍO** (primera ejecución de procesamiento cognitivo)\n",
    "clusters_header": "### Resultados de clústeres\n",
    "label_holistic": "{n} hechos del usuario de varias sesiones; solo patrones emocionales/terapéuticos duraderos respaldados por evidencia",
    "label_source_context": "{n} hechos candidatos; la escritura raw solo sirve para validar el sujeto/los detalles",
    "label_topic": "{n} hechos, tema",
    "raw_label": "Fragmento raw relevante por candidato",
    "holistic_note": (
        "Esta es una pasada nocturna de consolidación holística acotada. Crea como máximo "
        "{max_schemas} Schemas terapéuticos anclados. "
        "Prefiere unir desencadenante/trasfondo duradero, respuesta emocional recurrente "
        "y recurso de afrontamiento observado o restricción de decisión a través de distintas escrituras raw/sesiones. "
        "Antes de create_schema, une los raw_sources de sus IDs de evidencia y exige al menos dos "
        "IDs de fuente raw distintos; dos hechos de una misma escritura raw son un solo evento. "
        "Devuelve [] cuando la evidencia sea insuficiente.\n"
    ),
    "companion_header": "**Pistas complementarias de fuentes independientes (candidatos, no conclusiones):**",
    "unclustered_header": "### Hechos sin clúster (complementarios)\n",
    "unclustered_hint": "Los hechos de abajo no se agruparon en ningún clúster anterior. Revísalos después de los temas de los clústeres; tienen menor prioridad que los resultados de clústeres.\n",
    "forward_header": "### Nodos del Graph relacionados (recuperados mediante búsqueda por centroide de clúster, {count} resultados)\n",
    "graph_empty": "### Nodos existentes del Graph\nEl Graph está VACÍO actualmente: esta es la primera ejecución de procesamiento cognitivo. NO busques en el Graph (no devolverá nada). Pasa directamente a crear Schemas a partir de los resultados de clústeres.\n",
    "graph_no_recall": (
        "### Nodos existentes del Graph\n"
        "El Graph tiene {graph_total} Schemas, "
        "pero la recuperación vectorial basada en centroides no encontró coincidencias. Decide, a partir de los resultados de clústeres, si se necesitan Schemas nuevos.\n"
    ),
    "reverse_header": "### Referencias inversas del Graph (hechos de la VDB referenciados por Schemas existentes)\n",
    "tags_header": "### Etiquetas existentes (prefiere reutilizarlas antes que crear sinónimos)\n",
    "closing": (
        "Analiza los materiales anteriores y devuelve un array JSON de operaciones. "
        "Decide si crear Schemas nuevos o añadir evidencia a los existentes. "
        "Si los datos no bastan para obtener conclusiones fiables, devuelve un array vacío `[]`. "
        "Al etiquetar, prefiere reutilizar las etiquetas de la lista de etiquetas existentes anterior."
    ),
}
