/* Nueve comportamientos, nada más: refresco de fragmentos, edición en línea,
   dos de navegación (menú lateral y menú de cuenta), copiar al portapapeles,
   avisos flotantes, ventanas <dialog>, sus categorías y el visor de
   conversaciones. Todo lo demás son formularios HTML normales con POST y
   redirección, que funcionan aunque este archivo no cargue. */

(function () {
  "use strict";

  /* 1. Fragmentos que se actualizan solos.

        <div data-vivo="reportes" data-refrescar="/reportes/lista">…</div>

        El servidor mantiene abierto /eventos y avisa por ahí de QUÉ cambió
        («reportes», «uso»…), nunca de datos. Al llegar el aviso, cada fragmento
        que escuche ese tema vuelve a pedirse. No se consulta nada mientras no
        pase nada, y una consulta del servidor sirve para todas las pestañas
        abiertas a la vez.

        `data-cada` sigue existiendo como respaldo: si el flujo no se puede
        establecer (un proxy que no lo deja pasar, un navegador antiguo), se cae
        al refresco por reloj de siempre y la pantalla nunca queda peor que
        antes. */

  var RESPALDO_TRAS = 3;     // reconexiones fallidas antes de rendirse
  var CIERRA_OCULTA = 60000; // ms de pestaña oculta antes de soltar la conexión
  var CADA_RESPALDO = 10000; // ritmo del refresco por reloj, si toca usarlo

  var flujo = null;
  var fallos = 0;
  var respaldo = null;
  var temporizadorCierre = null;
  var pendienteAlVolver = false;
  var flujoAbiertoAlgunaVez = false;

  function vivos(tema) {
    // Se buscan en el momento y no al cargar la página: así un fragmento que
    // llegó dentro de otro fragmento (o de una ventana flotante) también se
    // actualiza. Antes solo se enganchaba lo que existía al arrancar.
    return Array.prototype.filter.call(
      document.querySelectorAll("[data-refrescar]"),
      function (nodo) {
        if (!tema) return true;
        var suyos = (nodo.getAttribute("data-vivo") || "").split(/[\s,]+/);
        return suyos.indexOf(tema) >= 0;
      }
    );
  }

  /* Pintar sin estropear lo que estabas haciendo.

     Reemplazar por las bravas tiene tres problemas que se notan todos: borra lo
     que estás escribiendo en una celda, cierra la ventana que tuvieras abierta
     dentro y te devuelve al principio de la tabla. */
  function pintar(nodo, html) {
    // Lo más frecuente es que no haya cambiado nada visible: sin esto habría un
    // repintado (y su parpadeo) en cada aviso.
    if (nodo.innerHTML === html) return;

    if (nodo.contains(document.activeElement) && document.activeElement !== document.body) {
      // Estás escribiendo aquí dentro. Se aparca y se pinta al salir del campo.
      nodo.dataset.pendiente = html;
      return;
    }
    if (nodo.querySelector("dialog[open]")) {
      nodo.dataset.pendiente = html;
      return;
    }

    var desplazado = nodo.scrollTop;
    var tabla = nodo.querySelector(".tabla-scroll");
    var lateralmente = tabla ? tabla.scrollLeft : 0;

    nodo.innerHTML = html;
    delete nodo.dataset.pendiente;

    nodo.scrollTop = desplazado;
    var nueva = nodo.querySelector(".tabla-scroll");
    if (nueva) nueva.scrollLeft = lateralmente;
  }

  // Lo que quedó aparcado se pinta en cuanto sueltas el campo o cierras la
  // ventana, no en el siguiente aviso: si no, un dato viejo se quedaría en
  // pantalla hasta que volviera a cambiar algo.
  function pintarPendientes() {
    document.querySelectorAll("[data-pendiente]").forEach(function (nodo) {
      if (nodo.contains(document.activeElement) && document.activeElement !== document.body) return;
      if (nodo.querySelector("dialog[open]")) return;
      var html = nodo.dataset.pendiente;
      delete nodo.dataset.pendiente;
      pintar(nodo, html);
    });
  }
  document.addEventListener("focusout", function () { setTimeout(pintarPendientes, 0); });
  document.addEventListener("close", pintarPendientes, true);

  function refrescar(nodo) {
    var url = nodo.getAttribute("data-refrescar");
    if (!url || nodo.dataset.enVuelo === "1") return;
    nodo.dataset.enVuelo = "1";
    fetch(url, { headers: { "X-Fragmento": "1" } })
      .then(function (r) { return r.ok ? r.text() : null; })
      .then(function (html) { if (html !== null) pintar(nodo, html); })
      .catch(function () { /* un fallo puntual de red no debe romper la página */ })
      .then(function () { delete nodo.dataset.enVuelo; });
  }

  /* El chat es el único que NO se repinta: se le añade lo que llegó.

     Repintar el hilo entero perdería tres cosas a la vez — dónde ibas leyendo,
     los mensajes anteriores que habías cargado a mano y el sitio del scroll. Se
     pide solo lo posterior a `data-ultimo-id` y se pega al final.

     Bajar el scroll solo si YA estabas abajo: si subiste a leer algo, un
     mensaje nuevo no puede arrastrarte al final. */
  function estirarChat(chat) {
    if (chat.dataset.enVuelo === "1") {
      // Puede entrar otro aviso mientras todavía se está leyendo el anterior
      // (es habitual: primero se guarda lo que escribió el cliente y enseguida
      // la respuesta del bot). No se puede ignorar: si la primera consulta ya
      // salió de Postgres, ese segundo mensaje quedaría invisible hasta el
      // próximo aviso o hasta recargar la página.
      chat.dataset.colaPendiente = "1";
      return;
    }
    chat.dataset.enVuelo = "1";

    var url = chat.getAttribute("data-cola") +
              "&desde=" + encodeURIComponent(chat.dataset.ultimoId || "0") +
              "&dia=" + encodeURIComponent(chat.dataset.dia || "");

    fetch(url, { headers: { "X-Fragmento": "1" } })
      .then(function (r) { return r.ok ? r.text() : null; })
      .then(function (html) {
        if (!html || !html.trim()) return;

        var desplazador = chat.closest("[data-mensajes-scroll]") || chat;
        var abajo = desplazador.scrollHeight - desplazador.scrollTop - desplazador.clientHeight < 80;
        var antes = chat.querySelectorAll("[data-id]").length;
        chat.insertAdjacentHTML("beforeend", html);

        // El id y el día se releen del DOM recién insertado: es la única
        // fuente que no se puede desincronizar de lo que hay en pantalla.
        var ultimas = chat.querySelectorAll("[data-id]");
        if (ultimas.length) {
          var ultima = ultimas[ultimas.length - 1];
          chat.dataset.ultimoId = ultima.getAttribute("data-id");
          chat.dataset.dia = ultima.getAttribute("data-dia") || chat.dataset.dia;
        }

        if (abajo || chat.dataset.forzarAbajo === "1") {
          desplazador.scrollTop = desplazador.scrollHeight;
          delete chat.dataset.forzarAbajo;
          ocultarAvisoNuevos(desplazador);
        } else {
          var nuevos = Math.max(1, chat.querySelectorAll("[data-id]").length - antes);
          mostrarAvisoNuevos(desplazador, nuevos);
        }
      })
      .catch(function () { /* lo reintenta el flujo o el respaldo */ })
      .then(function () {
        delete chat.dataset.enVuelo;
        if (chat.dataset.colaPendiente === "1") {
          delete chat.dataset.colaPendiente;
          estirarChat(chat);
        }
      });
  }

  function ocultarAvisoNuevos(desplazador) {
    var boton = desplazador && desplazador.querySelector("[data-mensajes-nuevos]");
    if (!boton) return;
    boton.hidden = true;
    boton.dataset.total = "0";
  }

  function mostrarAvisoNuevos(desplazador, cantidad) {
    var boton = desplazador && desplazador.querySelector("[data-mensajes-nuevos]");
    if (!boton) return;
    var total = Number(boton.dataset.total || 0) + Number(cantidad || 0);
    boton.dataset.total = String(total);
    boton.textContent = total + (total === 1 ? " mensaje nuevo" : " mensajes nuevos");
    boton.hidden = false;
  }

  function estirarChats() {
    document.querySelectorAll("[data-cola]").forEach(function (chat) {
      estirarChat(chat);
    });
  }

  // Puesta al día completa. Importa que incluya las colas del chat: durante
  // una desconexión no llega ningún tema que las dispare y los fragmentos
  // normales por sí solos no añaden las burbujas nuevas.
  function ponerAlDia() {
    estirarChats();
    actualizarListasConversaciones();
    vivos(null).forEach(refrescar);
  }

  function refrescarTema(tema) {
    // Con la pestaña de fondo no se pide nada: se pone al día al volver.
    if (document.hidden) {
      pendienteAlVolver = true;
      return;
    }
    if (tema === "conversaciones") {
      estirarChats();
      actualizarListasConversaciones();
    }
    if (tema === "bloqueos") refrescarHilosActivos();
    vivos(tema).forEach(refrescar);
  }

  function activarRespaldo() {
    if (respaldo) return;
    respaldo = setInterval(function () {
      if (document.hidden) return;
      ponerAlDia();
    }, CADA_RESPALDO);
  }

  function abrirFlujo() {
    if (flujo || respaldo || !window.EventSource) return;

    flujo = new EventSource("/eventos");

    flujo.onopen = function () {
      fallos = 0;
      // EventSource se reconecta sin crear otro objeto. El servidor empieza
      // entonces con una foto nueva como referencia y no reenvía lo ocurrido
      // durante el corte; por eso hay que pedir explícitamente lo que falte.
      if (flujoAbiertoAlgunaVez) ponerAlDia();
      flujoAbiertoAlgunaVez = true;
    };

    flujo.onmessage = function (e) {
      (e.data || "").split(",").forEach(function (tema) {
        if (tema) refrescarTema(tema);
      });
    };

    flujo.onerror = function () {
      // EventSource reconecta solo; solo hay que contar los intentos para saber
      // cuándo dejar de insistir y encender el respaldo.
      if (flujo && flujo.readyState === EventSource.CLOSED) {
        flujo = null;
      }
      if (++fallos >= RESPALDO_TRAS) {
        cerrarFlujo();
        activarRespaldo();
      }
    };
  }

  function cerrarFlujo() {
    if (!flujo) return;
    flujo.close();
    flujo = null;
  }

  /* El flujo lo abre el CONTENIDO de la página, no el armazón.

     `[data-secundario]` (el menú lateral) se actualiza si hay flujo, pero no lo
     abre: está en todas las pantallas, y dejándolo abrir se llevaba una de las
     seis conexiones que Chrome permite por origen en HTTP/1.1 en CADA página,
     incluidas las de catálogo que no tienen nada que refrescar. Al sexto cambio
     de pestaña el navegador se quedaba sin ranuras y todo se quedaba pensando. */
  function necesitanFlujo() {
    return document.querySelectorAll(
      "[data-refrescar]:not([data-secundario]), [data-cola], [data-conv-inicio]"
    ).length;
  }

  if (necesitanFlujo()) {
    abrirFlujo();

    /* Al salir de la página se suelta la conexión a mano. El navegador acabaría
       haciéndolo, pero no siempre a tiempo: la petición de la página siguiente
       sale ANTES de que se descarte la actual, así que una ranura que se libera
       tarde es una ranura que a la siguiente le falta. `pagehide` es el evento
       que sí se dispara al navegar (y también al volver atrás). */
    window.addEventListener("pagehide", cerrarFlujo);

    /* Con la pestaña de fondo se suelta la conexión al minuto. No es por
       ahorrar servidor —una conexión dormida no cuesta— sino por el navegador:
       en HTTP/1.1 solo permite seis conexiones por origen y cada EventSource
       ocupa una. Con siete pestañas del panel abiertas, la séptima se quedaba
       esperando para siempre. */
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) {
        temporizadorCierre = setTimeout(cerrarFlujo, CIERRA_OCULTA);
        return;
      }
      clearTimeout(temporizadorCierre);
      abrirFlujo();
      // Puesta al día: mientras no mirabas pudo cambiar cualquier cosa.
      if (pendienteAlVolver || !flujo) {
        pendienteAlVolver = false;
        ponerAlDia();
      }
    });
  }

  /* 2. Menú lateral.
        En escritorio se pliega (y la preferencia se recuerda); en pantalla
        estrecha se abre sobre el contenido con un velo que lo cierra. */
  // El ancho sale del CSS (`--bp-lateral`), no de un número escrito aquí: la
  // media query y esta comprobación tienen que ser el MISMO valor, y estando en
  // dos archivos se quedaba uno sin actualizar.
  var anchoLateral = getComputedStyle(document.documentElement)
    .getPropertyValue("--bp-lateral").trim() || "900px";
  var ESTRECHO = "(max-width: " + anchoLateral + ")";
  var velo = document.querySelector(".velo");

  function esEstrecho() {
    return window.matchMedia(ESTRECHO).matches;
  }

  function pintarVelo() {
    if (velo) velo.hidden = !document.body.classList.contains("lateral-abierto");
  }

  if (localStorage.getItem("lateral") === "plegado") {
    document.body.classList.add("lateral-plegado");
  }

  document.addEventListener("click", function (e) {
    var disparador = e.target.closest && e.target.closest("[data-alternar='lateral']");
    if (!disparador) return;

    if (esEstrecho()) {
      document.body.classList.toggle("lateral-abierto");
      pintarVelo();
      return;
    }

    var plegado = document.body.classList.toggle("lateral-plegado");
    localStorage.setItem("lateral", plegado ? "plegado" : "abierto");
  });

  // Al pasar a escritorio se descarta el estado de móvil: si no, el velo
  // quedaría tapando el panel al girar el teléfono o ensanchar la ventana.
  window.matchMedia(ESTRECHO).addEventListener("change", function () {
    document.body.classList.remove("lateral-abierto");
    pintarVelo();
  });

  /* 3. Menú de cuenta (arriba a la derecha). */
  document.addEventListener("click", function (e) {
    var contenedor = document.querySelector("[data-menu]");
    if (!contenedor) return;

    var menu = contenedor.querySelector(".menu");
    var abre = e.target.closest && e.target.closest("[data-abre-menu]");

    if (abre && contenedor.contains(abre)) {
      menu.hidden = !menu.hidden;
      return;
    }
    // Cualquier clic fuera lo cierra.
    if (!menu.hidden && !menu.contains(e.target)) menu.hidden = true;
  });

  /* 4. Copiar al portapapeles.
        <span class="credencial" data-copiar>…</span>: un clic copia su texto.
        Es para las URLs de webhook, que hay que pegar en otra aplicación y son
        demasiado largas para seleccionarlas a mano sin equivocarse. */
  document.addEventListener("click", function (e) {
    var nodo = e.target.closest && e.target.closest("[data-copiar]");
    if (!nodo || !navigator.clipboard) return;

    navigator.clipboard.writeText(nodo.textContent.trim()).then(function () {
      var previo = nodo.getAttribute("data-etiqueta") || "";
      nodo.setAttribute("data-etiqueta", "copiado");
      setTimeout(function () {
        if (previo) nodo.setAttribute("data-etiqueta", previo);
        else nodo.removeAttribute("data-etiqueta");
      }, 1500);
    });
  });

  /* 5. Avisos flotantes (abajo a la derecha).
        Los de éxito se van solos a los 4 s; los errores esperan a que los
        cierres, porque un error que desaparece sin que lo leas no sirve. */
  document.querySelectorAll(".toast[data-auto]").forEach(function (toast) {
    setTimeout(function () { toast.remove(); }, 4000);
  });

  document.addEventListener("click", function (e) {
    var cerrar = e.target.closest && e.target.closest(".toast .cerrar");
    if (cerrar) cerrar.closest(".toast").remove();
  });

  /* 6. Ventanas flotantes.
        <button data-abre="config">…</button> abre <dialog id="config">.
        Se usa el <dialog> nativo: trae el foco atrapado, el fondo inerte y el
        cierre con Escape sin escribir nada de eso a mano.

        Los formularios que cambian algo irreversible (regenerar el webhook,
        eliminar) viven dentro de su propio <dialog> de confirmación, así que
        no hace falta un confirm() aparte. */
  function abrirDesde(elemento, objetivo) {
    // Un control PROPIO dentro de algo que abre ventana manda sobre él. Pasa en
    // las bandas del conocimiento: la banda entera abre la edición, pero lleva
    // dentro el visto de activar/desactivar. Sin esto, pulsar el visto enviaría
    // su formulario Y abriría la ventana encima.
    var control = elemento.closest("button, a, input, label, select, textarea");
    if (control && control !== objetivo && objetivo.contains(control)) return false;

    var destino = document.getElementById(objetivo.getAttribute("data-abre"));
    if (!destino || !destino.showModal) return false;
    destino.showModal();
    return true;
  }

  document.addEventListener("click", function (e) {
    var abre = e.target.closest && e.target.closest("[data-abre]");
    if (abre && abrirDesde(e.target, abre)) return;

    var cierra = e.target.closest && e.target.closest("[data-cierra]");
    if (cierra) {
      var dialogo = cierra.closest("dialog");
      if (dialogo) dialogo.close();
    }
  });

  // Lo que se pulsa con el ratón se abre también con el teclado. Un <article>
  // que abre una ventana no es un botón para el navegador, así que Enter y
  // Espacio hay que atenderlos a mano o la página no se puede usar sin ratón.
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" && e.key !== " ") return;
    var abre = e.target.closest && e.target.closest("[data-abre][tabindex]");
    if (!abre || abre !== e.target) return;
    e.preventDefault();
    abrirDesde(e.target, abre);
  });

  /* 7b. Ventanas que se abren solas al cargar.
        <dialog data-abrir-al-cargar>: tras guardar, el servidor redirige aquí y
        marca la ventana en la que estabas. Sin esto, cada guardado te devolvía
        a la lista y había que volver a buscar el mensaje y abrir sus dos
        ventanas otra vez.

        Se abren de la más externa a la más interna (por orden en el documento),
        que es como quedan apiladas: al cerrar la de arriba aparece la de
        debajo, igual que si las hubieras abierto tú. */
  document.querySelectorAll("dialog[data-abrir-al-cargar]").forEach(function (dialogo) {
    if (dialogo.showModal) dialogo.showModal();
  });

  /* 7c. Un switch que enseña u oculta lo que depende de él.
        <input type="checkbox" data-revela="media-3"> muestra u oculta #media-3.
        Es solo presentación: quien decide de verdad si hay adjunto es el valor
        del switch al enviar el formulario, así que si este archivo no carga se
        ven todos los campos y el formulario funciona igual. */
  document.querySelectorAll("[data-revela]").forEach(function (interruptor) {
    var destino = document.getElementById(interruptor.getAttribute("data-revela"));
    if (!destino) return;

    interruptor.addEventListener("change", function () {
      destino.hidden = !interruptor.checked;
    });
  });

  /* 7e. Un desplegable que filtra a otro.
        <select data-filtra="referencia_id"> deja visibles en #referencia_id solo
        las <option data-grupo="..."> de la categoría elegida.

        Se mandan las tres listas de una vez y se filtran aquí en vez de recargar
        la página al cambiar de categoría: recargar perdería la lista de números
        que ya se había pegado en el formulario. */
  document.querySelectorAll("[data-filtra]").forEach(function (maestro) {
    var destino = document.getElementById(maestro.getAttribute("data-filtra"));
    if (!destino) return;

    function filtrar() {
      var grupo = maestro.value;
      var primera = -1;
      Array.prototype.forEach.call(destino.options, function (opcion, i) {
        var suya = opcion.getAttribute("data-grupo") === grupo;
        opcion.hidden = !suya;
        if (suya && primera < 0 && !opcion.disabled) primera = i;
      });

      var actual = destino.selectedOptions[0];
      if (actual && !actual.hidden && !actual.disabled) return;

      // Se mueve por ÍNDICE, nunca por `value`. Los tres orígenes son tablas
      // distintas y sus ids se repiten: el mensaje 1 y la palabra clave 1
      // conviven en esta lista. Asignando por valor, el desplegable se posaba en
      // la primera opción con ese id —la de otra categoría— y la pantalla
      // enseñaba un nombre mientras se enviaba otro.
      destino.selectedIndex = primera;
    }

    maestro.addEventListener("change", filtrar);
    filtrar();
  });

  /* 7f. Cargar un fragmento dentro de un elemento al pulsar.
        <button data-carga="/ruta" data-carga-en="id-destino">

        Lo usan las sesiones de envío: el detalle de una tanda (qué número falló
        y por qué) se pide al ABRIR su ventana, no al pintar la lista. Con veinte
        sesiones serían veinte consultas en cada refresco de las barras. */
  document.addEventListener("click", function (e) {
    var disparador = e.target.closest && e.target.closest("[data-carga]");
    if (!disparador) return;
    var destino = document.getElementById(disparador.getAttribute("data-carga-en"));
    if (!destino) return;

    // Si es un enlace (el «ver solo los que fallaron» de dentro de la ventana),
    // se carga en su sitio en vez de navegar fuera.
    e.preventDefault();
    destino.innerHTML = '<p class="sub">Cargando…</p>';
    fetch(disparador.getAttribute("data-carga"), { headers: { "X-Fragmento": "1" } })
      .then(function (r) { return r.ok ? r.text() : null; })
      .then(function (html) {
        destino.innerHTML = html === null ? '<p class="vacio">No se pudo cargar.</p>' : html;
      })
      .catch(function () {
        destino.innerHTML = '<p class="vacio">No se pudo cargar.</p>';
      });
  });

  /* 7d. Contador de caracteres.
        <textarea data-contador="2000"> pinta debajo cuántos lleva y se pone en
        rojo al pasarse. El límite se aplica igual en el servidor: esto solo
        evita escribir de más para enterarte al guardar. */
  document.querySelectorAll("[data-contador]").forEach(function (campo) {
    var limite = parseInt(campo.getAttribute("data-contador"), 10);
    var salida = document.createElement("p");
    salida.className = "contador";
    campo.insertAdjacentElement("afterend", salida);

    function pintar() {
      salida.textContent = campo.value.trim().length + " / " + limite;
      salida.classList.toggle("pasado", campo.value.trim().length > limite);
    }

    campo.addEventListener("input", pintar);
    pintar();
  });

  // Un clic en el fondo oscuro (fuera del contenido) también cierra. Se escucha
  // en el documento y no ventana por ventana porque algunas se inyectan después
  // (la confirmación de borrar vive dentro del hilo que se carga por fetch), y
  // esas nunca habrían pasado por un bucle hecho al cargar la página.
  document.addEventListener("click", function (e) {
    if (e.target.tagName === "DIALOG") e.target.close();
  });

  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    var menu = document.querySelector("[data-menu] .menu");
    if (menu) menu.hidden = true;
    document.body.classList.remove("lateral-abierto");
    pintarVelo();
  });

  /* 7. Categorías dentro de una ventana flotante.
        <nav> con <button data-pestana="cfg-general"> y secciones [data-panel]
        con ese id. Sin esto, la configuración de un cliente sería un formulario
        interminable en una sola columna.

        Es solo mostrar y ocultar: cada sección es un formulario normal que
        funciona por su cuenta, así que si este archivo no carga, se ven todas
        seguidas y se pueden usar igual. */
  document.addEventListener("click", function (e) {
    var boton = e.target.closest && e.target.closest("[data-pestana]");
    if (!boton) return;
    var contenedor = boton.closest("[data-pestanas]");
    if (!contenedor) return;

    contenedor.querySelectorAll("[data-pestana]").forEach(function (otro) {
      otro.classList.toggle("activo", otro === boton);
    });
    var destino = boton.getAttribute("data-pestana");
    contenedor.querySelectorAll("[data-panel]").forEach(function (panel) {
      panel.hidden = panel.id !== destino;
    });
  });

  /* 8. Visor de conversaciones del negocio (las dos columnas del <dialog>).

        Todo se pide al servidor como fragmento y se inyecta: la lista, el
        filtro por ID y el chat. Así abrir un chat no recarga el perfil ni
        cierra la ventana, que es el motivo de que sea flotante.

        No hay estado ni plantillas aquí: el HTML lo arma Jinja, igual que en la
        página completa. Este bloque solo decide QUÉ pedir y DÓNDE ponerlo. */
  function traerA(url, destino, opciones) {
    if (!destino) return;
    fetch(url, { headers: { "X-Fragmento": "1" } })
      .then(function (r) { return r.ok ? r.text() : null; })
      .then(function (html) {
        if (html === null) {
          destino.innerHTML = '<p class="vacio">No se pudo cargar.</p>';
          return;
        }
        destino.innerHTML = html;
        var desplazador = destino.querySelector("[data-mensajes-scroll]");
        if (desplazador && (!opciones || opciones.alFinal !== false)) {
          requestAnimationFrame(function () {
            desplazador.scrollTop = desplazador.scrollHeight;
            ocultarAvisoNuevos(desplazador);
          });
        }
      })
      .catch(function () {
        destino.innerHTML = '<p class="vacio">No se pudo cargar.</p>';
      });
  }

  function actualizarListaConversaciones(contenedor, consulta) {
    if (!contenedor) return;
    if (contenedor.tagName === "DIALOG" && !contenedor.open) return;
    var panel = contenedor.querySelector("[data-conv-panel]");
    var base = contenedor.getAttribute("data-conv-inicio");
    if (!panel || !base) return;

    if (panel.dataset.enVuelo === "1") {
      // Un chat puede producir varios avisos seguidos. El último no se pierde
      // aunque la consulta anterior ya hubiera salido hacia Postgres.
      panel.dataset.listaPendiente = "1";
      return;
    }

    if (typeof consulta !== "string") {
      var campo = panel.querySelector("[data-conv-buscar] input[name=q]");
      consulta = campo ? campo.value : "";
    }

    panel.dataset.enVuelo = "1";
    var listaAnterior = panel.querySelector("[data-conv-lista-scroll]");
    var desplazado = listaAnterior ? listaAnterior.scrollTop : 0;
    var activa = contenedor.dataset.convActiva || "";
    var url = base + (consulta ? "?q=" + encodeURIComponent(consulta) : "");

    fetch(url, { headers: { "X-Fragmento": "1" } })
      .then(function (r) { return r.ok ? r.text() : null; })
      .then(function (html) {
        if (html === null) {
          panel.innerHTML = '<p class="vacio">No se pudo cargar.</p>';
          return;
        }
        panel.innerHTML = html;
        var listaNueva = panel.querySelector("[data-conv-lista-scroll]");
        if (listaNueva) listaNueva.scrollTop = desplazado;

        // Repintar la lista no debe borrar qué conversación está abierta.
        panel.querySelectorAll("[data-conv]").forEach(function (item) {
          item.classList.toggle(
            "activo",
            (item.getAttribute("data-conv-clave") || item.getAttribute("data-conv")) === activa
          );
        });
        if (activa && !panel.querySelector('[data-conv-clave="' + CSS.escape(activa) + '"]')) {
          contenedor.dataset.convActiva = "";
          contenedor.querySelector("[data-conv-detalle]").innerHTML =
            '<p class="vacio">La conversación ya no está disponible.</p>';
        }
      })
      .catch(function () {
        panel.innerHTML = '<p class="mensaje error">No se pudo cargar. <button type="button" class="mini" data-reintenta-conversaciones>Reintentar</button></p>';
      })
      .then(function () {
        delete panel.dataset.enVuelo;
        if (panel.dataset.listaPendiente === "1") {
          delete panel.dataset.listaPendiente;
          actualizarListaConversaciones(contenedor);
        }
      });
  }

  function actualizarListasConversaciones() {
    // Solo se consultan ventanas abiertas. Esto conserva la carga diferida y
    // hace que un chat nuevo aparezca (o suba al primer lugar) en tiempo real.
    document.querySelectorAll("[data-conv-inicio]").forEach(function (contenedor) {
      if (contenedor.tagName !== "DIALOG" || contenedor.open) {
        actualizarListaConversaciones(contenedor);
      }
    });
  }

  function refrescarHilosActivos() {
    document.querySelectorAll("[data-conv-inicio]").forEach(function (contenedor) {
      var activa = contenedor.dataset.convActiva || "";
      if (!activa) return;
      var item = contenedor.querySelector('[data-conv-clave="' + CSS.escape(activa) + '"]');
      var partes = activa.split(":");
      var url = item ? item.getAttribute("data-conv") :
        "/conversaciones/" + encodeURIComponent(partes.shift()) + "/" +
        encodeURIComponent(partes.join(":")) + "?fragmento=1";
      traerA(url, contenedor.querySelector("[data-conv-detalle]"));
    });
  }

  // La lista se carga al abrir la ventana, no al cargar la página: si el
  // administrador nunca la abre, no se consulta la base. Se vuelve a pedir en
  // cada apertura para recuperar cualquier cambio ocurrido mientras estaba
  // cerrada.
  document.querySelectorAll("[data-conv-inicio]").forEach(function (dialogo) {
    if (dialogo.tagName !== "DIALOG") {
      actualizarListaConversaciones(dialogo);
      return;
    }
    var observador = function () {
      if (!dialogo.open) return;
      actualizarListaConversaciones(dialogo);
    };
    // `showModal()` no dispara ningún evento propio, así que se observa el
    // atributo `open`, que es lo que sí cambia.
    new MutationObserver(observador).observe(dialogo, { attributes: true, attributeFilter: ["open"] });
  });

  document.addEventListener("click", function (e) {
    var item = e.target.closest && e.target.closest("[data-conv]");
    if (!item) return;
    var dialogo = item.closest("[data-conv-inicio]");
    if (!dialogo) return;
    dialogo.querySelectorAll(".conv-item.activo").forEach(function (otro) {
      otro.classList.remove("activo");
    });
    item.classList.add("activo");
    dialogo.dataset.convActiva = item.getAttribute("data-conv-clave") || item.getAttribute("data-conv");
    dialogo.classList.add("chat-abierto");
    traerA(item.getAttribute("data-conv"), dialogo.querySelector("[data-conv-detalle]"));
  });

  document.addEventListener("click", function (e) {
    var volver = e.target.closest && e.target.closest("[data-conv-volver]");
    if (!volver) return;
    var contenedor = volver.closest("[data-conv-inicio]");
    if (contenedor) contenedor.classList.remove("chat-abierto");
  });

  document.addEventListener("click", function (e) {
    var boton = e.target.closest && e.target.closest("[data-mensajes-nuevos]");
    if (!boton) return;
    var desplazador = boton.closest("[data-mensajes-scroll]");
    desplazador.scrollTop = desplazador.scrollHeight;
    ocultarAvisoNuevos(desplazador);
  });

  document.addEventListener("scroll", function (e) {
    var desplazador = e.target.closest && e.target.closest("[data-mensajes-scroll]");
    if (!desplazador) return;
    if (desplazador.scrollHeight - desplazador.scrollTop - desplazador.clientHeight < 80) {
      ocultarAvisoNuevos(desplazador);
    }
  }, true);

  // El historial antiguo se antepone sin perder el mensaje que estaba arriba.
  document.addEventListener("click", function (e) {
    var boton = e.target.closest && e.target.closest("[data-mensajes-anteriores]");
    if (!boton || boton.dataset.enVuelo === "1") return;
    boton.dataset.enVuelo = "1";
    var chat = boton.closest(".chat");
    var desplazador = chat && chat.closest("[data-mensajes-scroll]");
    var alturaAnterior = desplazador ? desplazador.scrollHeight : 0;
    fetch(boton.getAttribute("data-mensajes-anteriores"), { headers: { "X-Fragmento": "1" } })
      .then(function (r) { return r.ok ? r.text() : Promise.reject(); })
      .then(function (html) {
        var temporal = document.createElement("div");
        temporal.innerHTML = html;
        var chatNuevo = temporal.querySelector(".chat");
        if (!chatNuevo) throw new Error();
        var siguienteBoton = chatNuevo.querySelector("[data-mensajes-anteriores]");
        if (siguienteBoton) siguienteBoton.remove();
        boton.remove();
        var fragmento = document.createDocumentFragment();
        Array.from(chatNuevo.childNodes).forEach(function (nodo) {
          fragmento.appendChild(nodo);
        });
        chat.insertBefore(fragmento, chat.firstChild);
        if (siguienteBoton) chat.insertBefore(siguienteBoton, chat.firstChild);
        if (desplazador) desplazador.scrollTop += desplazador.scrollHeight - alturaAnterior;
      })
      .catch(function () { boton.textContent = "No se pudo cargar. Reintentar"; })
      .then(function () { delete boton.dataset.enVuelo; });
  });

  document.addEventListener("submit", function (e) {
    var form = e.target.closest && e.target.closest("[data-conv-buscar]");
    if (!form) return;
    e.preventDefault();
    var q = (form.querySelector("input[name=q]") || {}).value || "";
    actualizarListaConversaciones(form.closest("[data-conv-inicio]"), q);
  });

  document.addEventListener("click", function (e) {
    var boton = e.target.closest && e.target.closest("[data-reintenta-conversaciones]");
    if (boton) actualizarListaConversaciones(boton.closest("[data-conv-inicio]"));
  });

  // Bloquear y eliminar desde el hilo no recarga la página. Tras un bloqueo se
  // vuelve a pedir el hilo; tras borrar se limpian las dos columnas juntas.
  document.addEventListener("submit", function (e) {
    var form = e.target.closest && e.target.closest("[data-conv-accion]");
    if (!form) return;
    e.preventDefault();
    var contenedor = form.closest("[data-conv-inicio]");
    if (!contenedor || form.dataset.enVuelo === "1") return;
    form.dataset.enVuelo = "1";
    fetch(form.action, {
      method: "POST",
      body: new FormData(form),
      headers: { "X-Fragmento": "1" }
    })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, json: j }; }); })
      .then(function (resultado) {
        if (!resultado.ok && !resultado.json.eliminada) throw new Error(resultado.json.error || "No se pudo guardar");
        if (form.getAttribute("data-conv-accion") === "eliminar") {
          contenedor.dataset.convActiva = "";
          contenedor.querySelector("[data-conv-detalle]").innerHTML =
            '<p class="vacio">La conversación fue eliminada.</p>';
          actualizarListaConversaciones(contenedor);
          return;
        }
        var activa = contenedor.dataset.convActiva;
        var item = activa && contenedor.querySelector('[data-conv-clave="' + CSS.escape(activa) + '"]');
        if (item) traerA(item.getAttribute("data-conv"), contenedor.querySelector("[data-conv-detalle]"));
      })
      .catch(function () {
        form.insertAdjacentHTML("beforebegin", '<p class="mensaje error">No se pudo guardar. Inténtalo otra vez.</p>');
      })
      .then(function () { delete form.dataset.enVuelo; });
  });

  // Responder desde el panel conserva el borrador si WasenderAPI falla. En
  // éxito, la cola normal añade la burbuja sin repintar ni perder el scroll.
  document.addEventListener("submit", function (e) {
    var form = e.target.closest && e.target.closest("[data-conv-responder]");
    if (!form) return;
    e.preventDefault();
    if (form.dataset.enVuelo === "1") return;
    form.dataset.enVuelo = "1";
    form.querySelectorAll(".mensaje.error").forEach(function (n) { n.remove(); });

    fetch(form.action, {
      method: "POST",
      body: new FormData(form),
      headers: { "X-Fragmento": "1" }
    })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, json: j }; }); })
      .then(function (resultado) {
        if (!resultado.ok) throw new Error(resultado.json.error || "No se pudo enviar");
        form.querySelector("textarea[name=texto]").value = "";
        var hilo = form.closest("[data-estructura-chat]");
        var cabecera = hilo && hilo.querySelector(".cabecera-chat");
        if (cabecera && !cabecera.querySelector("[data-pausa-ia]")) {
          cabecera.insertAdjacentHTML("afterbegin", '<span class="pastilla alerta" data-pausa-ia>IA pausada durante 12 días</span>');
        }
        var chat = hilo && hilo.querySelector("[data-cola]");
        if (chat) {
          chat.dataset.forzarAbajo = "1";
          estirarChat(chat);
        }
        actualizarListasConversaciones();
      })
      .catch(function (error) {
        form.insertAdjacentHTML(
          "afterbegin",
          '<p class="mensaje error">' + String(error.message || "No se pudo enviar") + '</p>'
        );
      })
      .then(function () { delete form.dataset.enVuelo; });
  });

  // Configuración del proyecto: no viaja en cada página; se carga al abrir y
  // vuelve a pedir solo su fragmento después de guardar o buscar.
  function cargarConfiguracion(dialogo, url) {
    var destino = dialogo && dialogo.querySelector("[data-carga-destino]");
    if (!destino || dialogo.dataset.enVuelo === "1") return;
    dialogo.dataset.enVuelo = "1";
    destino.setAttribute("aria-busy", "true");
    fetch(url || dialogo.getAttribute("data-carga-dialogo"), { headers: { "X-Fragmento": "1" } })
      .then(function (r) { return r.ok ? r.text() : Promise.reject(); })
      .then(function (html) { destino.innerHTML = html; })
      .catch(function () { destino.innerHTML = '<p class="mensaje error">No se pudo cargar. <button type="button" class="mini" data-reintenta-configuracion>Reintentar</button></p>'; })
      .then(function () {
        destino.removeAttribute("aria-busy");
        delete dialogo.dataset.enVuelo;
      });
  }

  document.querySelectorAll("[data-carga-dialogo]").forEach(function (dialogo) {
    new MutationObserver(function () {
      if (dialogo.open) cargarConfiguracion(dialogo);
    }).observe(dialogo, { attributes: true, attributeFilter: ["open"] });
  });

  document.addEventListener("click", function (e) {
    var boton = e.target.closest && e.target.closest("[data-reintenta-configuracion]");
    if (boton) cargarConfiguracion(boton.closest("[data-carga-dialogo]"));
  });

  document.addEventListener("submit", function (e) {
    var form = e.target.closest && e.target.closest("[data-config-proyecto-form], [data-config-proyecto-buscar]");
    if (!form) return;
    e.preventDefault();
    var dialogo = form.closest("[data-carga-dialogo]");
    if (!dialogo) return;
    if (form.hasAttribute("data-config-proyecto-buscar")) {
      var q = (form.querySelector("[name=q]") || {}).value || "";
      cargarConfiguracion(dialogo, form.action + (q ? "?q=" + encodeURIComponent(q) : ""));
      return;
    }
    fetch(form.action, {
      method: "POST", body: new FormData(form), headers: { "X-Fragmento": "1" }
    }).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok) throw new Error(j.error || "No se pudo guardar");
        var q = (dialogo.querySelector("[data-config-proyecto-buscar] [name=q]") || {}).value || "";
        var base = dialogo.getAttribute("data-carga-dialogo");
        cargarConfiguracion(dialogo, base + (q ? "?q=" + encodeURIComponent(q) : ""));
        refrescarHilosActivos();
      });
    }).catch(function (error) {
      form.insertAdjacentHTML("beforebegin", '<p class="mensaje error">' + error.message + '</p>');
    });
  });
})();
