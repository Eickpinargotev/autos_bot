-- La pantalla de registros recorre el histórico de cada proyecto de diez en
-- diez, desde el más reciente. El índice único existente resuelve la búsqueda
-- exacta por número; este segundo índice evita ordenar toda la tabla para cada
-- tanda del listado.

CREATE INDEX IF NOT EXISTS idx_keyword_registros_pagina
    ON keyword_registros (proyecto_id, creado_en DESC, id DESC);
