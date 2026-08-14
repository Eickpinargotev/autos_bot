"""Quién está bloqueado y cómo se le levanta el bloqueo.

`users_blocked` es una tabla del BOT (la crea él al arrancar, ver
`postgres_conn._crear_tablas_del_bot`); aquí solo se lee y se borra. Es la única
forma de que un bloqueo se pueda deshacer sin entrar a la base a mano: hasta
ahora se ponían solos —el dueño escribe desde su teléfono y el chat queda 12
días en silencio, alguien entra al grupo, un `/block`— y no había ninguna
pantalla donde verlos ni levantarlos.

Detalles que importan:

* La clave es `canal:id` (`whatsapp:50688888888`). Quedan filas antiguas de
  Telegram guardadas solo con el id, sin canal, así que desbloquear borra las
  dos formas — igual que hace `PostgresUserRepo.unblock_user`.
* `expires_at` viene sin zona: el bot la escribe con la hora de su contenedor,
  que corre en UTC. Por eso se compara contra `NOW() AT TIME ZONE 'UTC'` y no
  contra `NOW()` a secas, que compararía peras con manzanas si algún día el
  servidor deja de estar en UTC.
* Un bloqueo VENCIDO sigue en la tabla hasta que el bot lo pisa (lo borra al
  comprobarlo), así que aquí se distingue del que está en vigor: si no, la
  pantalla acusaría de bloqueada a gente que ya puede escribir.
"""

from typing import Any

from src.db import pool

_SELECT = """
    SELECT b.user_id,
           b.reason,
           b.blocked_at,
           b.expires_at,
           b.expires_at IS NULL AS permanente,
           (b.expires_at IS NOT NULL AND b.expires_at <= (NOW() AT TIME ZONE 'UTC')) AS vencido,
           SPLIT_PART(b.user_id, ':', 1) AS canal_crudo,
           NULLIF(SPLIT_PART(b.user_id, ':', 2), '') AS id_crudo
    FROM users_blocked b
"""


def _presentar(fila: dict[str, Any]) -> dict[str, Any]:
    """Parte `canal:id` en sus dos mitades, tolerando las filas viejas sin canal."""
    if fila.get("id_crudo"):
        fila["canal"] = fila["canal_crudo"]
        fila["client_id"] = fila["id_crudo"]
    else:
        # Fila antigua: solo el id, sin canal. Eran de Telegram.
        fila["canal"] = "telegram"
        fila["client_id"] = fila["canal_crudo"]
    fila["en_vigor"] = not fila["vencido"]
    return fila


def _claves(canal: str, client_id: str) -> list[str]:
    """Las formas con las que ese usuario puede estar guardado.

    Igual que `PostgresUserRepo.is_blocked`: la clave sin canal solo se mira en
    Telegram, que es de donde vienen las filas antiguas. Mirarla también en
    WhatsApp daría por bloqueado a un número que coincidiera con un id viejo de
    Telegram.
    """
    claves = [f"{canal}:{client_id}"]
    if canal == "telegram":
        claves.append(client_id)
    return claves


def listar(busqueda: str = "") -> list[dict[str, Any]]:
    """Los bloqueos, el más reciente primero. `busqueda` filtra por número.

    Se busca por dígitos, como en el visor de conversaciones: '+506 8888-8888' y
    '50688888888' tienen que encontrar lo mismo.
    """
    numero = "".join(c for c in str(busqueda or "") if c.isdigit())
    if busqueda and not numero:
        return []

    where = "WHERE b.user_id LIKE %s" if numero else ""
    params = (f"%{numero}%",) if numero else None
    filas = pool.consultar(f"{_SELECT} {where} ORDER BY b.blocked_at DESC LIMIT 200", params)
    return [_presentar(fila) for fila in filas]


def estado_de(canal: str, client_id: str) -> dict[str, Any] | None:
    """El bloqueo EN VIGOR de una conversación, o None.

    Lo usa la cabecera del chat: es donde se nota que alguien está bloqueado y
    donde tiene sentido levantarlo, sin tener que ir a buscarlo a otra pantalla.
    """
    fila = pool.consultar_uno(f"{_SELECT} WHERE b.user_id = ANY(%s)", (_claves(canal, client_id),))
    if not fila:
        return None
    fila = _presentar(fila)
    return fila if fila["en_vigor"] else None


def desbloquear(canal: str, client_id: str) -> int:
    """Levanta el bloqueo.

    Borra las dos formas de la clave —con canal y sin él— aunque el canal no sea
    Telegram, igual que `PostgresUserRepo.unblock_user`: al desbloquear conviene
    barrer de más, porque dejar una fila huérfana mantendría al usuario mudo sin
    que nada lo explique.
    """
    return pool.ejecutar(
        "DELETE FROM users_blocked WHERE user_id = ANY(%s)",
        ([f"{canal}:{client_id}", client_id],),
    )
