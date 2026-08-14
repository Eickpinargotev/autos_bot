-- `users_blocked` deja de ser solo del bot: ahora el panel la lee y la limpia.
--
-- La tabla la sigue creando el bot al arrancar (`postgres_conn._crear_tablas_del_bot`),
-- porque tiene que poder levantarse contra una base limpia aunque el dashboard
-- todavía no haya corrido. Pero desde que el panel muestra los bloqueos y
-- permite levantarlos, el orden de arranque dejó de dar igual: si el dashboard
-- sube primero, la pantalla de bloqueos reventaría contra una tabla que no
-- existe.
--
-- Por eso se declara aquí también, con `IF NOT EXISTS` y EXACTAMENTE la misma
-- forma: el que llegue primero la crea y el otro no hace nada. Si algún día
-- cambia una columna, hay que cambiarla en los dos sitios.
--
-- Por qué hacía falta la pantalla: los bloqueos se ponen SOLOS (el dueño
-- responde desde su teléfono y el chat queda 12 días en silencio, alguien entra
-- al grupo, un `/block`) y hasta ahora la única forma de levantar uno era entrar
-- a la base a mano.

CREATE TABLE IF NOT EXISTS users_blocked (
    user_id     VARCHAR(50) PRIMARY KEY,
    reason      TEXT,
    blocked_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at  TIMESTAMP
);
