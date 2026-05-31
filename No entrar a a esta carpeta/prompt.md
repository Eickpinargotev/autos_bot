# PORMPT

vamos a trabajar en un agente de chat con IA para una escuela de manejo.
Tenmos que contruir un sistema basado en la teoria de maquina de estados finito.
nos vamos a conectar a travez de webhook a evolution API y vamos a recibir mensajes de whatsapp que seran los disparadores de las acciones segun el estado en el que se encuentre el flujo justo en el momento en el que se recibe el mensaje.
El estado en el que todo numero inicia se llama INICIO.

Antes de pasar a los estados sus caracteristicas hablemos sobre que caracteristicas tienen los mensajes que llegan y como deben ser tratados cada uno.

Lo primero que hay que tener en cuenta, es que de whatsapp podemos recibir mensajes de todo tipo: texto, stickers, emojis, videos, imagenes, documentos, etc… Pero nosotros solo tomaremos en cuenta texto, en caso de recibir un audio este será transcrito a texto, en caso de recibir una imagen, se debe enviar un mensaje diciendo: “No podemos ver imagenes, si deseas que alguien la revise, avísame y te contacto con un asesor”, si envian documentos es el mismo proceso, se envia un mensaje que diga lo mismo pero para documento, de ahi cualquier otro tipo de mensaje se ignora, no se realiza ninguna accion en ningun estado. Entonces, solo procesamos texto, audio, imagenes y documentos (se envia un mensaje informativo) y se ignora el resto de tipos de mensaje. Nota: solo se envian 2 mensajes informativos cada 5 minutos, si envian mensajes que requieren el envio de un mensaje informativo, pero ya se exedio el limite de mensajes informativos enviados, se debe solicitar ayuda con la razón: ”el usuario insiste enviado varias imagenes”.

Ahora la siguiente consideracion con los mensajes es que debe haber un buffer de mensajes, nosotros recibimos mensajes y los almacenamos hasta que el usuario no haya enviado otro mensaje en [X] segundos, este parametro es configurable en una variable de entorno. una vez que no se haya recibido ningun mensaje nuevo en [X] segundos, se procesa todo el contenido del buffer como un solo mensaje.

Nota hay un dos tipos especiales de mensajes que podemos recibir y que se procesan de forma especial y son:

Ingreso a grupos: Cuando un usuario ingresa a un grupo, recibimos un webhook, mas adelante estan las indicaciones en como proceder antes el ingreso de un cliente a un grupo. Por otro lado, tambien tenemos mensajes de estudiantes que vienen de una campaña publicitaria, asi mismo mas adelante se especifica como proceder en este caso.Desde ahora en adelante nos referiremos con “texto” al mensaje unitario del cliente, es decir, solo texto, pero unitario y por otro lado nos referiremos a “mensaje” a la tanda de mensaje o grupo de mensajes que pasaron por el buffer y están listos para ser procesado, asumiendo lo explicado (solo procesamos texto) y asumiendo que ya es la tanda de mensajes que se pasaron por el proceso del buffer.

EN CUALQUIER ESTADO: 

Cuando recibimos un texto en cualquier estado debemos revisar si tienen algunas de estos comandos:

- “/d”: este comando limpia el número y lo deja como nuevo, es decir, elimina el historial, se cambia al estado INICIO, se borra el historial del numero al cual esta relacionada y en genera se deja el numero limpio, como nuevo.
- “tareas/transporte”: este comando enviamos los mensaje del grupo de “KEYWORD” dependiendo del caso y prepara 3 meses porgamados para ser enviamos, cada mensaje tiene su tiempo de envío desde el momento en el que el cliente envío el comando, si el usuario envia la palabras clave “tareas” se envia T1, si envía “transporte” se envía H1 y en ambos casos, los mensajes T2,T3 Y T4 se programan para ser enviados según los tiempos que tiene programados en el parámetro “segundos” del mensaje, nota si el usuario contesta a algunos de esos mensajes, se genera un reporte (solo 1, recuerda que los reportes bloquean al usuario por 12 días), sin embargo, los mensaje programados se continuan enviado.
- “/block #”: este comando bloquea un numero para siempre, este numero debe ser anadido a una base de datos de numeros bloqueados en postgresSQL.

Como pudiste notar, hay comandos para bloquear numeros, por eso lo primero que hacemos cuando recibimos un texto es revisar si trae un comando y luego revisar si no está bloqueado, sino no esta bloqueado y no contiene un comando, entonces pasa a almacenarce en el buffer para continuar con el proceso, pero si esta bloqueado, se ignora el mensaje, al menos que se este esperando una respuesta para generar un reporte, nota que si el sistema esta esperando un mensaje de un cliente para generar un reporte pero llega un mensaje “from_me” es decir que el dueño del numero escribió al cliente, se bloquea el numero como siempre y no se envía el reporte que esta programado. nota que las palabras claves tarea y transporte reciben un trato especial, los mensajes quedan programados, la única forma de que se detengan esos envíos es usando el comando “/d” o “/block”, con cualquier da esos dos comando, el numero se resetea y quedan como nuevos, como si nunca hubieran enviado un mensaje.

Quiero que revises el archivo @mensaje.json y @flujo.mmd, en @flujo.mmd encuentras el flujo de mensajes y sus condicione. Quiero que manejes esto usando la teoría de maquina de estado finita, cada estado debe tener un agente que permita controlar el flujo o contestar preguntas usando un sistema RAG, pero siempre dejando la ultima pregunta del mensaje enviado para retomar el flujo.
Acá te dejo indicaciones para que sepas cuando un mensaje entra a cada flujo

Para el flujo DICTAMEN

```
Si el mensaje tiene algunas de las siguientes palabras claves:
Examen médico, Dictamen, Prueba médica, Cita dictamen, Requisitos dictamen, Formulario dictamen
```

para el flujo CLASES

```
Si el mensaje tiene algunas de las siguientes palabras claves:
Clases, Clases de manejo, Manejo, Lecciones, Practica, Conducción
NOTA: Eviita frases que contengan la palabra teórico, eso va en la categoria general.
```

para el flujo GENERAL

```
Si el mensaje tiene algunas de las siguientes palabras claves:
- teórico, examen teórico, cita teórico, preparación teórico, curso teórico, información del curso
- prueba de manejo, cita de manejo, mi prueba es en
- agendamiento (y las variaciones de esta palabra) 
- COSEVI, MOPT
- licencia (y las variaciones de esta palabra) 
FRASES O MENSAJES CON INTENCIÓN DE OBTENER INFORMACIÓN
Ocupo información
Quiero información
Que hay que hacer?
etc...
```

para el flujo ALQUILER

```
Si el mensaje tiene algunas de las siguientes palabras claves:
- Alquiler (y las variaciones de esta palabra)
- prueba de manejo
- auto, carro, moto, bus, camión, trailer
- B1, B2, B3, B4, A1, A2, A3
```

Para el flujo QUEJAS

```
Si en el mensaje claramente se nota que el cliente se está quejando:
posibles palabras claves:
- queja, molestía
- devolucion
- problemas
- palabras inadecuadas
- insultos
SIGNOS SENTIMENTALES EN EL MENSAJES
- enojo
- Frustracion
- Insatisfacción

```

Para el flujo WIN:

```
Si el cliente expresa que ha ganado Disparadores: éxito/aprobación: “gané”, “aprobé”, “pasé”, “me fue bien” + (teórico/práctico/examen/prueba) Ej.: “ya aprobé el teórico”, “pasé la prueba práctica” Bloqueadores / matices: Negación (“no he ganado”, “no aprobé”) → suele ir a licencias (preparación/cita)
```

Si no hace match con ninguna, debe entrar al flujo general.
Ahora esto si el mensaje no entra ni en la categoria: palabra clave, publicidad o ingreso a grupo.

Ya sabes que hacer en la categoria palabra clave, acá te dejo indicaciones para la categoria publicidad.

Primero antes de continuar no olvides que debes tener una capa en el proyecto en la que se programen los canales, es decir por ejemplo telegram o whatsapp, el proyecto debe conectarse a estos canales de forma que si cambio de canal, no tengo que cambiar el codigo, porque el codigo sigue funcionando igual, lo unico que es necesario cambiar el programa del canal, para que se pueda comunicar correctamente con el sistema agente.
entonces dicho esto.

tendremos 3 canales, el primero será telegram, como sabes en telegram no hay grupo, asi que un mensaje viene de publicidad si ingresa el texto asi add[”texto de publicidad”], cualquier mensaje aqui dentro es el mensaje de publicidad y cuando envíe grupo[””entra al grupo”] significa que el usuario ingreso al grupo.
tambien puede ingresar por varios canales de whatsapp, se diferencian en como llegan los webhooks, el formato json con el que se llega y el formato que espera para enviar mensaje y en general para comunicarse de regreso. por ahora tendremos 2, pero aquí vamos a trabajar con uno de ellos, le vamos a llamar a este canal evo_go.

cuando un mensaje entra de publicidad se siguen los sigueintes pasos:
Se envía el mensaje correspondiente a la palabra clave que se encuentre en el mensaje que recibimos de la publicidad, estos los encontrarás en nocoDB, eso lo vemos luego y hacemos pruebas luego usando Telegram, pero por ahora ya sabes que se enviará un grupo de mensajes, además de esto, el numero se debe registrar en otra tabla de nocoDB, donde se registrarán los siguientes campos basados en la tabla de invitaciones exactamente la información se saca del primer campo “PRIMER MENSAJE” este mensaje debe pasar por un LLM para que extraiga la siguiente información: día (fecha en la que inicia el curso en este formato dd/mm/2026), valor(valor del curso en colones que cuesta el curso), hora(hora a la que inicia el curso en formato 24 horas HH:MM). con estos datos armamos el tercer mensaje programado.
aqui te dejo un ejemplo de como quedaría el mensaje junto con la información, nota que el link de whatsapp siempre está en el cuarto mensaje.

```
📌Hola!!!

Le comparto la información de nuestro curso en Alajuela.

Fecha: Sábado 21 de marzo 

Hora: 5:00 de la tarde 

Valor: 15000 colones

Unirse al grupo: https://chat.whatsapp.com/BafqwTRyryyBKFmhcbDf04?mode=gi_t

Le esperamo
```

necesito que tu lo armes de forma mas simple y directa.
ahora quiero que tengas en cuenta el resto de mensajes, el primero y el 

primer mensaje: “📌 Hola!!!  No recibí respuesta a nuestra conversación.  Podemos continuar???”
segundo mensaje: ”👋🏻Vi que no se unió al grupo👋🏻\n\n*¿¿¿Tiene alguna duda duda antes de unirse al grupo???”
tercer mensaje (ejemplo):*  “📌Hola!!!\n\nLe comparto la información de nuestro curso en Cañas.\n\nFecha: Sábado 21 de febrero\n\nHora: 6:00 de la tarde\nValor: 15000 colones\n\nUnirse al grupo: [https://chat.whatsapp.com/IezJIlnPb960HSt7ReMMzB](https://chat.whatsapp.com/IezJIlnPb960HSt7ReMMzB)\n\nLe esperamos”
Nota: Si falta alguno de esos datos, no se podrá armar el tercer mensaje, asi que simplemente genera un reporte de que no se pudo generar los mensajes programados y bloquea al usuario por 12 días.
sobre los tiempos a los que se deben enviar los mensajes son:
2 horas el primer mensaje, 20 horas el segundo y 23 horas el tercero. es decir el ultimo mensaje se enviará 23 horas después de que el cliente haya venido por publicidad.
además, nota que si se registra que el usuario entro al grupo, se envía el mensaje del grupo PUBLICIDAD.PO, y se bloquea al usuario por 12 horas.
Primero programemos todo con telegram, dejamos para despues evo_go.

Algunos puntos importantes.
Quiero que generes buenos prompts para que no haya dudas y que todos los prompts los coloques en algún archivo en el que yo pueda modificarlo.

Tambien quiero que tengas en cuenta que este proyecto contendra
postgresSQL, Redis, NocoDB y qdrant.
ya tengo imagenes para cada uno.
para postgresSQL el id de la imagen es: 

sha256:4e6e670bb069649261c9c18031f0aded7bb249a5b6664ddec29c013a89310d50

Para NocoDB

sha256:98090d707ab36ad0fe62dac8c8416b4ebf6ee939a8f851676b880b5ac159a74a

Para Qdrant

sha256:94728574965d17c6485dd361aa3c0818b325b9016dac5ea6afec7b4b2700865f

falta la imagen de redis, quiero que descargues la versión que necesites.
no olvides que una vez que un usuario se bloquea, se borra todo el historial de conversación, ten cuidado que si se genera un bloqueo de usuario por reporte, ya no deben enviarse más, ten en cuenta que durante el flujo de tarea o transporte el usuario permanece boqueado, los mensaje que envía simplemente generan los reportes.

Por ahora el RAG para responder preguntas déjalo vacío, luego rellenamos, pero considera en la planificación el hecho que podemos responder preguntas durante el flujo GENERAL, CLASES y ALQUILER.
Deja preparado el entorno en nocoDB, para yo preparar todas las columnas y tablas y pasarte las información  y las indicaciones para que puedas continuar.

te dejo en el archivo .env el token de telegram y la api de open ai y el modelo con el que vamos a trabajar, tambien te dejo un poco los puestos para tener la conexion con nocodb.
El actual proyecto ya cuenta con una estructura debes remodelar todo, para que funcione perfecto según las instrucciones de este prompt.
Debes reestructurar todo de nuevo, borra cualquier archivo que no necesites y utuliza lo que necesites.