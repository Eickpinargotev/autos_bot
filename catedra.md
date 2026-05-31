# Catedra sobre puertos, dominios, Docker, EasyPanel y despliegue de este proyecto

Este documento explica desde la base que esta pasando cuando levantas este proyecto con Docker y cuando luego lo subes a un servidor con EasyPanel. La idea no es solo saber que linea tocar, sino entender el modelo mental completo: que es un puerto, que significa exponer un servicio, que papel juega un dominio, que hace un reverse proxy, que guarda Docker en volumenes y por que EasyPanel puede darte una URL publica sin que tu tengas que publicar manualmente cada puerto.

## 1. La idea original: un servidor, una IP y muchos servicios

En internet, una maquina se identifica por una direccion IP. Por ejemplo:

```text
192.0.2.10
```

Pero una misma maquina puede ejecutar muchos programas al mismo tiempo:

- Un servidor web.
- Una base de datos.
- Un panel administrativo.
- Un servicio de cache.
- Una API.
- Un bot.

Entonces aparece una pregunta natural: si todos viven en la misma IP, como sabe el sistema operativo a que programa debe enviar cada conexion?

La respuesta historica y tecnica es: **puertos**.

Un puerto es un numero que identifica una puerta logica dentro de una IP. La IP identifica la maquina; el puerto identifica el servicio dentro de esa maquina.

Ejemplo:

```text
192.0.2.10:80     -> servidor web HTTP
192.0.2.10:443    -> servidor web HTTPS
192.0.2.10:5432   -> PostgreSQL
192.0.2.10:6379   -> Redis
192.0.2.10:8080   -> una app web alternativa
```

La combinacion importante es:

```text
IP + puerto
```

Dos programas no pueden escuchar al mismo tiempo en la misma IP y el mismo puerto. Si un proceso ya esta usando `0.0.0.0:8080`, otro proceso no puede usar tambien `0.0.0.0:8080`. A eso le llamamos comunmente un **choque de puertos**.

## 2. Un poco de historia: de servidores fisicos a contenedores

Antes, lo comun era tener un servidor fisico o virtual y configurar todo directamente sobre el sistema operativo:

- Instalar PostgreSQL en el servidor.
- Instalar Nginx o Apache.
- Instalar Node, Python, PHP, etc.
- Crear carpetas.
- Configurar servicios del sistema.
- Abrir puertos en el firewall.

Esto funcionaba, pero tenia varios problemas:

- Era facil que dos proyectos se pisaran dependencias.
- Migrar de un servidor a otro era incomodo.
- Reproducir en local lo mismo que habia en produccion era dificil.
- Actualizar una parte podia romper otra.

Luego se popularizaron las maquinas virtuales, que permitian aislar sistemas completos. Una VM tiene su propio sistema operativo invitado, sus propios procesos y su propia configuracion. Eso mejoro el aislamiento, pero cada VM era relativamente pesada.

Docker popularizo otra idea: **contenedores**. Un contenedor empaqueta una aplicacion con sus dependencias, pero comparte el kernel del sistema anfitrion. Es mas liviano que una VM y permite declarar la infraestructura de una app de forma bastante reproducible.

En vez de decir:

> "Instala PostgreSQL, luego instala Redis, luego configura Python..."

Puedes decir:

```yaml
services:
  postgres:
    image: postgres:16-alpine

  redis:
    image: redis:7-alpine

  nocodb:
    image: nocodb/nocodb:2026.05.0
```

Eso es mucho mas claro, portable y mantenible.

## 3. Docker Compose: varios contenedores como una sola aplicacion

Docker por si solo puede ejecutar contenedores individuales. Docker Compose permite definir varios servicios en un archivo `docker-compose.yml`.

Tu proyecto tiene varios servicios:

- `postgres`: base de datos PostgreSQL.
- `redis`: cache y broker temporal.
- `qdrant`: base vectorial.
- `nocodb`: panel visual tipo Airtable sobre bases/tablas.
- `evolution_go`: servicio de WhatsApp/Evolution.
- `bot_agent`: tu bot/agente principal.
- `celery_worker`: worker para tareas en segundo plano.
- `whatsapp_webhook`: API que recibe eventos/webhooks.
- `evolution_go_db_init`: contenedor temporal que crea bases necesarias para Evolution.

Compose crea una red interna para estos servicios. Dentro de esa red, cada contenedor puede llamar a otro usando el nombre del servicio.

Por eso dentro de Docker funciona esto:

```text
postgres:5432
redis:6379
qdrant:6333
nocodb:8080
evolution_go:8080
whatsapp_webhook:8010
```

Esto es clave: **los contenedores no necesitan usar `localhost` para hablar entre ellos**. Usan el nombre del servicio.

Ejemplo de tu proyecto:

```yaml
POSTGRES_URL=postgresql://usuario:password@postgres:5432/base
NOCODB_URL=http://nocodb:8080
REDIS_URL=redis://redis:6379/0
QDRANT_URL=http://qdrant:6333
EVOLUTION_GO_URL=http://evolution_go:8080
```

Eso significa:

> "Desde este contenedor, conectate al contenedor llamado `postgres`, en su puerto interno `5432`."

No significa:

> "Conectate al puerto 5432 publico del servidor."

Son mundos distintos.

## 4. Puerto interno vs puerto externo

Esta es una de las distinciones mas importantes.

Un contenedor puede escuchar en un puerto interno. Por ejemplo NocoDB escucha dentro del contenedor en:

```text
8080
```

Pero eso no significa automaticamente que tu puedas abrir desde tu Mac:

```text
http://localhost:8080
```

Para poder acceder desde fuera del contenedor, Docker necesita publicar ese puerto. Eso se hace con `ports`.

Ejemplo:

```yaml
nocodb:
  ports:
    - "8080:8080"
```

La estructura es:

```text
PUERTO_EXTERNO:PUERTO_INTERNO
```

En este caso:

```text
localhost:8080 -> contenedor nocodb:8080
```

Otro ejemplo:

```yaml
evolution_go:
  ports:
    - "4000:8080"
```

Significa:

```text
localhost:4000 -> contenedor evolution_go:8080
```

El servicio dentro del contenedor sigue escuchando en `8080`, pero desde fuera lo ves en `4000`.

## 5. Que pasa si no publicas un puerto

Si quitas:

```yaml
ports:
  - "8080:8080"
```

NocoDB sigue funcionando dentro de la red Docker. El bot podria seguir llamando a:

```text
http://nocodb:8080
```

Pero tu navegador local ya no podria entrar por:

```text
http://localhost:8080
```

Es decir:

- **Sin `ports`:** el servicio vive dentro de Docker.
- **Con `ports`:** el servicio tambien queda publicado hacia la maquina anfitriona.

## 6. Puertos automaticos

Docker puede asignar un puerto externo automaticamente.

En vez de:

```yaml
ports:
  - "8080:8080"
```

puedes usar:

```yaml
ports:
  - "8080"
```

Eso significa:

```text
Publica el puerto interno 8080 en algun puerto libre del host.
```

Docker podria asignar algo como:

```text
0.0.0.0:32791 -> contenedor:8080
```

Ventaja:

- Evita choques de puertos.

Desventaja:

- No sabes de memoria que puerto quedo asignado.
- Debes revisarlo con Docker/EasyPanel.
- Es incomodo si quieres abrir manualmente `IP:PUERTO`.

Por eso los puertos automaticos tienen sentido para pruebas o para casos donde no te importa entrar por `IP:PUERTO`. Pero si vas a usar dominios con EasyPanel, muchas veces ni siquiera necesitas publicar esos puertos.

## 7. Dominios: nombres humanos para llegar a una IP

Una IP es dificil de recordar. Por eso existen los dominios.

Cuando escribes:

```text
https://nocodb.tudominio.com
```

el navegador pregunta al sistema DNS:

```text
Que IP tiene nocodb.tudominio.com?
```

El DNS responde algo como:

```text
203.0.113.50
```

Entonces tu navegador conecta a esa IP.

Normalmente, si usas HTTPS, conecta al puerto:

```text
443
```

Y si usas HTTP, conecta al puerto:

```text
80
```

Por eso una URL normal no muestra puerto:

```text
https://nocodb.tudominio.com
```

Pero implicitamente esta usando:

```text
nocodb.tudominio.com:443
```

## 8. El problema: varios servicios web, un solo puerto 443

Supongamos que tienes en el mismo servidor:

```text
https://nocodb.tudominio.com
https://evolution.tudominio.com
https://webhook.tudominio.com
https://otra-app.tudominio.com
```

Todos usan HTTPS. Todos llegan al mismo servidor. Todos entran por el puerto 443.

Entonces, como sabe el servidor a que contenedor mandar cada peticion?

Aqui entra el **reverse proxy**.

## 9. Reverse proxy: el portero profesional

Un reverse proxy es un servicio que recibe trafico web publico y lo enruta hacia servicios internos.

Herramientas comunes:

- Nginx.
- Traefik.
- Caddy.
- HAProxy.

Su trabajo es mirar la peticion entrante y decidir:

```text
Si viene para nocodb.tudominio.com -> manda a nocodb:8080
Si viene para evolution.tudominio.com -> manda a evolution_go:8080
Si viene para webhook.tudominio.com -> manda a whatsapp_webhook:8010
```

Visualmente:

```text
Internet
   |
   | https://nocodb.tudominio.com
   v
Servidor:443
   |
   v
Reverse proxy
   |
   v
nocodb:8080
```

Y para otro dominio:

```text
Internet
   |
   | https://webhook.tudominio.com
   v
Servidor:443
   |
   v
Reverse proxy
   |
   v
whatsapp_webhook:8010
```

Esto permite que muchos servicios compartan el mismo puerto publico `443`, siempre que tengan dominios/subdominios distintos.

## 10. Entonces, para que sirven los `ports` si tengo dominio?

Sirven para publicar un servicio directamente como:

```text
http://IP_DEL_SERVIDOR:8080
```

Pero si usas reverse proxy con dominio:

```text
https://nocodb.tudominio.com
```

no necesitas necesariamente publicar:

```yaml
ports:
  - "8080:8080"
```

El proxy puede hablar con el contenedor por la red interna de Docker.

La diferencia queda asi:

```text
Acceso por IP:PUERTO
Necesita ports:
http://IP_DEL_SERVIDOR:8080

Acceso por dominio/proxy
No necesariamente necesita ports:
https://nocodb.tudominio.com
```

## 11. Que es EasyPanel

EasyPanel es una plataforma/panel para desplegar aplicaciones en servidores. En terminos practicos, puedes pensarlo como una capa visual encima de Docker y herramientas de despliegue.

EasyPanel normalmente te ayuda con:

- Crear servicios/contenedores.
- Configurar variables de entorno.
- Asociar dominios.
- Gestionar certificados SSL.
- Ver logs.
- Reiniciar servicios.
- Manejar volumenes.
- Conectar proyectos a repositorios o archivos Compose.

No debes pensar en EasyPanel como "magia que reemplaza Docker". Es mas correcto pensarlo como:

> Un panel que administra contenedores y te facilita el acceso publico mediante dominios y proxy.

## 12. Es EasyPanel Kubernetes?

No en el sentido clasico.

Kubernetes es un orquestador de contenedores mucho mas grande. Trabaja con conceptos como:

- Clusters.
- Pods.
- Deployments.
- Services.
- Ingress.
- ConfigMaps.
- Secrets.
- Autoscaling.
- Nodes.

EasyPanel, en cambio, suele ser mas directo y sencillo para proyectos de un servidor o despliegues menos complejos:

```text
Docker / Docker Compose + panel visual + dominios + SSL + proxy
```

Comparacion:

```text
Docker Compose
Define contenedores, redes y volumenes en un archivo.

EasyPanel
Administra contenedores/proyectos y expone servicios con dominios.

Kubernetes
Orquesta aplicaciones a escala de cluster con muchos componentes.
```

Para tu caso, EasyPanel es suficiente y mucho mas simple que Kubernetes.

## 13. Local vs servidor: dos mundos parecidos, no identicos

En local, tu maquina es la anfitriona. Si publicas:

```yaml
ports:
  - "8080:8080"
```

accedes con:

```text
http://localhost:8080
```

En un servidor, si publicas:

```yaml
ports:
  - "8080:8080"
```

accedes con:

```text
http://IP_DEL_SERVIDOR:8080
```

Pero si EasyPanel configura un dominio con proxy, puedes acceder con:

```text
https://nocodb.tudominio.com
```

En este caso, EasyPanel recibe la peticion y la manda al contenedor correcto.

## 14. Por que algunos servicios no deberian exponerse publicamente

No todos los servicios deben estar abiertos a internet.

Servicios que normalmente **si pueden necesitar URL publica**:

- `nocodb`, si quieres administrarlo desde tu navegador.
- `evolution_go`, si necesitas entrar a su API/panel desde fuera.
- `whatsapp_webhook`, si debe recibir webhooks desde servicios externos.

Servicios que normalmente **no deberian exponerse publicamente**:

- `postgres`.
- `redis`.
- `qdrant`, salvo que tengas una razon muy especifica y controles seguridad.

PostgreSQL, Redis y Qdrant son infraestructura interna. Tu app los necesita, pero el publico no.

Si publicas PostgreSQL asi:

```yaml
ports:
  - "5432:5432"
```

entonces el servidor puede aceptar conexiones externas en:

```text
IP_DEL_SERVIDOR:5432
```

Eso puede ser util para administrar desde TablePlus/DBeaver, pero tambien aumenta el riesgo si no configuras firewall, usuarios fuertes, contrasenas fuertes y reglas de acceso.

En produccion, suele ser mejor que esos servicios solo vivan dentro de la red Docker.

## 15. Volumenes: donde vive lo que no quieres perder

Los contenedores son reemplazables. Los datos importantes no deberian vivir solo dentro de la capa temporal del contenedor.

Por eso existen los volumenes.

En tu proyecto:

```yaml
postgres:
  volumes:
    - postgres_data:/var/lib/postgresql/data
```

Esto significa:

```text
Los datos reales de PostgreSQL viven en un volumen Docker llamado postgres_data.
```

Si destruyes y recreas el contenedor de Postgres, el volumen puede seguir existiendo. Por eso los datos no se borran simplemente por hacer redeploy.

Tambien tienes:

```yaml
redis:
  volumes:
    - redis_data:/data
```

Y:

```yaml
qdrant:
  volumes:
    - ./data/qdrant_storage:/qdrant/storage
```

Y:

```yaml
nocodb:
  volumes:
    - ./data/nocodb_metadata:/usr/app/data
```

Hay dos estilos aqui:

### Volumen nombrado

Ejemplo:

```yaml
postgres_data:/var/lib/postgresql/data
```

Docker administra ese volumen fuera de la carpeta del proyecto.

### Carpeta montada

Ejemplo:

```yaml
./data/nocodb_metadata:/usr/app/data
```

Aqui la data vive dentro de la carpeta del proyecto, en `data/nocodb_metadata`.

## 16. Que pasa si redeployas en EasyPanel

Si haces un ajuste en el codigo y vuelves a desplegar, normalmente:

- Se recrean contenedores.
- Se actualizan imagenes o codigo.
- Se conservan volumenes persistentes.

Por eso PostgreSQL no deberia borrarse si EasyPanel conserva el volumen `postgres_data`.

Pero se podria perder informacion si:

- Eliminas el proyecto y eliges borrar volumenes.
- Cambias el nombre del volumen.
- Montas otra ruta vacia encima.
- Haces un reset de almacenamiento.
- Subes una carpeta `data` vieja y reemplazas metadata actual del servidor.

La regla profesional es:

> El codigo se redeploya; los datos se migran o se preservan con volumenes.

Codigo y data tienen ciclos de vida diferentes.

## 17. Tu proyecto especificamente

Tu `docker-compose.yml` actualmente publica estos puertos:

```text
postgres       5432:5432
qdrant         6333:6333
qdrant grpc    6334:6334
nocodb         8080:8080
evolution_go   4000:8080
redis          6379:6379
```

En local esto es comodo:

```text
NocoDB:       http://localhost:8080
Evolution:    http://localhost:4000
Postgres:     localhost:5432
Redis:        localhost:6379
Qdrant:       http://localhost:6333
```

Pero en EasyPanel, si vas a usar dominios, no necesitas publicar todos esos puertos al host.

El modelo recomendado en servidor seria:

```text
nocodb.tudominio.com      -> servicio nocodb, puerto interno 8080
evolution.tudominio.com   -> servicio evolution_go, puerto interno 8080
webhook.tudominio.com     -> servicio whatsapp_webhook, puerto interno 8010
```

Y dejar internos:

```text
postgres:5432
redis:6379
qdrant:6333
```

## 18. La pregunta central: hay problema con puertos externos aleatorios?

No hay problema tecnico si usas puertos externos aleatorios.

Ejemplo:

```yaml
nocodb:
  ports:
    - "8080"
```

Docker asignara un puerto libre en el servidor.

Pero si accedes por dominio de EasyPanel, ese puerto aleatorio deja de ser importante. EasyPanel puede enrutar al puerto interno `8080` del contenedor.

Entonces, profesionalmente:

```text
Si vas a acceder por IP:PUERTO:
Necesitas ports y te importa el puerto externo.

Si vas a acceder por dominio EasyPanel:
Te importa el puerto interno del contenedor, no el puerto externo.

Si el servicio es solo interno:
No publiques ports.
```

## 19. Por que quitar ports puede molestar en local

En local, si quitas:

```yaml
nocodb:
  ports:
    - "8080:8080"
```

ya no podras abrir:

```text
http://localhost:8080
```

Pero los contenedores seguiran pudiendo hablar con:

```text
http://nocodb:8080
```

Esto explica por que una configuracion buena para produccion puede ser menos comoda para desarrollo local.

## 20. La solucion elegante: dos configuraciones

Una practica profesional es tener:

```text
docker-compose.yml
docker-compose.prod.yml
```

El archivo base puede servir para local. El archivo de produccion puede ajustar:

- Puertos publicados.
- Volumenes.
- Variables.
- Politicas de seguridad.
- Dominios/proxy en EasyPanel.

Ejemplo conceptual para produccion:

```yaml
services:
  postgres:
    ports: []

  redis:
    ports: []

  qdrant:
    ports: []

  nocodb:
    expose:
      - "8080"

  evolution_go:
    expose:
      - "8080"

  whatsapp_webhook:
    expose:
      - "8010"
```

En Compose puro, sobrescribir listas como `ports` requiere cuidado. EasyPanel tambien puede manejar parte de esto desde su UI, dependiendo de como importes el proyecto.

Otra opcion es mantener un solo archivo con variables:

```yaml
nocodb:
  ports:
    - "${NOCODB_HOST_PORT:-8080}:8080"
```

En local:

```text
NOCODB_HOST_PORT=8080
```

En servidor:

```text
NOCODB_HOST_PORT=18080
```

Esto evita choque, pero sigue publicando el puerto. Si quieres usar solo dominio/proxy, lo mas limpio es no publicar `ports` en produccion.

## 21. DNS y EasyPanel: que debes configurar tu

EasyPanel puede encargarse de enrutar y generar certificados, pero tu normalmente debes apuntar el dominio al servidor.

En tu proveedor DNS:

```text
Tipo: A
Nombre: nocodb
Valor: IP_DE_TU_SERVIDOR
```

Eso crea:

```text
nocodb.tudominio.com -> IP_DE_TU_SERVIDOR
```

Luego en EasyPanel configuras:

```text
Dominio: nocodb.tudominio.com
Servicio: nocodb
Puerto interno: 8080
```

Para webhook:

```text
Dominio: webhook.tudominio.com
Servicio: whatsapp_webhook
Puerto interno: 8010
```

Para Evolution:

```text
Dominio: evolution.tudominio.com
Servicio: evolution_go
Puerto interno: 8080
```

## 22. Un detalle importante sobre webhooks

Dentro de tu Compose tienes:

```yaml
WEBHOOK_URL: http://whatsapp_webhook:8010/webhooks/evolution-go?token=${EVOLUTION_WEBHOOK_TOKEN}
```

Eso es una URL interna de Docker.

Si `evolution_go` llama al webhook desde dentro de la misma red Docker, esta bien:

```text
http://whatsapp_webhook:8010
```

Pero si un servicio externo necesita llamar al webhook desde internet, no puede usar `whatsapp_webhook`, porque ese nombre solo existe dentro de Docker.

Desde internet deberia usar algo como:

```text
https://webhook.tudominio.com/webhooks/evolution-go?token=...
```

La regla:

```text
Entre contenedores: usa nombres de servicio Docker.
Desde internet: usa dominio publico.
```

## 23. NocoDB y sus tablas en este proyecto

Tu NocoDB monta:

```yaml
./data/nocodb_metadata:/usr/app/data
```

Dentro de esa carpeta existe:

```text
data/nocodb_metadata/noco.db
```

Ese archivo contiene la metadata local de NocoDB y, en este caso, tambien se ven tablas propias de NocoDB como:

```text
nc_zu4t___FAQs
nc_zu4t___Invitacion_publicidad
nc_zu4t___No answer
nc_zu4t___Registros
nc_zu4t___reportes
nc_qb3f___mensajes_record
```

Eso significa que si subes esa carpeta como parte del proyecto, NocoDB puede arrancar con sus tablas y configuraciones ya existentes.

Pero en produccion hay que tener cuidado:

```text
Si el servidor ya tiene datos nuevos en NocoDB y luego subes una carpeta data/nocodb_metadata vieja desde local, puedes pisar lo que habia en el servidor.
```

Por eso, una vez en produccion, conviene tratar `data/nocodb_metadata` como dato persistente del servidor, no como codigo que se reemplaza en cada deploy.

## 24. La regla de oro: separar codigo, configuracion y datos

En despliegues profesionales se separan tres cosas:

### Codigo

Ejemplos:

```text
services/bot_agent
docker-compose.yml
Dockerfile
requirements.txt
mensajes.json
```

Se puede actualizar con redeploy.

### Configuracion/secrets

Ejemplos:

```text
.env
OPENAI_API_KEY
TELEGRAM_BOT_TOKEN
POSTGRES_PASSWORD
EVOLUTION_GO_API_KEY
```

En produccion deberian configurarse como variables secretas en EasyPanel, no subirse alegremente a repositorios.

### Datos

Ejemplos:

```text
postgres_data
redis_data
data/nocodb_metadata
data/qdrant_storage
```

Se preservan, se respaldan y se migran con cuidado.

## 25. Recomendacion practica para este proyecto

Para desarrollo local, mantener puertos es comodo:

```text
NocoDB por localhost:8080
Evolution por localhost:4000
Postgres por localhost:5432 si usas cliente externo
Qdrant por localhost:6333 si inspeccionas
Redis por localhost:6379 si depuras
```

Para EasyPanel/produccion:

- Exponer por dominio solo lo que necesites acceder desde fuera.
- Usar dominio para `nocodb`.
- Usar dominio para `whatsapp_webhook` si debe recibir llamadas externas.
- Usar dominio para `evolution_go` si realmente necesitas acceso externo.
- No publicar Postgres, Redis y Qdrant salvo que tengas una razon concreta.
- No borrar volumenes al redeployar.
- No reemplazar datos de produccion con carpetas locales viejas.

## 26. Mapa mental final

```text
LOCAL

Tu navegador
   |
   | http://localhost:8080
   v
Docker host publica puerto 8080
   |
   v
nocodb:8080
```

```text
PRODUCCION CON IP:PUERTO

Tu navegador
   |
   | http://IP_DEL_SERVIDOR:8080
   v
Servidor publica puerto 8080
   |
   v
nocodb:8080
```

```text
PRODUCCION CON EASYPANEL + DOMINIO

Tu navegador
   |
   | https://nocodb.tudominio.com
   v
Servidor:443
   |
   v
EasyPanel / reverse proxy
   |
   v
nocodb:8080
```

```text
COMUNICACION INTERNA ENTRE CONTENEDORES

bot_agent
   |
   | http://nocodb:8080
   v
nocodb

bot_agent
   |
   | postgresql://postgres:5432
   v
postgres
```

## 27. Conclusion profesional

Los puertos externos no son la esencia de la comunicacion entre tus servicios. Son solo una forma de publicar servicios hacia fuera usando `IP:PUERTO`.

En Docker, tus contenedores hablan entre si por nombres de servicio y puertos internos. En EasyPanel, el acceso publico moderno se resuelve normalmente con dominios, HTTPS y un reverse proxy que enruta hacia el puerto interno correcto.

Por eso:

- En local, los `ports` son utiles para comodidad.
- En produccion, los dominios de EasyPanel son la forma limpia de exponer servicios web.
- Los servicios internos como Postgres, Redis y Qdrant no necesitan puerto publico.
- Los datos importantes viven en volumenes o carpetas persistentes y no deberian tratarse como codigo reemplazable.

Si entiendes esto, ya tienes el modelo mental correcto para desplegar proyectos Docker sin miedo: sabes diferenciar red interna, puerto externo, dominio publico, reverse proxy, volumen persistente y ciclo de vida de datos. Esa es la base real.
