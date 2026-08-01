"""Aplicación de migraciones SQL al arrancar el dashboard.

Las migraciones son archivos `NNN_nombre.sql` en `migrations/`, aplicados en
orden y una sola vez: `schema_migrations` guarda cuáles ya corrieron. Cada
archivo se aplica dentro de UNA transacción, así que una migración a medias no
puede dejar el esquema en un estado intermedio.

El bloqueo de asesoría (`pg_advisory_lock`) evita que dos réplicas del dashboard
arrancando a la vez apliquen la misma migración dos veces.
"""

import hashlib
from pathlib import Path

import psycopg2

from src.db.pool import conexion

DIRECTORIO_MIGRACIONES = Path(__file__).parent / "migrations"

# Número arbitrario pero fijo: identifica este candado entre todos los que
# pudieran pedirse sobre la misma base.
_LOCK_ID = 728_413_905


def _asegurar_tabla_control(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            nombre     VARCHAR(200) PRIMARY KEY,
            checksum   VARCHAR(64)  NOT NULL,
            aplicada_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def migraciones_disponibles() -> list[Path]:
    return sorted(DIRECTORIO_MIGRACIONES.glob("*.sql"))


def aplicar_migraciones() -> list[str]:
    """Aplica las migraciones pendientes. Devuelve los nombres aplicados."""
    aplicadas: list[str] = []

    with conexion(autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(%s)", (_LOCK_ID,))
        try:
            with conn.cursor() as cur:
                _asegurar_tabla_control(cur)
                cur.execute("SELECT nombre, checksum FROM schema_migrations")
                ya_aplicadas = {nombre: checksum for nombre, checksum in cur.fetchall()}

            for archivo in migraciones_disponibles():
                sql = archivo.read_text(encoding="utf-8")
                checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()

                if archivo.name in ya_aplicadas:
                    if ya_aplicadas[archivo.name] != checksum:
                        # Editar una migración ya aplicada deja el esquema real y
                        # el archivo desincronizados en silencio. Se avisa fuerte
                        # pero no se re-aplica: hay que escribir una migración nueva.
                        print(
                            f"AVISO: la migración {archivo.name} cambió después de "
                            "aplicarse. Crea una migración nueva en vez de editarla."
                        )
                    continue

                conn.autocommit = False
                try:
                    with conn.cursor() as cur:
                        cur.execute(sql)
                        cur.execute(
                            "INSERT INTO schema_migrations (nombre, checksum) VALUES (%s, %s)",
                            (archivo.name, checksum),
                        )
                    conn.commit()
                    aplicadas.append(archivo.name)
                    print(f"Migración aplicada: {archivo.name}")
                except psycopg2.Error:
                    conn.rollback()
                    raise
                finally:
                    conn.autocommit = True
        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_ID,))

    return aplicadas


if __name__ == "__main__":
    resultado = aplicar_migraciones()
    print(f"{len(resultado)} migración(es) aplicada(s).")
