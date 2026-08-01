/* Siete comportamientos, nada más: refresco de fragmentos, edición en línea,
   dos de navegación (menú lateral y menú de cuenta), copiar al portapapeles,
   avisos flotantes y ventanas <dialog>. Todo lo demás son formularios HTML
   normales con POST y redirección, que funcionan aunque este archivo no
   cargue. */

(function () {
  "use strict";

  /* 1. Refresco periódico de un fragmento.
        <div data-refrescar="/factura/totales" data-cada="5000">…</div>
        Sustituye su contenido por el HTML que devuelva la ruta. */
  document.querySelectorAll("[data-refrescar]").forEach(function (nodo) {
    var url = nodo.getAttribute("data-refrescar");
    var cada = parseInt(nodo.getAttribute("data-cada") || "5000", 10);
    var enVuelo = false;

    setInterval(function () {
      // Sin pestaña visible no tiene sentido consultar: ahorra trabajo al
      // servidor cuando alguien deja el panel abierto en segundo plano.
      if (enVuelo || document.hidden) return;
      enVuelo = true;
      fetch(url, { headers: { "X-Fragmento": "1" } })
        .then(function (r) { return r.ok ? r.text() : null; })
        .then(function (html) { if (html !== null) nodo.innerHTML = html; })
        .catch(function () { /* un fallo puntual de red no debe romper la página */ })
        .then(function () { enVuelo = false; });
    }, cada);
  });

  /* 2. Edición en línea de celdas.
        Un <textarea data-guardar-en="/ciudades/3/campo" data-campo="mensaje_1">
        guarda al salir del campo, solo si su valor cambió.

        Se escucha con delegación en el documento (y no campo por campo) porque
        al guardar se reemplaza la fila entera: unos listeners atados a los
        elementos viejos se perderían tras la primera edición. */
  document.addEventListener("focusin", function (e) {
    var campo = e.target;
    if (campo.hasAttribute && campo.hasAttribute("data-guardar-en")) {
      campo.dataset.valorPrevio = campo.value;
    }
  });

  document.addEventListener("focusout", function (e) {
    var campo = e.target;
    if (!campo.hasAttribute || !campo.hasAttribute("data-guardar-en")) return;
    if (campo.value === campo.dataset.valorPrevio) return;

    var previo = campo.dataset.valorPrevio;
    var cuerpo = new FormData();
    cuerpo.append("campo", campo.getAttribute("data-campo"));
    cuerpo.append("valor", campo.value);
    cuerpo.append("csrf", document.body.getAttribute("data-csrf") || "");

    campo.disabled = true;

    fetch(campo.getAttribute("data-guardar-en"), { method: "POST", body: cuerpo })
      .then(function (r) {
        if (!r.ok) throw new Error("No se pudo guardar");
        return r.text();
      })
      .then(function (html) {
        // La respuesta es la fila entera repintada: así los avisos de validación
        // (falta el enlace del grupo, etc.) se actualizan solos.
        var fila = campo.closest("tr");
        var temporal = document.createElement("tbody");
        temporal.innerHTML = (html || "").trim();
        var nueva = temporal.querySelector("tr");
        if (fila && nueva) {
          fila.replaceWith(nueva);
          return;
        }
        campo.disabled = false;
        campo.dataset.valorPrevio = campo.value;
      })
      .catch(function () {
        campo.disabled = false;
        // Se devuelve el valor previo: no hay que dar por guardado algo que no lo está.
        campo.value = previo;
        alert("No se pudo guardar el cambio. Revisa tu conexión e inténtalo de nuevo.");
      });
  });

  /* 3. Menú lateral.
        En escritorio se pliega (y la preferencia se recuerda); en pantalla
        estrecha se abre sobre el contenido con un velo que lo cierra. */
  var ESTRECHO = "(max-width: 900px)";
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

  /* 4. Menú de cuenta (arriba a la derecha). */
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

  /* 5. Copiar al portapapeles.
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

  /* 6. Avisos flotantes (abajo a la derecha).
        Los de éxito se van solos a los 4 s; los errores esperan a que los
        cierres, porque un error que desaparece sin que lo leas no sirve. */
  document.querySelectorAll(".toast[data-auto]").forEach(function (toast) {
    setTimeout(function () { toast.remove(); }, 4000);
  });

  document.addEventListener("click", function (e) {
    var cerrar = e.target.closest && e.target.closest(".toast .cerrar");
    if (cerrar) cerrar.closest(".toast").remove();
  });

  /* 7. Ventanas flotantes.
        <button data-abre="config">…</button> abre <dialog id="config">.
        Se usa el <dialog> nativo: trae el foco atrapado, el fondo inerte y el
        cierre con Escape sin escribir nada de eso a mano.

        Los formularios que cambian algo irreversible (regenerar el webhook,
        eliminar) viven dentro de su propio <dialog> de confirmación, así que
        no hace falta un confirm() aparte. */
  document.addEventListener("click", function (e) {
    var abre = e.target.closest && e.target.closest("[data-abre]");
    if (abre) {
      var destino = document.getElementById(abre.getAttribute("data-abre"));
      if (destino && destino.showModal) {
        destino.showModal();
        return;
      }
    }
    var cierra = e.target.closest && e.target.closest("[data-cierra]");
    if (cierra) {
      var dialogo = cierra.closest("dialog");
      if (dialogo) dialogo.close();
    }
  });

  // Un clic en el fondo oscuro (fuera del contenido) también cierra.
  document.querySelectorAll("dialog").forEach(function (dialogo) {
    dialogo.addEventListener("click", function (e) {
      if (e.target === dialogo) dialogo.close();
    });
  });

  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    var menu = document.querySelector("[data-menu] .menu");
    if (menu) menu.hidden = true;
    document.body.classList.remove("lateral-abierto");
    pintarVelo();
  });
})();
